"""Backed-up, transactional conversion of legacy feed-to-sqlite databases."""

import base64
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import sqlite3
from urllib.parse import quote

from .events import Counts, ProgressCallback, emit
from .models import Feed, Snapshot, TextConstruct
from .parsing import canonical_json, entry_from_fields
from .schema import (
    TABLES,
    SchemaError,
    connect,
    create_schema,
    initialize,
    initialized,
    table_names,
)
from .storage import Store


class MigrationError(SchemaError):
    pass


@dataclass(frozen=True)
class MigrationResult:
    migrated: bool = False
    backup_path: Path | None = None
    converted_rows: int = 0


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def legacy_value(value):
    if isinstance(value, bytes):
        return {"$legacy_bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        return {"$legacy_float": repr(value)}
    return value


def _backup_locked_database(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(path.name + ".pre-ledger-" + stamp + ".sqlite3")
    backup.touch(exist_ok=False)
    # The caller holds BEGIN IMMEDIATE but has not changed any pages. A separate
    # read-only connection can copy that stable committed state without backing
    # up a connection that itself owns a write transaction (which would hang).
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise MigrationError("Backup failed SQLite quick_check")
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def _legacy_snapshots(conn, path):
    names = table_names(conn)
    if "feeds" not in names:
        raise MigrationError("Unrecognized database: no legacy feeds table")
    columns = {row[1] for row in conn.execute('PRAGMA table_info("feeds")')}
    if not {"title", "link"}.issubset(columns):
        raise MigrationError("Unrecognized legacy feeds table layout")
    feeds = [dict(row) for row in conn.execute('SELECT * FROM "feeds"')]
    aliases = defaultdict(set)
    for position, feed in enumerate(feeds):
        for key in (feed.get("id"), feed.get("link")):
            if key is not None:
                aliases[str(key)].add(position)
    grouped = defaultdict(list)
    entry_tables = []
    for name in sorted(names - {"feeds"}):
        columns = {
            row[1] for row in conn.execute(f"PRAGMA table_info({identifier(name)})")
        }
        if "feed" not in columns or not ({"id", "link"} & columns):
            continue
        entry_tables.append(name)
        for row_number, row in enumerate(
            conn.execute(f"SELECT * FROM {identifier(name)}")
        ):
            row = dict(row)
            matches = aliases.get(str(row.get("feed")), set())
            if len(matches) != 1:
                raise MigrationError(
                    f"Ambiguous or missing feed association in {name}, row {row_number}"
                )
            grouped[next(iter(matches))].append((name, row_number, row))
    snapshots = []
    for position, raw_feed in enumerate(feeds):
        raw_items = grouped[position]
        feed_id = str(
            raw_feed.get("id") or raw_feed.get("link") or f"legacy-feed:{position}"
        )
        feed = Feed(
            id=feed_id,
            url=raw_feed.get("link"),
            title=TextConstruct(value=str(raw_feed["title"]))
            if raw_feed.get("title") is not None
            else None,
            parser_fields={
                "legacy_row": {
                    key: legacy_value(value) for key, value in raw_feed.items()
                }
            },
        )
        entries = []
        exported_items = []
        for name, row_number, row in raw_items:
            exported = {key: legacy_value(value) for key, value in row.items()}
            exported_items.append(
                {"table": name, "row": row_number, "values": exported}
            )
            fields = {
                key: row[key]
                for key in ("title", "link", "author", "published", "updated")
                if isinstance(row.get(key), str)
            }
            fields["id"] = str(
                row.get("id") or row.get("link") or f"legacy:{name}:{row_number}"
            )
            if isinstance(row.get("description"), str):
                fields["summary"] = row["description"]
                fields["summary_detail"] = {
                    "value": row["description"],
                    "type": "text/html",
                }
            fields["legacy"] = {"table": name, "row": row_number, "values": exported}
            entries.append(entry_from_fields(fields, feed_id))
        blob = canonical_json(
            {
                "format": "legacy-json-v1",
                "feed": feed.parser_fields,
                "items": exported_items,
            }
        ).encode()
        source_hash = hashlib.sha256(blob).hexdigest()
        snapshots.append(
            (
                Snapshot(
                    feed=feed,
                    entries=entries,
                    source_uri="legacy:"
                    + path.resolve().as_uri()
                    + "#feed="
                    + quote(feed_id, safe=""),
                    source_sha256=source_hash,
                    capture_key="legacy:" + source_hash,
                    feedparser_version="legacy-v1",
                    parse_policy="legacy-import-v1",
                    format="legacy-json",
                ),
                blob,
            )
        )
    return snapshots, entry_tables


def prepare_database(
    path: str | Path, *, progress: ProgressCallback | None = None
) -> MigrationResult:
    """Initialize fresh/current databases or migrate legacy data with a backup.

    No legacy table is deleted. Conflicting names are retained under a unique
    __prompthound_legacy_ prefix. All schema/data changes share one transaction.
    """
    path = Path(path)
    conn = connect(path)
    backup = None
    try:
        if initialized(conn) or not table_names(conn):
            initialize(conn)
            return MigrationResult()
        emit(progress, "migrate", "started", source=str(path))
        conn.execute("BEGIN IMMEDIATE")
        # Another writer may have completed migration while we waited for the lock.
        if initialized(conn):
            conn.rollback()
            initialize(conn)
            return MigrationResult()
        snapshots, entry_tables = _legacy_snapshots(conn, path)
        backup = _backup_locked_database(path)
        names = table_names(conn)
        reserved = {
            statement.split("CREATE TABLE ", 1)[1].split(" ", 1)[0]
            for statement in TABLES
        }
        for number, name in enumerate(sorted((names & reserved) | set(entry_tables))):
            renamed = f"__prompthound_legacy_{number}_{name}"
            while renamed in names:
                renamed = "_" + renamed
            conn.execute(
                f"ALTER TABLE {identifier(name)} RENAME TO {identifier(renamed)}"
            )
            names.add(renamed)
        create_schema(conn)
        total = sum(len(snapshot.entries) for snapshot, _ in snapshots)
        completed = 0
        counts = []
        with Store(conn) as store:
            for snapshot, blob in snapshots:
                result = store.ingest(snapshot, blob, progress=progress, commit=False)
                counts.append(result.counts)
                completed += len(snapshot.entries)
                emit(
                    progress,
                    "migrate",
                    "progress",
                    source=str(path),
                    completed=completed,
                    total=total,
                )
        if list(conn.execute("PRAGMA foreign_key_check")):
            raise MigrationError("Migrated data failed foreign-key validation")
        totals = Counts(
            **{
                name: sum(getattr(count, name) for count in counts)
                for name in Counts.__dataclass_fields__
            }
        )
        conn.commit()
    except BaseException as exc:
        conn.rollback()
        emit(
            progress,
            "migrate",
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "rolled_back",
            source=str(path),
            message=str(exc),
        )
        raise
    finally:
        conn.close()
    # Post-commit presentation errors must not claim migration was rolled back.
    emit(progress, "persist", "committed", source=str(path), counts=totals)
    emit(
        progress,
        "migrate",
        "completed",
        source=str(path),
        completed=completed,
        total=total,
        message=f"Original database backed up at {backup}",
    )
    return MigrationResult(True, backup, completed)
