import base64
import json
import sqlite3

import pytest
import sqlite_utils

from prompthound.migration import MigrationError, prepare_database
from prompthound.schema import connect, table_names
from prompthound.storage import Store


def legacy_database(path):
    db = sqlite_utils.Database(path)
    db["feeds"].insert(
        {
            "id": "feed-1",
            "title": "Legacy",
            "link": "https://example.com",
            "custom": "keep",
        },
        pk="id",
    )
    db["legacy_posts"].insert(
        {
            "id": "post-1",
            "feed": "feed-1",
            "title": "Post",
            "description": "<p>Body</p>",
            "published": "2026-01-01 12:00:00",
            "extra": "preserve",
            "binary": b"\x00\xff",
        },
        pk="id",
    )
    db["unrelated"].insert({"value": "untouched"})
    db["legacy_posts"].add_foreign_key("feed", "feeds", "id")
    db.conn.close()


def test_legacy_rows_and_backups_are_preserved_and_reruns_are_safe(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    events = []
    result = prepare_database(path, progress=events.append)
    assert result.migrated and result.converted_rows == 1
    assert result.backup_path.is_file()
    with sqlite3.connect(result.backup_path) as backup:
        assert (
            backup.execute("SELECT extra FROM legacy_posts").fetchone()[0] == "preserve"
        )
        assert backup.execute("SELECT COUNT(*) FROM feeds").fetchone()[0] == 1
    with Store(path) as store:
        assert (
            store.conn.execute("SELECT value FROM unrelated").fetchone()[0]
            == "untouched"
        )
        retained = [
            name
            for name in table_names(store.conn)
            if name.startswith("__prompthound_legacy_")
        ]
        assert len(retained) == 2
        revision = store.conn.execute("SELECT id FROM entry_revisions").fetchone()[0]
        entry = store.load_entry(revision)
        assert entry.id == "post-1"
        assert entry.parser_fields["legacy"]["values"]["extra"] == "preserve"
        assert (
            base64.b64decode(
                entry.parser_fields["legacy"]["values"]["binary"][
                    "$legacy_bytes_base64"
                ]
            )
            == b"\x00\xff"
        )
        assert (
            entry.published is None
        )  # Naive historical dates do not invent a timezone.
        row = store.conn.execute("SELECT * FROM snapshots").fetchone()
        assert row["captured_at"] is None and row["format"] == "legacy-json"
        assert (
            json.loads(row["source_bytes"])["items"][0]["values"]["extra"] == "preserve"
        )
        assert not list(store.conn.execute("PRAGMA foreign_key_check"))
    assert prepare_database(path).migrated is False
    assert len(list(tmp_path.glob("*.pre-ledger-*.sqlite3"))) == 1
    assert events[-1].stage == "migrate" and events[-1].outcome == "completed"
    assert sum(event.outcome == "committed" for event in events) == 1


def test_unknown_database_and_ambiguous_feed_associations_do_not_change_data(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE legacy_posts SET feed='missing'")
    with pytest.raises(MigrationError, match="association"):
        prepare_database(path)
    conn = connect(path)
    assert table_names(conn) == {"feeds", "legacy_posts", "unrelated"}
    assert conn.execute("SELECT feed FROM legacy_posts").fetchone()[0] == "missing"
    conn.close()
    unknown = tmp_path / "unknown.db"
    with sqlite3.connect(unknown) as conn:
        conn.execute("CREATE TABLE keep(value TEXT)")
    with pytest.raises(MigrationError, match="Unrecognized"):
        prepare_database(unknown)


@pytest.mark.parametrize("error", [RuntimeError("injected"), KeyboardInterrupt()])
def test_mid_migration_failure_retains_original_schema_and_recoverable_backup(
    tmp_path, error
):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    events = []

    def fail(event):
        events.append(event)
        if event.stage == "migrate" and event.outcome == "progress":
            raise error

    with pytest.raises(type(error)):
        prepare_database(path, progress=fail)
    conn = connect(path)
    assert table_names(conn) == {"feeds", "legacy_posts", "unrelated"}
    assert conn.execute("SELECT extra FROM legacy_posts").fetchone()[0] == "preserve"
    conn.close()
    assert len(list(tmp_path.glob("*.pre-ledger-*.sqlite3"))) == 1
    assert all(event.counts is None for event in events)
    assert prepare_database(path).migrated


def test_fresh_database_initializes_without_a_legacy_backup(tmp_path):
    path = tmp_path / "fresh.db"
    assert not prepare_database(path).migrated
    with Store(path) as store:
        assert "entry_revisions" in table_names(store.conn)
    assert not list(tmp_path.glob("*.pre-ledger-*.sqlite3"))


def test_post_commit_callback_failure_does_not_report_a_false_rollback(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    events = []

    def fail_after_commit(event):
        events.append(event)
        if event.outcome == "committed":
            raise RuntimeError("renderer failure")

    with pytest.raises(RuntimeError, match="renderer"):
        prepare_database(path, progress=fail_after_commit)
    assert not any(event.outcome == "rolled_back" for event in events)
    assert prepare_database(path).migrated is False
    with Store(path) as store:
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
