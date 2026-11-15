"""Portable production-path checks: capture, history, recovery, and honest output."""

import gzip
import hashlib
import sqlite3

import pytest
from click.testing import CliRunner

from prompthound.cli import cli
from prompthound.extraction import extract_block
from prompthound.schema import connect, initialize
from prompthound.storage import Store


def rss(title="A", extra=""):
    return (
        '<rss version="2.0"><channel><title>Example</title>'
        "<link>https://example.com/</link><description>Example feed</description>"
        f'<item><guid isPermaLink="false">opaque:1</guid><title>{title}</title>'
        "<link>https://example.com/post</link>"
        '<description>&lt;p&gt;Body &lt;a href="/more"&gt;more&lt;/a&gt;&lt;/p&gt;</description>'
        f"</item>{extra}</channel></rss>"
    ).encode()


def invoke(db, *args, **kwargs):
    return CliRunner(mix_stderr=False).invoke(
        cli, ["ingest", "--db", str(db), *map(str, args)], **kwargs
    )


def test_cli_capture_replay_changes_reversions_and_provenance(tmp_path):
    db = tmp_path / "nested" / "feed.db"
    path = tmp_path / "feed-2026-10-03T12:00:00Z.rss"
    for title, expected in [("A", 1), ("A", 1), ("B", 2), ("A", 2)]:
        path.write_bytes(rss(title))
        result = invoke(db, "--no-progress", path)
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "\x1b" not in result.stderr
        with Store(db) as store:
            assert (
                store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
                == expected
            )
    # Returning exactly to an already processed capture is a replay. A different
    # capture of A, after B, records the reversion rather than deduplicating history.
    second = tmp_path / "feed-2026-10-04T12:00:00Z.rss"
    second.write_bytes(rss("A"))
    assert invoke(db, second).exit_code == 0
    with Store(db) as store:
        assert [
            r[0]
            for r in store.conn.execute("SELECT title FROM entry_revisions ORDER BY id")
        ] == ["A", "B", "A"]
        assert (
            store.conn.execute("SELECT COUNT(*) FROM snapshot_entries").fetchone()[0]
            == 3
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM latest_entries").fetchone()[0] == 1
        )
        source = store.conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC"
        ).fetchone()
        assert source["source_bytes"] == rss("A")
        assert source["source_sha256"] == hashlib.sha256(rss("A")).hexdigest()
        assert source["captured_at"].startswith("2026-10-04T12:00:00")
        assert source["recorded_at"] != source["captured_at"]
        assert not list(store.conn.execute("PRAGMA foreign_key_check"))


def test_stdin_capture_ids_and_unknown_capture_time(tmp_path):
    db = tmp_path / "feed.db"
    for capture_id in ["one", "one", "two"]:
        result = invoke(db, "--capture-id", capture_id, "-", input=rss())
        assert result.exit_code == 0, result.output
    with Store(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute(
                "SELECT COUNT(*) FROM snapshots WHERE captured_at IS NULL"
            ).fetchone()[0]
            == 2
        )
    conflict = invoke(db, "--capture-id", "one", input=rss("changed"))
    assert conflict.exit_code == 1
    assert "different source bytes" in conflict.stderr.lower()


def test_duplicate_item_observations_and_gzip_preserve_raw_bytes(tmp_path):
    db = tmp_path / "feed.db"
    path = tmp_path / "feed.rss.gz"
    extra = '<item><guid isPermaLink="false">opaque:1</guid><title>B</title></item>'
    compressed = gzip.compress(rss(extra=extra))
    path.write_bytes(compressed)
    result = invoke(db, path)
    assert result.exit_code == 0, result.output
    with Store(db) as store:
        assert [
            r[0]
            for r in store.conn.execute(
                "SELECT position FROM snapshot_entries ORDER BY position"
            )
        ] == [0, 1]
        assert store.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 2
        )
        assert (
            store.conn.execute("SELECT source_bytes FROM snapshots").fetchone()[0]
            == compressed
        )


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"not xml",
        b"<rss><channel>",
        b'<rss version="2.0"><channel><link>https://example.com/</link></channel></rss>',
        None,
    ],
)
def test_invalid_or_unreadable_input_does_not_abort_later_files(tmp_path, bad):
    path = tmp_path / "bad.rss"
    if bad is not None:
        path.write_bytes(bad)
    good = tmp_path / "good.rss"
    good.write_bytes(rss())
    db = tmp_path / "feed.db"
    result = invoke(db, path, good)
    assert result.exit_code == 1, result.output
    assert "1 of 2 inputs" in result.stderr
    assert "1 revisions" in result.stderr
    assert "Ingestion complete." not in result.stderr
    with Store(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_extraction_write_failure_preserves_source_and_can_be_retried(tmp_path):
    db = tmp_path / "feed.db"
    conn = connect(db)
    initialize(conn)
    conn.execute("""CREATE TRIGGER fail_extraction BEFORE INSERT ON content_extractions
        BEGIN SELECT RAISE(ABORT, 'derived write failure'); END""")
    conn.close()
    result = invoke(db, input=rss())
    assert result.exit_code == 1, result.output
    assert "source history was preserved" in result.stderr
    assert "1 revisions" in result.stderr
    assert "1 pending extractions" in result.stderr
    with Store(db) as store:
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        store.conn.execute("DROP TRIGGER fail_extraction")
    retried = CliRunner(mix_stderr=False).invoke(
        cli, ["extract", "--db", str(db), "--no-progress"]
    )
    assert retried.exit_code == 0, retried.output
    assert retried.stdout == ""
    assert "0 pending extractions" in retried.stderr
    with Store(db) as store:
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute("SELECT text FROM latest_entry_content").fetchone()[0]
            == "Body more"
        )


