from click.testing import CliRunner
from prompthound.cli import cli

def test_cli_main():
    """Test the main CLI command."""
    runner = CliRunner()
    result = runner.invoke(cli, ['main'])
    assert result.exit_code == 0
    assert "Hello from prompthound CLI!" in result.output
