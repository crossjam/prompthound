from click.testing import CliRunner
from prompthound.cli import cli
import pytest
from pathlib import Path
import sqlite_utils


def test_cli_main():
    """Test the main CLI command."""
    runner = CliRunner()
    result = runner.invoke(cli, ['main'])
    assert result.exit_code == 0
    assert "Hello from prompthound CLI!" in result.output


def test_cli_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test the init CLI command."""

    def mock_user_data_dir(*args, **kwargs):
        return str(tmp_path)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ['init'])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert db_path.exists()

    db = sqlite_utils.Database(db_path)
    assert "feeds" in db.table_names()

    assert "Database initialized successfully." in result.output


def test_cli_init_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test the init CLI command with --dry-run."""

    def mock_user_data_dir(*args, **kwargs):
        return str(tmp_path)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ['init', '--dry-run'])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert not db_path.exists()
    assert "DRY RUN" in result.output
