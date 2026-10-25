"""Read the frozen RSS sample through the production CLI in a disposable database.

Run with uv run python plans/feed-item-model-2026-10/verify_production.py
--dataset ../../datasets/pluralistic_feed_snapshots. Dataset files are read-only.
"""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from click.testing import CliRunner

from prompthound.cli import cli
from prompthound.models import EntryRevision, Feed, HTMLFeatures, Snapshot
from prompthound.parsing import fingerprint
from prompthound.schema import SCHEMA_VERSION
from prompthound.storage import Store


def require(condition, message):
    if not condition:
        raise ValueError(message)


def counts(conn):
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "feeds",
            "entries",
            "snapshots",
            "entry_revisions",
            "snapshot_entries",
            "content_blocks",
            "content_extractions",
            "revision_tags",
            "latest_entries",
            "entry_history",
            "latest_entry_content",
        )
    }


def verify(dataset):
    manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
    paths = [dataset / file["name"] for file in manifest["files"]]
    for path, file in zip(paths, manifest["files"]):
        require(
            hashlib.sha256(path.read_bytes()).hexdigest() == file["sha256"],
            f"Checksum mismatch: {path}",
        )
    with TemporaryDirectory(prefix="prompthound-acceptance-") as temp:
        db = Path(temp) / "acceptance.db"
        args = ["ingest", "--db", str(db), "--no-progress", *map(str, paths)]
        first = CliRunner(mix_stderr=False).invoke(cli, args)
        require(first.exit_code == 0, first.stderr)
        require(
            first.stdout == "" and "\x1b" not in first.stderr,
            "Unexpected protocol output",
        )
        with Store(db) as store:
            conn = store.conn
            initial = counts(conn)
            require(initial["snapshots"] == len(paths), "Missing capture")
            require(
                not list(conn.execute("PRAGMA foreign_key_check")), "Broken foreign key"
            )
            require(
                conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                "Integrity check failed",
            )
            for row in conn.execute("SELECT * FROM entry_revisions"):
                entry = store.load_entry(row["id"])
                require(
                    json.loads(row["payload_json"]) == entry.parser_fields,
                    "Payload mismatch",
                )
                require(
                    row["fingerprint"] == fingerprint(entry, row["feedparser_version"]),
                    "Fingerprint mismatch",
                )
                revision = EntryRevision(
                    **{
                        key: row[key]
                        for key in EntryRevision.model_fields
                        if key != "snapshot_id"
                    },
                    snapshot_id=row["source_snapshot_id"],
                )
                require(
                    EntryRevision.model_validate_json(revision.model_dump_json())
                    == revision,
                    "Revision round trip failed",
                )
                require(
                    entry.title is None or entry.title.value == row["title"],
                    "SQL title mismatch",
                )
            for row in conn.execute("SELECT * FROM snapshots ORDER BY id"):
                require(
                    hashlib.sha256(row["source_bytes"]).hexdigest()
                    == row["source_sha256"],
                    "Source bytes mismatch",
                )
                entries = [
                    store.load_entry(obs[0])
                    for obs in conn.execute(
                        "SELECT revision_id FROM snapshot_entries WHERE snapshot_id=? ORDER BY position",
                        (row["id"],),
                    )
                ]
                snapshot = Snapshot(
                    feed=Feed.model_validate_json(row["feed_json"]),
                    entries=entries,
                    namespaces=json.loads(row["namespaces_json"]),
                    **{
                        key: row[key]
                        for key in Snapshot.model_fields
                        if key not in {"feed", "entries", "namespaces"}
                    },
                )
                require(snapshot.captured_at is not None, "Missing known capture time")
                require(
                    Snapshot.model_validate_json(snapshot.model_dump_json())
                    == snapshot,
                    "Snapshot round trip failed",
                )
            html = {"attempts": 0, "sections": 0, "editorial_blocks": 0}
            for row in conn.execute(
                "SELECT status,features_json FROM content_extractions"
            ):
                require(row["status"] == "completed", "Extraction failed")
                features = HTMLFeatures.model_validate_json(row["features_json"])
                require(
                    HTMLFeatures.model_validate_json(features.model_dump_json())
                    == features,
                    "HTML round trip failed",
                )
                html["attempts"] += 1
                html["sections"] += len(features.sections)
                html["editorial_blocks"] += bool(features.editorial)
            unchanged = initial["snapshot_entries"] - initial["entry_revisions"]
            histories = dict(
                conn.execute(
                    "SELECT revision_count,COUNT(*) FROM (SELECT entry_id,COUNT(*) AS revision_count FROM entry_revisions GROUP BY entry_id) GROUP BY revision_count"
                )
            )
        replay = CliRunner(mix_stderr=False).invoke(cli, args)
        require(replay.exit_code == 0, replay.stderr)
        with Store(db) as store:
            require(counts(store.conn) == initial, "Replay changed database counts")
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": SCHEMA_VERSION,
            "versions": {
                name: version(name)
                for name in (
                    "feedparser",
                    "pydantic",
                    "beautifulsoup4",
                    "lxml",
                    "htmd-py",
                    "rich",
                )
            },
            "sample_files": len(paths),
            "counts": initial,
            "unchanged_observations": unchanged,
            "items_by_revision_count": histories,
            "html": html,
            "first_summary": first.stderr.strip(),
            "replay_summary": replay.stderr.strip(),
            "checks": [
                "manifest SHA-256",
                "retained artifact SHA-256",
                "model/SQL round trips",
                "revision fingerprints",
                "query views",
                "foreign keys",
                "SQLite integrity",
                "idempotent replay",
                "clean stdout/plain stderr",
            ],
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(verify(options.dataset.resolve()), indent=2))
