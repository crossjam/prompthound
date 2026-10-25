import sqlite3

import pytest

from prompthound.schema import SchemaError, connect, initialize
from prompthound.models import Entry
from prompthound.parsing import entry_from_fields


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "schema.db")
    initialize(connection)
    yield connection
    connection.close()


def seed_snapshot(conn, feed_id=1, snapshot_id=1, key="capture"):
    conn.execute(
        "INSERT OR IGNORE INTO feeds(id,publisher_id) VALUES (?,?)",
        (feed_id, f"feed-{feed_id}"),
    )
    conn.execute(
        """INSERT INTO snapshots(id,feed_id,capture_key,source_uri,source_sha256,
                 source_bytes,recorded_at,feed_json,feedparser_version,parse_policy,format)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            snapshot_id,
            feed_id,
            key,
            "test",
            "a" * 64,
            b"source",
            "now",
            "{}",
            "v1",
            "p1",
            "rss20",
        ),
    )


def seed_revision(
    conn,
    entry_id=1,
    feed_id=1,
    snapshot_id=1,
    sequence=1,
    previous=None,
    *,
    entry_json="{}",
    payload_json="{}",
):
    conn.execute(
        "INSERT OR IGNORE INTO entries(id,feed_id,publisher_id) VALUES (?,?,?)",
        (entry_id, feed_id, f"guid-{entry_id}"),
    )
    return conn.execute(
        """INSERT INTO entry_revisions(entry_id,feed_id,revision_number,
                 previous_revision_id,source_snapshot_id,fingerprint,fingerprint_policy,
                 feedparser_version,payload_json,entry_json,recorded_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            entry_id,
            feed_id,
            sequence,
            previous,
            snapshot_id,
            "b" * 64,
            "p1",
            "v1",
            payload_json,
            entry_json,
            "now",
        ),
    ).lastrowid


def test_initialization_is_versioned_repeatable_and_enforces_foreign_keys(conn):
    initialize(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entries(feed_id,publisher_id) VALUES (999,'orphan')")
    assert not list(conn.execute("PRAGMA foreign_key_check"))


def test_unknown_database_is_not_overwritten(tmp_path):
    conn = connect(tmp_path / "unknown.db")
    conn.execute("CREATE TABLE keep(value TEXT)")
    conn.execute("INSERT INTO keep VALUES ('original')")
    with pytest.raises(SchemaError, match="legacy"):
        initialize(conn)
    assert conn.execute("SELECT value FROM keep").fetchone()[0] == "original"
    conn.close()


def test_repeated_fingerprints_are_valid_but_sequence_must_follow_latest(conn):
    seed_snapshot(conn)
    first = seed_revision(conn)
    second = seed_revision(conn, sequence=2, previous=first)
    third = seed_revision(conn, sequence=3, previous=second)
    assert first != third
    with pytest.raises(sqlite3.IntegrityError, match="latest"):
        seed_revision(conn, sequence=5, previous=third)
    with pytest.raises(sqlite3.IntegrityError, match="latest"):
        seed_revision(conn, sequence=4, previous=first)


def test_revision_predecessor_cannot_reference_another_item(conn):
    seed_snapshot(conn)
    first = seed_revision(conn)
    other = seed_revision(conn, entry_id=2)
    with pytest.raises(sqlite3.IntegrityError):
        seed_revision(conn, sequence=2, previous=other)
    assert first != other


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE entry_revisions SET title='changed'",
        "DELETE FROM entry_revisions",
        "INSERT OR REPLACE INTO feeds(id,publisher_id) VALUES (1,'feed-1')",
    ],
)
def test_source_history_is_immutable(conn, statement):
    seed_snapshot(conn)
    seed_revision(conn)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(statement)


def test_observation_feed_and_revision_identity_must_agree(conn):
    seed_snapshot(conn)
    first = seed_revision(conn)
    seed_snapshot(conn, feed_id=2, snapshot_id=2)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO snapshot_entries VALUES (2,0,2,1,?)", (first,))
    conn.execute("INSERT INTO snapshot_entries VALUES (1,0,1,1,?)", (first,))
    conn.execute("INSERT INTO snapshot_entries VALUES (1,1,1,1,?)", (first,))
    assert conn.execute("SELECT COUNT(*) FROM snapshot_entries").fetchone()[0] == 2


def test_distinct_captures_may_have_identical_bytes_and_null_capture_time(conn):
    seed_snapshot(conn)
    seed_snapshot(conn, snapshot_id=2, key="other-capture")
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE captured_at IS NULL"
        ).fetchone()[0]
        == 2
    )


def test_bad_json_and_negative_metadata_are_rejected(conn):
    seed_snapshot(conn)
    revision = seed_revision(conn)
    conn.execute(
        "INSERT INTO content_blocks(id,revision_id,role,position,type,value) VALUES (1,?,'content',0,'text/plain','text')",
        (revision,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO content_extractions(block_id,extractor_version,config_json,recorded_at,status,error)
                        VALUES (1,'v1','invalid','now','failed','test')""")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO revision_tags VALUES (?,-1,'tag',NULL,NULL)", (revision,)
        )


def test_model_json_roundtrips_through_sql_without_losing_extensions(conn):
    seed_snapshot(conn)
    entry = entry_from_fields(
        {
            "id": "guid-1",
            "title": "Café",
            "authors": [{"name": "A", "custom": 7}],
            "tags": [{"term": "topic"}],
            "unknown": {"nested": [1, None]},
        },
        "feed-1",
    )
    revision = seed_revision(conn, entry_json=entry.model_dump_json())
    stored = conn.execute(
        "SELECT entry_json FROM entry_revisions WHERE id=?", (revision,)
    ).fetchone()[0]
    assert Entry.model_validate_json(stored) == entry


def test_newer_schema_version_is_rejected_without_reinitialization(conn):
    conn.execute("INSERT INTO schema_migrations VALUES (999,'future')")
    with pytest.raises(SchemaError, match="999"):
        initialize(conn)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
