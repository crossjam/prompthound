"""Explicit SQLite schema and transactional initialization for immutable history."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import cast

import sqlite_utils

APPLICATION_ID = 0x50484E44
SCHEMA_VERSION = 2


class SchemaError(ValueError):
    pass


def connect(path: str | Path) -> sqlite3.Connection:
    conn = cast(sqlite3.Connection, sqlite_utils.Database(path).conn)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA recursive_triggers = ON")
    conn.execute("PRAGMA busy_timeout = 1000")
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


TABLES = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)""",
    """CREATE TABLE feeds (
        id INTEGER PRIMARY KEY, publisher_id TEXT NOT NULL UNIQUE,
        url TEXT, CHECK(length(publisher_id) > 0))""",
    """CREATE TABLE entries (
        id INTEGER PRIMARY KEY, feed_id INTEGER NOT NULL REFERENCES feeds(id),
        publisher_id TEXT NOT NULL CHECK(length(publisher_id) > 0),
        UNIQUE(feed_id, publisher_id), UNIQUE(id, feed_id))""",
    """CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY, feed_id INTEGER NOT NULL REFERENCES feeds(id),
        capture_key TEXT NOT NULL CHECK(length(capture_key) > 0),
        source_uri TEXT NOT NULL, source_sha256 TEXT NOT NULL
            CHECK(length(source_sha256)=64 AND source_sha256 NOT GLOB '*[^0-9a-f]*'),
        source_bytes BLOB NOT NULL, captured_at TEXT, recorded_at TEXT NOT NULL,
        feed_json TEXT NOT NULL CHECK(json_valid(feed_json)),
        namespaces_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(namespaces_json)),
        feedparser_version TEXT NOT NULL, parse_policy TEXT NOT NULL,
        format TEXT NOT NULL, encoding TEXT, bozo INTEGER NOT NULL DEFAULT 0 CHECK(bozo IN (0,1)),
        parse_error TEXT,
        UNIQUE(feed_id, capture_key, feedparser_version, parse_policy), UNIQUE(id, feed_id))""",
    """CREATE TABLE entry_revisions (
        id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL, feed_id INTEGER NOT NULL,
        revision_number INTEGER NOT NULL CHECK(revision_number >= 1),
        previous_revision_id INTEGER, source_snapshot_id INTEGER NOT NULL,
        fingerprint TEXT NOT NULL
            CHECK(length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*'),
        fingerprint_policy TEXT NOT NULL, feedparser_version TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
        entry_json TEXT NOT NULL CHECK(json_valid(entry_json)),
        title TEXT, url TEXT, published_at TEXT, updated_at TEXT,
        comments_url TEXT, comments_feed_url TEXT,
        comment_count INTEGER CHECK(comment_count >= 0),
        wordpress_post_id INTEGER CHECK(wordpress_post_id >= 0),
        id_is_permalink INTEGER CHECK(id_is_permalink IN (0,1)),
        recorded_at TEXT NOT NULL,
        CHECK((revision_number=1 AND previous_revision_id IS NULL) OR
              (revision_number>1 AND previous_revision_id IS NOT NULL)),
        UNIQUE(entry_id, revision_number), UNIQUE(id, entry_id),
        FOREIGN KEY(entry_id, feed_id) REFERENCES entries(id, feed_id),
        FOREIGN KEY(source_snapshot_id, feed_id) REFERENCES snapshots(id, feed_id),
        FOREIGN KEY(previous_revision_id, entry_id) REFERENCES entry_revisions(id, entry_id))""",
    """CREATE TABLE snapshot_entries (
        snapshot_id INTEGER NOT NULL, position INTEGER NOT NULL CHECK(position >= 0),
        feed_id INTEGER NOT NULL, entry_id INTEGER NOT NULL, revision_id INTEGER NOT NULL,
        PRIMARY KEY(snapshot_id, position),
        FOREIGN KEY(snapshot_id, feed_id) REFERENCES snapshots(id, feed_id),
        FOREIGN KEY(entry_id, feed_id) REFERENCES entries(id, feed_id),
        FOREIGN KEY(revision_id, entry_id) REFERENCES entry_revisions(id, entry_id))""",
    """CREATE TABLE content_blocks (
        id INTEGER PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES entry_revisions(id),
        role TEXT NOT NULL CHECK(role IN ('summary','content')),
        position INTEGER NOT NULL CHECK(position >= 0),
        type TEXT NOT NULL, language TEXT, base TEXT, value TEXT NOT NULL,
        UNIQUE(revision_id, role, position))""",
    """CREATE TABLE revision_tags (
        revision_id INTEGER NOT NULL REFERENCES entry_revisions(id),
        position INTEGER NOT NULL CHECK(position >= 0), term TEXT NOT NULL,
        scheme TEXT, label TEXT, PRIMARY KEY(revision_id, position))""",
    """CREATE TABLE content_extractions (
        id INTEGER PRIMARY KEY, block_id INTEGER NOT NULL REFERENCES content_blocks(id),
        extractor_version TEXT NOT NULL, config_json TEXT NOT NULL CHECK(json_valid(config_json)),
        recorded_at TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('completed','failed')),
        text TEXT, markdown TEXT, features_json TEXT CHECK(features_json IS NULL OR json_valid(features_json)),
        error TEXT,
        CHECK((status='completed' AND error IS NULL AND text IS NOT NULL AND markdown IS NOT NULL
               AND features_json IS NOT NULL) OR (status='failed' AND error IS NOT NULL)))""",
)

