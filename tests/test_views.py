from prompthound.extraction import extract_pending
from prompthound.parsing import parse_snapshot
from prompthound.schema import initialize
from prompthound.storage import Store


def ingest(store, title, source, *, site="https://example.com", body=""):
    xml = f"""<rss version="2.0"><channel><title>Feed</title><link>{site}</link>
    <item><guid isPermaLink="false">same-guid</guid><title>{title}</title>{body}</item></channel></rss>""".encode()
    model = parse_snapshot(xml, source_uri=source)
    return store.ingest(model, xml)


def test_latest_is_ledger_order_with_complete_feed_scoped_history(tmp_path):
    with Store(tmp_path / "views.db") as store:
        for title, source in [
            ("A", "2026-10-20T00:00:00Z.rss"),
            ("B", "2026-10-22T00:00:00Z.rss"),
            ("A", "2026-10-01T00:00:00Z.rss"),
            ("A", "2026-10-02T00:00:00Z.rss"),
        ]:
            ingest(store, title, source)
        ingest(store, "Other", "other", site="https://other.example")
        rows = list(
            store.conn.execute("SELECT * FROM latest_entries ORDER BY entry_id")
        )
        assert len(rows) == 2
        assert [(row["title"], row["revision_number"]) for row in rows] == [
            ("A", 3),
            ("Other", 1),
        ]
        assert rows[0]["captured_at"].startswith("2026-10-01")
        assert rows[0]["last_observed_captured_at"].startswith("2026-10-02")
        assert rows[0]["item_identifier"] == rows[1]["item_identifier"] == "same-guid"
        assert rows[0]["feed_identifier"] != rows[1]["feed_identifier"]
        assert rows[0]["updated_at"] is None
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_history").fetchone()[0] == 4
        )
        assert (
            store.conn.execute(
                "SELECT COUNT(*) FROM latest_entry_content WHERE block_id IS NULL"
            ).fetchone()[0]
            == 2
        )


def test_content_selects_latest_successful_attempt_without_duplicate_versions(tmp_path):
    with Store(tmp_path / "views.db") as store:
        result = ingest(
            store,
            "A",
            "one",
            body="<description><![CDATA[<p>Content</p>]]></description>",
        )
        before = store.conn.execute("SELECT * FROM latest_entry_content").fetchone()
        assert before["value"] == "<p>Content</p>" and before["text"] is None
        extract_pending(store, result.snapshot_id, extractor_version="v1")
        extract_pending(store, result.snapshot_id, extractor_version="v2")
        store.conn.execute(
            """INSERT INTO content_extractions(block_id,extractor_version,config_json,
                            recorded_at,status,error) VALUES (?,'v3','{}','now','failed','failure')""",
            (before["block_id"],),
        )
        rows = list(store.conn.execute("SELECT * FROM latest_entry_content"))
        assert len(rows) == 1
        assert rows[0]["extractor_version"] == "v2"
        assert rows[0]["text"] == "Content"
        assert rows[0]["item_identifier"] == "same-guid"


def test_history_lookup_uses_revision_and_extraction_indexes(tmp_path):
    with Store(tmp_path / "views.db") as store:
        ingest(store, "A", "one", body="<description>Body</description>")
        plan = " ".join(
            row[3]
            for row in store.conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM latest_entry_content WHERE entry_id=1"
            )
        )
        assert "revisions_entry_sequence" in plan
        assert "extractions_success_latest" in plan


def test_version_one_database_upgrades_without_replacing_history(tmp_path):
    with Store(tmp_path / "views.db") as store:
        ingest(store, "A", "one")
        for name in ("latest_entry_content", "latest_entries", "entry_history"):
            store.conn.execute(f"DROP VIEW {name}")
        store.conn.execute("DROP INDEX extractions_success_latest")
        store.conn.execute("DELETE FROM schema_migrations WHERE version=2")
        initialize(store.conn)
        assert (
            store.conn.execute("SELECT title FROM latest_entries").fetchone()[0] == "A"
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        initialize(store.conn)
        assert (
            store.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            == 2
        )
