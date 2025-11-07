import sys
from pathlib import Path
import click
import platformdirs
import sqlite_utils
from rich.console import Console
from loguru import logger
from feed_to_sqlite.ingest import get_feeds_table
from .logconfig import logging_config, LOGURU_LEVEL_NAMES

@click.group()
@click.option(
    "--log-level",
    "log_level",
    type=click.Choice(LOGURU_LEVEL_NAMES, case_sensitive=False),
    default="INFO",
    show_default=True,
    help="Set the log level for the command.",
)
@click.option(
    "--log-file",
    "log_file",
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    default=None,
    help="Path to a file for logging.",
)
@click.pass_context
def cli(ctx, log_level, log_file):
    """A command-line tool for interacting with the prompthound package."""
    ctx.ensure_object(dict)

    # Set up logging
    logging_config(log_level=log_level.upper(), log_file=log_file)
    logger.info(f"Log level set to {log_level}")

    # Set up console
    console = Console(file=sys.stderr)

    ctx.obj["LOGGER"] = logger
    ctx.obj["CONSOLE"] = console


@cli.command()
@click.pass_context
def main(ctx):
    """The main entry point for the prompthound CLI."""
    console = ctx.obj["CONSOLE"]
    logger = ctx.obj["LOGGER"]

    console.print("Hello from prompthound CLI!", style="bold green")
    logger.info("CLI command executed successfully.")


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize the prompthound database."""
    console = ctx.obj["CONSOLE"]
    logger = ctx.obj["LOGGER"]

    app_dir = Path(platformdirs.user_data_dir("dev.pirateninja.prompthound", "pirateninja.dev"))
    app_dir.mkdir(parents=True, exist_ok=True)
    db_path = app_dir / "prompthound.db"

    console.print(f"Database path: {db_path}")
    db = sqlite_utils.Database(db_path)
    get_feeds_table(db)
    console.print("Database initialized successfully.", style="bold green")


if __name__ == "__main__":
    cli()