def test_interrupt_rolls_back_current_snapshot_and_reports_committed_counts(
    tmp_path, monkeypatch
):
    db = tmp_path / "feed.db"
    first = tmp_path / "first.rss"
    second = tmp_path / "second.rss"
    first.write_bytes(rss("A"))
    second.write_bytes(rss("B"))
    original = Store._source_children

    def interrupt(self, *args, **kwargs):
        if self.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0] == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "_source_children", interrupt)
    result = invoke(db, first, second)
    assert result.exit_code == 1, result.output
    assert "Ingestion interrupted." in result.stderr
    assert "2 inputs" in result.stderr
    assert "1 revisions" in result.stderr
    assert "Ingestion complete." not in result.stderr
    with Store(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )


def test_init_is_repeatable_without_prompt_and_refuses_unknown_database(tmp_path):
    db = tmp_path / "feed.db"
    assert invoke(db, input=rss()).exit_code == 0
    for _ in range(2):
        result = CliRunner(mix_stderr=False).invoke(cli, ["init", "--db", str(db)])
        assert result.exit_code == 0, result.output
        assert "Overwrite" not in result.output
    with Store(db) as store:
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
    other = tmp_path / "unrelated.db"
    with sqlite3.connect(other) as conn:
        conn.execute("CREATE TABLE keep(value TEXT)")
        conn.execute("INSERT INTO keep VALUES('untouched')")
    before = other.read_bytes()
    result = CliRunner(mix_stderr=False).invoke(cli, ["init", "--db", str(other)])
    assert result.exit_code == 1, result.output
    assert other.read_bytes() == before


def test_bad_capture_options_fail_before_creating_database(tmp_path):
    db = tmp_path / "feed.db"
    for args in [
        ("--captured-at", "2026-10-03T12:00:00"),
        ("--capture-id", "a", "one.rss", "two.rss"),
    ]:
        result = invoke(db, *args)
        assert result.exit_code == 2, result.output
        assert not db.exists()


def test_cli_migrates_legacy_data_keeps_backup_and_appends_new_snapshot(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE feeds(id TEXT PRIMARY KEY,title TEXT,link TEXT)")
        conn.execute(
            "INSERT INTO feeds VALUES('https://example.com/','Example','https://example.com/')"
        )
        conn.execute(
            "CREATE TABLE old_posts(id TEXT PRIMARY KEY,feed TEXT,title TEXT,description TEXT)"
        )
        conn.execute(
            "INSERT INTO old_posts VALUES('opaque:1','https://example.com/','Old','<p>Old body</p>')"
        )
    result = invoke(db, input=rss())
    assert result.exit_code == 0, result.stderr
    assert "Original database backed up at" in result.stderr
    assert "2 revisions" in result.stderr  # Includes committed migration rows.
    backups = list(tmp_path.glob("*.pre-ledger-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT title FROM old_posts").fetchone()[0] == "Old"
    with Store(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        assert [
            r[0]
            for r in store.conn.execute(
                "SELECT title FROM entry_history ORDER BY revision_number"
            )
        ] == ["Old", "A"]
        assert not list(store.conn.execute("PRAGMA foreign_key_check"))


def test_failed_extraction_attempt_is_explicit_and_later_success_does_not_revise_source(
    tmp_path, monkeypatch
):
    db = tmp_path / "feed.db"

    def fail(*args, **kwargs):
        raise ValueError("cannot convert HTML")

    monkeypatch.setattr("prompthound.extraction.extract_block", fail)
    result = invoke(db, input=rss())
    assert result.exit_code == 1, result.stderr
    assert "cannot convert HTML" in result.stderr
    with Store(db) as store:
        failed = store.conn.execute(
            "SELECT status,error FROM content_extractions"
        ).fetchone()
        assert tuple(failed) == ("failed", "cannot convert HTML")
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
    monkeypatch.setattr("prompthound.extraction.extract_block", extract_block)
    retried = invoke(db, input=rss())
    assert retried.exit_code == 0, retried.stderr
    assert "1 replayed" in retried.stderr
    with Store(db) as store:
        assert [
            r[0]
            for r in store.conn.execute(
                "SELECT status FROM content_extractions ORDER BY id"
            )
        ] == ["failed", "completed"]
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )


def test_extraction_interrupt_keeps_source_and_reports_pending_work(
    tmp_path, monkeypatch
):
    db = tmp_path / "feed.db"

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("prompthound.extraction.extract_block", interrupt)
    result = invoke(db, input=rss())
    assert result.exit_code == 1, result.stderr
    assert "Ingestion interrupted." in result.stderr
    assert "1 revisions" in result.stderr and "1 pending extractions" in result.stderr
    with Store(db) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    result = CliRunner(mix_stderr=False).invoke(cli, ["extract", "--db", str(db)])
    assert result.exit_code == 1
    assert (
        "Extraction interrupted." in result.stderr
        and "1 pending extractions" in result.stderr
    )
