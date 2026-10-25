"""Regression coverage for custom database initialization and batch failures."""

import sqlite3
from pathlib import Path

import pytest
import sqlite_utils
from click.testing import CliRunner

from prompthound.cli import cli
from prompthound.vendor.feed_to_sqlite.ingest import ingest_feed


@pytest.fixture(autouse=True)
def isolate_default_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "platformdirs.user_data_dir", lambda *args: str(tmp_path / "default")
    )


@pytest.fixture
def feed_file(tmp_path: Path) -> Path:
    path = tmp_path / "valid.rss"
    path.write_text(
        '<rss version="2.0"><channel><title>Valid feed</title>'
        "<link>https://example.com/</link><description>A feed</description>"
        "<item><title>An entry</title><link>https://example.com/post</link>"
        "<guid>post-1</guid></item></channel></rss>",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("default_exists", [False, True])
def test_custom_database_initialization_ignores_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feed_file: Path,
    default_exists: bool,
):
    default_db = tmp_path / "default" / "prompthound.db"
    monkeypatch.setattr(
        "platformdirs.user_data_dir", lambda *args: str(default_db.parent)
    )
    if default_exists:
        default_db.parent.mkdir()
        database = sqlite_utils.Database(default_db)
        database["sentinel"].insert({"value": "preserve"})
        database.conn.close()
    original_bytes = default_db.read_bytes() if default_exists else None
    custom_db = tmp_path / "nested" / "custom" / "feeds.db"

    result = CliRunner().invoke(cli, ["ingest", "--db", str(custom_db), str(feed_file)])

    assert result.exit_code == 0, result.output
    assert "Overwrite?" not in result.output
    database = sqlite_utils.Database(custom_db)
    assert database["feeds"].count == 1
    database.conn.close()
    if default_exists:
        assert default_db.read_bytes() == original_bytes
    else:
        assert not default_db.parent.exists()


@pytest.mark.parametrize("bad_input", ["decoding", "missing-metadata"])
def test_failed_input_exits_nonzero_and_continues_batch(
    tmp_path: Path,
    feed_file: Path,
    bad_input: str,
):
    bad_file = tmp_path / "bad.rss"
    if bad_input == "decoding":
        bad_file.write_bytes(b"\xff\xfe")
    else:
        # A parsed item without channel link/id cannot be persisted by the ingester.
        bad_file.write_text(
            '<rss version="2.0"><channel><title>Bad feed</title>'
            "<item><title>Bad entry</title><link>https://bad.example/post</link>"
            "</item></channel></rss>",
            encoding="utf-8",
        )
    db_path = tmp_path / "feeds.db"
    result = CliRunner().invoke(
        cli,
        ["ingest", "--db", str(db_path), str(bad_file), str(feed_file)],
    )

    assert result.exit_code == 1, result.output
    assert "Ingestion complete." not in result.output
    assert "1 of 2 inputs" in result.output
    assert str(bad_file) in result.output
    database = sqlite_utils.Database(db_path)
    assert database["feeds"].count == 1
    assert next(database["feeds"].rows)["title"] == "Valid feed"
    database.conn.close()


def test_database_failure_exits_nonzero_and_continues_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feed_file: Path,
):
    bad_file = tmp_path / "database-error.rss"
    bad_file.write_text(feed_file.read_text().replace("Valid feed", "Broken feed"))

    def ingest_with_database_failure(db, *, feed_content):
        if "Broken feed" in feed_content:
            raise sqlite3.OperationalError("simulated database write failure")
        return ingest_feed(db, feed_content=feed_content)

    monkeypatch.setattr("prompthound.cli.ingest_feed", ingest_with_database_failure)
    db_path = tmp_path / "feeds.db"
    result = CliRunner().invoke(
        cli,
        ["ingest", "--db", str(db_path), str(bad_file), str(feed_file)],
    )

    assert result.exit_code == 1, result.output
    assert "Ingestion complete." not in result.output
    assert "1 of 2 inputs" in result.output
    database = sqlite_utils.Database(db_path)
    assert database["feeds"].count == 1
    database.conn.close()


def test_all_failed_inputs_are_reported(tmp_path: Path):
    files = [tmp_path / "bad-1.rss", tmp_path / "bad-2.rss"]
    for path in files:
        path.write_bytes(b"\xff\xfe")
    result = CliRunner().invoke(
        cli,
        ["ingest", "--db", str(tmp_path / "feeds.db"), *map(str, files)],
    )

    assert result.exit_code == 1, result.output
    assert "Ingestion complete." not in result.output
    assert "2 of 2 inputs" in result.output


def test_link_only_entries_are_persisted_once(tmp_path: Path, feed_file: Path):
    feed_file.write_text(feed_file.read_text().replace("<guid>post-1</guid>", ""))
    db_path = tmp_path / "feeds.db"
    arguments = ["ingest", "--db", str(db_path), str(feed_file)]

    for _ in range(2):
        result = CliRunner().invoke(cli, arguments)
        assert result.exit_code == 0, result.output

    database = sqlite_utils.Database(db_path)
    entry_tables = [name for name in database.table_names() if name != "feeds"]
    assert len(entry_tables) == 1
    entries = database[entry_tables[0]]
    assert entries.count == 1
    assert next(entries.rows)["id"] == "https://example.com/post"
    database.conn.close()
