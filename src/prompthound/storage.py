"""Atomic snapshot ingestion into append-only per-item ledgers."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import time

from .events import Counts, ProgressCallback, emit
from .models import Entry, Snapshot
from .parsing import FINGERPRINT_POLICY, canonical_json, fingerprint
from .schema import connect, initialize, initialized


def utc_text(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


@dataclass(frozen=True)
class IngestResult:
    snapshot_id: int
    replayed: bool
    counts: Counts = Counts()


class CaptureConflict(ValueError):
    pass


class Store:
    def __init__(self, path: str | Path | sqlite3.Connection):
        self._owns_connection = not isinstance(path, sqlite3.Connection)
        if isinstance(path, sqlite3.Connection):
            if not initialized(path):
                raise ValueError("External connection must have an initialized schema")
            self.conn = path
            return
        self.conn = connect(path)
        try:
            initialize(self.conn)
        except BaseException:
            self.conn.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if self._owns_connection:
            self.conn.close()

    def ingest(
        self,
        snapshot: Snapshot,
        source_bytes: bytes,
        *,
        progress: ProgressCallback | None = None,
        retries: int = 3,
        commit: bool = True,
    ) -> IngestResult:
        # Migration can append inside its outer transaction without publishing counters early.
        if self.conn.in_transaction == commit:
            raise ValueError("Use commit=False only inside a caller-owned transaction")
        # Freeze caller-owned dictionaries and prepare expensive serialization outside the lock.
        snapshot = Snapshot.model_validate_json(snapshot.model_dump_json())
        if hashlib.sha256(source_bytes).hexdigest() != snapshot.source_sha256:
            raise ValueError("Snapshot checksum does not match source bytes")
        if retries < 1:
            raise ValueError("At least one write attempt is required")
        prepared = []
        for entry in snapshot.entries:
            if (
                entry.feed_id != snapshot.feed.id
                or (entry.parser_fields.get("id") or entry.parser_fields.get("link"))
                != entry.id
            ):
                raise ValueError(
                    "Item identity does not match its feed or parser payload"
                )
            prepared.append(
                (
                    entry,
                    fingerprint(entry, snapshot.feedparser_version),
                    canonical_json(entry.parser_fields),
                    entry.model_dump_json(),
                )
            )
        source = snapshot.source_uri
        emit(progress, "persist", "started", source=source, total=len(prepared))
        for attempt in range(retries):
            try:
                result = self._ingest_once(
                    snapshot, source_bytes, prepared, progress, commit
                )
            except sqlite3.OperationalError as exc:
                if (
                    commit
                    and (getattr(exc, "sqlite_errorcode", 0) & 0xFF)
                    in (
                        sqlite3.SQLITE_BUSY,
                        sqlite3.SQLITE_LOCKED,
                    )
                    and attempt + 1 < retries
                ):
                    time.sleep(0.02 * (attempt + 1))
                    continue
                emit(
                    progress, "persist", "rolled_back", source=source, message=str(exc)
                )
                raise
            except BaseException as exc:
                emit(
                    progress,
                    "persist",
                    "interrupted"
                    if isinstance(exc, KeyboardInterrupt)
                    else "rolled_back",
                    source=source,
                    message=str(exc),
                )
                raise
            else:
                if not commit:
                    return result
                # Outside the rollback handler: a renderer error cannot relabel a committed TX.
                emit(
                    progress,
                    "persist",
                    "replayed" if result.replayed else "committed",
                    source=source,
                    completed=len(prepared),
                    total=len(prepared),
                    counts=None if result.replayed else result.counts,
                )
                return result
        raise AssertionError("Write attempts exhausted")

    def _ingest_once(
        self, snapshot, source_bytes, prepared, progress, commit
    ) -> IngestResult:
        conn = self.conn
        try:
            if commit:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO feeds(publisher_id,url) VALUES (?,?) ON CONFLICT(publisher_id) DO NOTHING",
                (snapshot.feed.id, snapshot.feed.url),
            )
            feed_id = conn.execute(
                "SELECT id FROM feeds WHERE publisher_id=?", (snapshot.feed.id,)
            ).fetchone()[0]
            previous = conn.execute(
                "SELECT source_sha256 FROM snapshots WHERE feed_id=? AND capture_key=? LIMIT 1",
                (feed_id, snapshot.capture_key),
            ).fetchone()
            if previous is not None and previous[0] != snapshot.source_sha256:
                raise CaptureConflict(
                    "Capture ID was already used with different source bytes"
                )
            replay = conn.execute(
                """SELECT id FROM snapshots WHERE feed_id=? AND capture_key=?
                         AND feedparser_version=? AND parse_policy=?""",
                (
                    feed_id,
                    snapshot.capture_key,
                    snapshot.feedparser_version,
                    snapshot.parse_policy,
                ),
            ).fetchone()
            if replay is not None:
                if commit:
                    conn.commit()
                return IngestResult(snapshot_id=replay[0], replayed=True)
            now = datetime.now(timezone.utc).isoformat()
            snapshot_id = conn.execute(
                """INSERT INTO snapshots(
                feed_id,capture_key,source_uri,source_sha256,source_bytes,captured_at,recorded_at,
                feed_json,namespaces_json,feedparser_version,parse_policy,format,encoding,bozo,parse_error)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    feed_id,
                    snapshot.capture_key,
                    snapshot.source_uri,
                    snapshot.source_sha256,
                    source_bytes,
                    utc_text(snapshot.captured_at),
                    now,
                    snapshot.feed.model_dump_json(),
                    canonical_json(snapshot.namespaces),
                    snapshot.feedparser_version,
                    snapshot.parse_policy,
                    snapshot.format,
                    snapshot.encoding,
                    snapshot.bozo,
                    snapshot.parse_error,
                ),
            ).lastrowid
            new_items = revisions = unchanged = blocks = 0
            for position, (entry, item_fingerprint, payload, normalized) in enumerate(
                prepared
            ):
                cursor = conn.execute(
                    "INSERT INTO entries(feed_id,publisher_id) VALUES (?,?) ON CONFLICT(feed_id,publisher_id) DO NOTHING",
                    (feed_id, entry.id),
                )
                new_items += cursor.rowcount
                entry_id = conn.execute(
                    "SELECT id FROM entries WHERE feed_id=? AND publisher_id=?",
                    (feed_id, entry.id),
                ).fetchone()[0]
                latest = conn.execute(
                    "SELECT id,revision_number,fingerprint FROM entry_revisions WHERE entry_id=? ORDER BY revision_number DESC LIMIT 1",
                    (entry_id,),
                ).fetchone()
                if latest is not None and latest["fingerprint"] == item_fingerprint:
                    revision_id = latest["id"]
                    unchanged += 1
                else:
                    revision_id = conn.execute(
                        """INSERT INTO entry_revisions(
                        entry_id,feed_id,revision_number,previous_revision_id,source_snapshot_id,
                        fingerprint,fingerprint_policy,feedparser_version,payload_json,entry_json,title,url,
                        published_at,updated_at,comments_url,comments_feed_url,comment_count,wordpress_post_id,
                        id_is_permalink,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            entry_id,
                            feed_id,
                            latest["revision_number"] + 1 if latest else 1,
                            latest["id"] if latest else None,
                            snapshot_id,
                            item_fingerprint,
                            FINGERPRINT_POLICY,
                            snapshot.feedparser_version,
                            payload,
                            normalized,
                            entry.title.value if entry.title else None,
                            entry.url,
                            utc_text(entry.published),
                            utc_text(entry.updated),
                            entry.comments_url,
                            entry.comments_feed_url,
                            entry.comment_count,
                            entry.wordpress_post_id,
                            entry.id_is_permalink,
                            now,
                        ),
                    ).lastrowid
                    revisions += 1
                    blocks += self._source_children(revision_id, entry)
                conn.execute(
                    "INSERT INTO snapshot_entries(snapshot_id,position,feed_id,entry_id,revision_id) VALUES (?,?,?,?,?)",
                    (snapshot_id, position, feed_id, entry_id, revision_id),
                )
                emit(
                    progress,
                    "persist",
                    "progress",
                    source=snapshot.source_uri,
                    completed=position + 1,
                    total=len(prepared),
                )
            if commit:
                conn.commit()
            return IngestResult(
                snapshot_id=snapshot_id,
                replayed=False,
                counts=Counts(new_items, revisions, len(prepared), unchanged, blocks),
            )
        except BaseException:
            if commit:
                conn.rollback()
            raise

    def _source_children(self, revision_id: int, entry: Entry) -> int:
        blocks = []
        if entry.summary is not None:
            blocks.append(("summary", 0, entry.summary))
        blocks.extend(
            ("content", position, block) for position, block in enumerate(entry.content)
        )
        for role, position, block in blocks:
            self.conn.execute(
                "INSERT INTO content_blocks(revision_id,role,position,type,language,base,value) VALUES (?,?,?,?,?,?,?)",
                (
                    revision_id,
                    role,
                    position,
                    block.type,
                    block.language,
                    block.base,
                    block.value,
                ),
            )
        for position, tag in enumerate(entry.tags):
            self.conn.execute(
                "INSERT INTO revision_tags(revision_id,position,term,scheme,label) VALUES (?,?,?,?,?)",
                (revision_id, position, tag.term, tag.scheme, tag.label),
            )
        return len(blocks)

    def load_entry(self, revision_id: int) -> Entry:
        row = self.conn.execute(
            "SELECT entry_json FROM entry_revisions WHERE id=?", (revision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        return Entry.model_validate_json(row[0])
