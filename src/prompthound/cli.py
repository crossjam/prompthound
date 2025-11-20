import sys
from pathlib import Path
import click
import platformdirs
import sqlite_utils
from rich.console import Console
from loguru import logger
from .vendor.feed_to_sqlite.ingest import get_feeds_table, ingest_feed


from .logconfig import logging_config, LOGURU_LEVEL_NAMES


@click.group()
@click.option(
    "--log-level",
    "log_level",
    type=click.Choice(LOGURU_LEVEL_NAMES, case_sensitive=False),
    default="WARNING",
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
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what actions would be taken without making changes.",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    default=None,
    help="Path to the SQLite database file.",
)
@click.pass_context
def init(ctx, dry_run, db_path):
    """Initialize the prompthound database."""
    console = ctx.obj["CONSOLE"]
    logger = ctx.obj["LOGGER"]

    if not db_path:
        app_dir = Path(
            platformdirs.user_data_dir("dev.pirateninja.prompthound", "pirateninja.dev")
        )
        db_path = app_dir / "prompthound.db"
    else:
        db_path = Path(db_path)

    app_dir = db_path.parent

    if not dry_run and db_path.exists():
        if not click.confirm(f"Database already exists at {db_path}. Overwrite?"):
            console.print("Initialization cancelled.", style="bold red")
            return

    if dry_run:
        console.print("[bold cyan]-- Dry Run Mode --[/bold cyan]")
        if app_dir.exists():
            console.print(f":white_check_mark: [green]Directory exists:[/] {app_dir}")
        else:
            console.print(
                f":deciduous_tree: [yellow]Directory would be created:[/] {app_dir}"
            )

        if db_path.exists():
            console.print(f":white_check_mark: [green]Database exists:[/] {db_path}")
        else:
            console.print(
                f":floppy_disk: [yellow]Database would be created:[/] {db_path}"
            )
        return

    app_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"Database path: {db_path}")
    db = sqlite_utils.Database(db_path)
    get_feeds_table(db)
    console.print("Database initialized successfully.", style="bold green")


@cli.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    default=None,
    help="Path to the SQLite database file.",
)
@click.argument("files", nargs=-1, type=click.File("r"))
@click.pass_context
def ingest(ctx, db_path, files):
    """Ingest feed data from files or stdin."""
    console = ctx.obj["CONSOLE"]
    logger = ctx.obj["LOGGER"]

    db_path = Path(db_path) if db_path else None

    if not db_path:
        app_dir = Path(
            platformdirs.user_data_dir("dev.pirateninja.prompthound", "pirateninja.dev")
        )
        db_path = app_dir / "prompthound.db"

    if not db_path.exists():
        console.print(f"Database not found at {db_path}. Initializing...", style="bold yellow")
        ctx.invoke(init)

    db = sqlite_utils.Database(db_path)

    if not files:
        files = [click.get_text_stream("stdin")]

    for file in files:
        try:
            console.print(f"Ingesting from {file.name}...", style="bold cyan")
            content = file.read()

            # ingest_feed automatically handles gzipped content if it detects it.
            ingest_feed(db, feed_content=content)

        except Exception as e:
            logger.error(f"An unexpected error occurred while processing {file.name}: {e}")
            console.print(
                f"An unexpected error occurred while processing {file.name}.",
                style="bold red",
            )

    console.print("Ingestion complete.", style="bold green")


if __name__ == "__main__":
    cli()
