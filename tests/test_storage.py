from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import sqlite3

import pytest

from prompthound.events import Results
from prompthound.parsing import parse_snapshot
from prompthound.storage import CaptureConflict, Store


def snapshot(
    title="A",
    source="capture-1",
    *,
    guid="item",
    site="https://example.com",
    comments=0,
    extra="",
):
    xml = f"""<rss version="2.0" xmlns:slash="http://purl.org/rss/1.0/modules/slash/">
    <channel><title>Feed</title><link>{site}</link><description>Feed</description>
    <item><guid isPermaLink="false">{guid}</guid><title>{title}</title>
    <description><![CDATA[<p>Body {title}</p>]]></description><category>Topic</category>
    <slash:comments>{comments}</slash:comments></item>{extra}</channel></rss>""".encode()
    return parse_snapshot(xml, source_uri=source), xml


def test_unchanged_captures_and_reversions_form_a_ledger(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        results = []
        for i, title in enumerate(["A", "A", "B", "B", "A"]):
            model, xml = snapshot(title, f"capture-{i}")
            results.append(store.ingest(model, xml))
        assert [r.counts.revisions for r in results] == [1, 0, 1, 0, 1]
        rows = list(
            store.conn.execute("SELECT * FROM entry_revisions ORDER BY revision_number")
        )
        assert [row["revision_number"] for row in rows] == [1, 2, 3]
        assert (
            rows[0]["fingerprint"] == rows[2]["fingerprint"] != rows[1]["fingerprint"]
        )
        assert [row["previous_revision_id"] for row in rows] == [
            None,
            rows[0]["id"],
            rows[1]["id"],
        ]
        assert (
            store.conn.execute("SELECT COUNT(*) FROM snapshot_entries").fetchone()[0]
            == 5
        )
        assert store.load_entry(rows[0]["id"]).title.value == "A"
        assert not list(store.conn.execute("PRAGMA foreign_key_check"))


def test_capture_replay_and_conflicting_explicit_identity(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        model, xml = snapshot()
        first = store.ingest(model, xml)
        replay = store.ingest(model, xml)
        assert replay.replayed and replay.snapshot_id == first.snapshot_id
        assert replay.counts.observations == 0
        changed, changed_xml = snapshot("B")
        changed.capture_key = model.capture_key
        with pytest.raises(CaptureConflict):
            store.ingest(changed, changed_xml)
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_duplicate_ids_preserve_positions_and_different_values(tmp_path):
    extra = '<item><guid isPermaLink="false">item</guid><title>B</title></item>'
    model, xml = snapshot(extra=extra)
    with Store(tmp_path / "ledger.db") as store:
        result = store.ingest(model, xml)
        assert result.counts.new_items == 1
        assert result.counts.revisions == 2
        assert [
            row[0]
            for row in store.conn.execute(
                "SELECT position FROM snapshot_entries ORDER BY position"
            )
        ] == [0, 1]


def test_metadata_only_changes_and_same_guid_in_different_feeds(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        for i, args in enumerate(
            [{"comments": 0}, {"comments": 1}, {"site": "https://other.example"}]
        ):
            model, xml = snapshot(source=f"capture-{i}", **args)
            store.ingest(model, xml)
        assert store.conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 2
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 3
        )


def test_failures_rollback_all_rows_and_never_publish_committed_counts(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        store.conn.execute("""CREATE TRIGGER fail_item BEFORE INSERT ON entry_revisions
                            WHEN NEW.title='explode' BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        extra = "<item><guid>second</guid><title>explode</title></item>"
        model, xml = snapshot(extra=extra)
        events = []
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            store.ingest(model, xml, progress=events.append)
        for table in (
            "feeds",
            "entries",
            "snapshots",
            "entry_revisions",
            "snapshot_entries",
            "content_blocks",
            "revision_tags",
        ):
            assert (
                store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            )
        assert events[-1].outcome == "rolled_back"
        assert all(event.counts is None for event in events)
        store.conn.execute("DROP TRIGGER fail_item")
        assert store.ingest(model, xml).counts.revisions == 2


def test_identical_bytes_distinct_captures_and_parser_versions(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        first, xml = snapshot()
        store.ingest(first, xml)
        second = first.model_copy(deep=True)
        second.capture_key = "another capture"
        assert store.ingest(second, xml).counts.revisions == 0
        second.feedparser_version = "future-parser"
        assert store.ingest(second, xml).counts.revisions == 1
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 3


def test_late_capture_keeps_capture_time_separate_from_recorded_order(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        for title, date in [("A", "2026-10-20"), ("B", "2026-10-01")]:
            model, xml = snapshot(title, f"snapshot-{date}T00:00:00Z.rss")
            store.ingest(model, xml)
        rows = list(
            store.conn.execute("""SELECT r.revision_number,s.captured_at,r.recorded_at FROM entry_revisions r
                                         JOIN snapshots s ON s.id=r.source_snapshot_id ORDER BY r.revision_number""")
        )
        assert rows[1]["captured_at"] < rows[0]["captured_at"]
        assert rows[1]["recorded_at"] >= rows[0]["recorded_at"]


def test_competing_writers_recheck_capture_replay(tmp_path):
    path = tmp_path / "ledger.db"
    barrier = Barrier(2)
    model, xml = snapshot()

    def write():
        with Store(path) as store:
            barrier.wait(timeout=5)
            return store.ingest(model, xml)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(result.replayed for result in results) == [False, True]
    with Store(path) as store:
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM snapshot_entries").fetchone()[0]
            == 1
        )


def test_locked_writer_retries_without_duplicate_rows(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    with Store(path) as store, Store(path) as locker:
        store.conn.execute("PRAGMA busy_timeout=1")
        locker.conn.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(
            "prompthound.storage.time.sleep", lambda _: locker.conn.rollback()
        )
        model, xml = snapshot()
        assert store.ingest(model, xml).counts.revisions == 1
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_interrupt_rolls_back_and_emits_no_committed_counts(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        model, xml = snapshot()
        events = []

        def interrupt(event):
            events.append(event)
            if event.outcome == "progress":
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            store.ingest(model, xml, progress=interrupt)
        assert events[-1].outcome == "interrupted"
        assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
        assert all(event.counts is None for event in events)


def test_only_committed_results_are_published(tmp_path):
    with Store(tmp_path / "ledger.db") as store:
        model, xml = snapshot()
        events = []
        result = store.ingest(model, xml, progress=events.append)
        assert events[-1].outcome == "committed"
        results = Results()
        for event in events:
            results.consume(event)
        assert results.revisions == result.counts.revisions == 1
        assert (
            store.conn.execute("SELECT source_bytes FROM snapshots").fetchone()[0]
            == xml
        )
        with pytest.raises(ValueError, match="checksum"):
            store.ingest(model, b"different")
