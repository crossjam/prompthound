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

    non_existent_dir = tmp_path / "non_existent"
    def mock_user_data_dir(*args, **kwargs):
        return str(non_existent_dir)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ['init', '--dry-run'])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert not db_path.exists()
    assert "Dry Run Mode" in result.output
    assert "Directory would be created" in result.output
    assert "Database would be created" in result.output


def test_cli_init_dry_run_dir_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Test the init CLI command with --dry-run when the directory already exists."""

    def mock_user_data_dir(*args, **kwargs):
        return str(tmp_path)

    monkeypatch.setattr("platformdirs.user_data_dir", mock_user_data_dir)

    runner = CliRunner()
    result = runner.invoke(cli, ['init', '--dry-run'])
    assert result.exit_code == 0
    db_path = tmp_path / "prompthound.db"
    assert not db_path.exists()
    assert "Directory exists" in result.output
    assert "Database would be created" in result.output
