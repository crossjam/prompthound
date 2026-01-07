from click.testing import CliRunner
from prompthound.cli import cli
import pytest
from pathlib import Path
import sqlite_utils
import tarfile
import feedparser
from prompthound.vendor.feed_to_sqlite.ingest import slugify


def test_cli_main():
    """Test the main CLI command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["main"])
    assert result.exit_code == 0
    assert "Hello from prompthound CLI!" in result.output


def test_cli_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test the init CLI command."""

    def mock_user_data_dir(*args, **kwargs):
        return str(tmp_path)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert db_path.exists()

    db = sqlite_utils.Database(db_path)
    assert "feeds" in db.table_names()

    assert "Database initialized successfully." in result.output


def test_cli_init_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test the init CLI command with --dry-run."""

    non_existent_dir = tmp_path / "non_existent"

    def mock_user_data_dir(*args, **kwargs):
        return str(non_existent_dir)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--dry-run"])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert not db_path.exists()
    assert "Dry Run Mode" in result.output
    assert "Directory would be created" in result.output
    assert "Database would be created" in result.output



def test_cli_ingest(tmp_path: Path):
    """Test the ingest CLI command with a single file."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_content = """
    <rss version="2.0">
    <channel>
        <title>Test Feed</title>
        <link>http://example.com/</link>
        <description>A test feed for prompthound.</description>
        <item>
            <title>Test Entry 1</title>
            <link>http://example.com/1</link>
            <description>This is the first test entry.</description>
        </item>
    </channel>
    </rss>
    """
    xml_file = tmp_path / "test.xml"
    xml_file.write_text(xml_content)

    result = runner.invoke(cli, ["ingest", "--db", str(db_path), str(xml_file)])
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)
    assert "feeds" in db.table_names()
    assert db["feeds"].count == 1
    assert "Ingestion complete." in result.output


def test_cli_ingest_multiple_files(tmp_path: Path):
    """Test the ingest CLI command with multiple files."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_content1 = """
    <rss version="2.0">
    <channel>
        <title>Test Feed 1</title>
        <link>http://example.com/1</link>
        <description>A test feed for prompthound.</description>
        <item>
            <title>Test Entry 1</title>
            <link>http://example.com/1</link>
            <description>This is the first test entry.</description>
        </item>
    </channel>
    </rss>
    """
    xml_file1 = tmp_path / "test1.xml"
    xml_file1.write_text(xml_content1)

    xml_content2 = """
    <rss version="2.0">
    <channel>
        <title>Test Feed 2</title>
        <link>http://example.com/2</link>
        <description>A test feed for prompthound.</description>
        <item>
            <title>Test Entry 2</title>
            <link>http://example.com/2</link>
            <description>This is the second test entry.</description>
        </item>
    </channel>
    </rss>
    """
    xml_file2 = tmp_path / "test2.xml"
    xml_file2.write_text(xml_content2)

    result = runner.invoke(
        cli, ["ingest", "--db", str(db_path), str(xml_file1), str(xml_file2)]
    )
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)
    assert db["feeds"].count == 2


def test_cli_ingest_stdin(tmp_path: Path):
    """Test the ingest CLI command with stdin."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_content = """
    <rss version="2.0">
    <channel>
        <title>Test Feed</title>
        <link>http://example.com/</link>
        <description>A test feed for prompthound.</description>
        <item>
            <title>Test Entry 1</title>
            <link>http://example.com/1</link>
            <description>This is the first test entry.</description>
        </item>
    </channel>
    </rss>
    """

    result = runner.invoke(cli, ["ingest", "--db", str(db_path)], input=xml_content)
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)
    assert db["feeds"].count == 1


def test_cli_ingest_duplicate_entries(tmp_path: Path):
    """Test that duplicate entries are not added to the database."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_content = """
    <rss version="2.0">
    <channel>
        <title>Test Feed</title>
        <link>http://example.com/</link>
        <description>A test feed for prompthound.</description>
        <item>
            <title>Test Entry 1</title>
            <link>http://example.com/1</link>
            <description>This is the first test entry.</description>
        </item>
        <item>
            <title>Test Entry 1</title>
            <link>http://example.com/1</link>
            <description>This is the first test entry.</description>
        </item>
    </channel>
    </rss>
    """
    xml_file = tmp_path / "test.xml"
    xml_file.write_text(xml_content)

    result = runner.invoke(cli, ["ingest", "--db", str(db_path), str(xml_file)])
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)
    assert db["feeds"].count == 1


def test_cli_ingest_pluralistic_sample(tmp_path: Path):
    """Test the ingest CLI command with the Pluralistic_archive_sample.xml file."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_file = "tests/data/Pluralistic_archive_sample.xml"
    
    result = runner.invoke(cli, ["ingest", "--db", str(db_path), xml_file])
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)

    # Dynamically get the slugified table name
    with open(xml_file, "r") as f:
        feed_content = f.read()
    feed = feedparser.parse(feed_content)
    expected_table_name = slugify(feed.feed.title)

    assert expected_table_name in db.table_names()
    assert db[expected_table_name].count == 91


def test_cli_ingest_pluralistic_sample_lxml(tmp_path: Path):
    """Test the ingest CLI command with the Pluralistic_archive_sample_lxml.xml file."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    xml_file = "tests/data/Pluralistic_archive_sample_lxml.xml"

    result = runner.invoke(cli, ["ingest", "--db", str(db_path), xml_file])
    assert result.exit_code == 0

    db = sqlite_utils.Database(db_path)

    # Dynamically get the slugified table name
    with open(xml_file, "r") as f:
        feed_content = f.read()
    feed = feedparser.parse(feed_content)
    expected_table_name = slugify(feed.feed.title)

    assert expected_table_name in db.table_names()
    assert db[expected_table_name].count == 91


@pytest.mark.skip(reason="Temporarily skipping tar.gz archive test")
def test_cli_ingest_tar_gz_archive(tmp_path: Path):
    """Test the ingest CLI command with a tar.gz archive of RSS files."""
    runner = CliRunner()
    db_path = tmp_path / "test.db"
    archive_path = "tests/data/sample_rss.tar.gz"
    extract_path = tmp_path / "extracted_rss"
    extract_path.mkdir()

    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(path=extract_path)

    xml_files = [str(p) for p in extract_path.glob("**/*.xml")]

    # First, initialize the database
    init_result = runner.invoke(cli, ["init", "--db", str(db_path)])
    assert init_result.exit_code == 0

    result = runner.invoke(cli, ["ingest", "--db", str(db_path)] + xml_files)
    assert result.exit_code == 0

    # Re-initialize db object to reflect changes made by the CLI command
    db = sqlite_utils.Database(db_path)

    assert "feeds" in db.table_names()
    for xml_file_path in extract_path.glob("**/*.xml"):
        with open(xml_file_path, "r") as f:
            feed_content = f.read()
        feed = feedparser.parse(feed_content)
        expected_table_name = slugify(feed.feed.title)
        assert expected_table_name in db.table_names()
        assert db[expected_table_name].count > 0