INDEXES = (
    "CREATE INDEX revisions_entry_sequence ON entry_revisions(entry_id, revision_number DESC)",
    "CREATE INDEX revisions_fingerprint ON entry_revisions(fingerprint)",
    "CREATE INDEX revisions_published ON entry_revisions(published_at)",
    "CREATE INDEX snapshots_feed_capture ON snapshots(feed_id, captured_at)",
    "CREATE INDEX observations_revision ON snapshot_entries(revision_id)",
    "CREATE INDEX observations_entry ON snapshot_entries(entry_id)",
    "CREATE INDEX tags_term_revision ON revision_tags(term, revision_id)",
    "CREATE INDEX blocks_revision ON content_blocks(revision_id)",
    "CREATE INDEX extractions_block_version ON content_extractions(block_id, extractor_version, id DESC)",
)

IMMUTABLE_TABLES = (
    "feeds",
    "entries",
    "snapshots",
    "entry_revisions",
    "snapshot_entries",
    "content_blocks",
    "revision_tags",
    "content_extractions",
)

VIEWS = (
    """CREATE VIEW entry_history AS
        SELECT r.id AS revision_id,r.entry_id,r.feed_id,
            f.publisher_id AS feed_identifier,e.publisher_id AS item_identifier,
            r.revision_number,r.previous_revision_id,r.source_snapshot_id,
            r.fingerprint,r.fingerprint_policy,r.feedparser_version,
            r.payload_json,r.entry_json,r.title,r.url,r.published_at,r.updated_at,
            r.comments_url,r.comments_feed_url,r.comment_count,r.wordpress_post_id,
            r.id_is_permalink,r.recorded_at,s.captured_at,s.source_uri,s.source_sha256,s.format,
            (SELECT MAX(o.snapshot_id) FROM snapshot_entries o WHERE o.revision_id=r.id)
                AS last_observed_snapshot_id
        FROM entry_revisions r JOIN entries e ON e.id=r.entry_id
        JOIN feeds f ON f.id=r.feed_id JOIN snapshots s ON s.id=r.source_snapshot_id""",
    """CREATE VIEW latest_entries AS
        SELECT h.*,s.captured_at AS last_observed_captured_at,
            s.recorded_at AS last_observed_recorded_at
        FROM entry_history h LEFT JOIN snapshots s ON s.id=h.last_observed_snapshot_id
        WHERE NOT EXISTS (SELECT 1 FROM entry_revisions newer
            WHERE newer.entry_id=h.entry_id AND newer.revision_number>h.revision_number)""",
    """CREATE INDEX extractions_success_latest ON content_extractions(block_id,id DESC)
        WHERE status='completed'""",
    """CREATE VIEW latest_entry_content AS
        SELECT e.*,b.id AS block_id,b.role,b.position,b.type,b.language,b.base,b.value,
            x.id AS extraction_id,x.extractor_version,x.config_json AS extraction_config_json,
            x.recorded_at AS extracted_at,x.text,x.markdown,x.features_json
        FROM latest_entries e LEFT JOIN content_blocks b ON b.revision_id=e.revision_id
        LEFT JOIN content_extractions x ON x.id=(SELECT MAX(candidate.id)
            FROM content_extractions candidate
            WHERE candidate.block_id=b.id AND candidate.status='completed')""",
)


def upgrade_schema(conn: sqlite3.Connection, current: int) -> None:
    if current == 1:
        for statement in VIEWS:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations VALUES (2, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )


def create_schema(conn: sqlite3.Connection) -> None:
    """Apply inside the caller's transaction; never use executescript's implicit commit."""
    if not conn.in_transaction:
        raise SchemaError("Schema creation requires a transaction")
    for statement in (*TABLES, *INDEXES):
        conn.execute(statement)
    conn.execute("""CREATE TRIGGER revision_sequence BEFORE INSERT ON entry_revisions
        WHEN NEW.revision_number != COALESCE((SELECT MAX(revision_number)+1 FROM entry_revisions
             WHERE entry_id=NEW.entry_id), 1)
          OR NEW.previous_revision_id IS NOT (SELECT id FROM entry_revisions
             WHERE entry_id=NEW.entry_id ORDER BY revision_number DESC LIMIT 1)
        BEGIN SELECT RAISE(ABORT, 'revision must follow latest item state'); END""")
    for table in IMMUTABLE_TABLES:
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER immutable_{table}_{action.lower()}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT, 'append-only history cannot be modified'); END""")
    conn.execute(
        "INSERT INTO schema_migrations VALUES (?, ?)",
        (1, datetime.now(timezone.utc).isoformat()),
    )
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    upgrade_schema(conn, 1)


def initialized(conn: sqlite3.Connection) -> bool:
    return conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID


def initialize(conn: sqlite3.Connection) -> None:
    """Initialize fresh DBs; legacy conversion must explicitly preserve their tables."""
    if conn.in_transaction:
        raise SchemaError("Cannot initialize inside another transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if initialized(conn):
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if version not in (1, SCHEMA_VERSION):
                raise SchemaError(f"Unsupported database schema version: {version}")
            upgrade_schema(conn, version)
        elif table_names(conn):
            raise SchemaError("Existing database requires explicit legacy migration")
        else:
            create_schema(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
