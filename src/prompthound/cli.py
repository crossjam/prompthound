import sys
from datetime import datetime
from pathlib import Path
import click
import platformdirs
from rich.console import Console
from loguru import logger
from .events import Event
from .extraction import extract_pending
from .ingestion import ingest_source, pending_extractions
from .migration import prepare_database
from .progress import CLIProgress
from .storage import Store
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

    console = Console(file=sys.stderr)
    logging_config(log_level=log_level.upper(), log_file=log_file, console=console)
    logger.info(f"Log level set to {log_level}")

    ctx.obj["LOGGER"] = logger
    ctx.obj["CONSOLE"] = console


def database_path(value):
    if value:
        return Path(value)
    return (
        Path(
            platformdirs.user_data_dir("dev.pirateninja.prompthound", "pirateninja.dev")
        )
        / "prompthound.db"
    )


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
@click.option("--no-progress", is_flag=True, help="Disable live progress indicators.")
def init(ctx, dry_run, db_path, no_progress):
    """Initialize or migrate the database, preserving all existing data."""
    console = ctx.obj["CONSOLE"]
    db_path = database_path(db_path)
    app_dir = db_path.parent

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

    try:
        app_dir.mkdir(parents=True, exist_ok=True)
        with CLIProgress(console, no_progress=no_progress) as progress:
            prepare_database(db_path, progress=progress)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    console.print(f"Database path: {db_path}", markup=False)
    console.print("Database initialized successfully.", style="bold green")


@cli.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    default=None,
    help="Path to the SQLite database file.",
)
@click.option("--no-progress", is_flag=True, help="Disable live progress indicators.")
@click.option(
    "--capture-id",
    default=None,
    help="Explicit capture identity for one input; useful with stdin.",
)
@click.option(
    "--captured-at",
    default=None,
    help="Known capture time in ISO 8601 format with timezone.",
)
@click.argument("files", nargs=-1, type=click.Path(dir_okay=False, path_type=Path))
@click.pass_context
def ingest(ctx, db_path, files, no_progress, capture_id, captured_at):
    """Append feed snapshots from FILES or stdin (-), then extract HTML content."""
    console = ctx.obj["CONSOLE"]
    logger = ctx.obj["LOGGER"]
    inputs = files or (Path("-"),)
    if capture_id is not None and len(inputs) != 1:
        raise click.UsageError("--capture-id requires exactly one input")
    capture_time = None
    if captured_at is not None:
        try:
            capture_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if capture_time.tzinfo is None:
                raise ValueError("timezone is required")
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--captured-at") from exc
    db_path = database_path(db_path)
    progress = CLIProgress(console, total_files=len(inputs), no_progress=no_progress)
    pending = 0
    try:
        with progress:
            try:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                prepare_database(db_path, progress=progress)
            except Exception as exc:
                raise click.ClickException(
                    f"Unable to prepare database: {exc}"
                ) from exc
            with Store(db_path) as store:
                try:
                    for path in inputs:
                        source = "stdin" if str(path) == "-" else str(path)
                        progress(Event("input", "started", source=source))
                        try:
                            progress(Event("read", "started", source=source))
                            content = (
                                click.get_binary_stream("stdin").read()
                                if str(path) == "-"
                                else path.read_bytes()
                            )
                            result = ingest_source(
                                store,
                                content,
                                source_uri="stdin"
                                if str(path) == "-"
                                else path.resolve().as_uri(),
                                capture_id=capture_id,
                                captured_at=capture_time,
                                progress=progress,
                            )
                        except KeyboardInterrupt:
                            progress(Event("input", "interrupted", source=source))
                            raise
                        except Exception as exc:
                            logger.error(f"Failed processing {source}: {exc}")
                            progress(
                                Event(
                                    "input", "failed", source=source, message=str(exc)
                                )
                            )
                        else:
                            progress(
                                Event(
                                    "input",
                                    "replayed"
                                    if result.ingestion.replayed
                                    else "completed",
                                    source=source,
                                )
                            )
                finally:
                    pending = pending_extractions(store)
    except KeyboardInterrupt:
        progress.summary(pending_extractions=pending)
        raise click.Abort() from None
    progress.summary(pending_extractions=pending)
    if progress.results.failed_files:
        raise click.ClickException(
            f"Ingestion failed for {progress.results.failed_files} of {len(inputs)} inputs."
        )
    if progress.results.failed_extractions:
        raise click.ClickException(
            "HTML extraction failed; source history was preserved. Retry with prompthound extract."
        )


@cli.command()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, exists=True, resolve_path=True),
    default=None,
)
@click.option("--no-progress", is_flag=True, help="Disable live progress indicators.")
@click.pass_context
def extract(ctx, db_path, no_progress):
    """Retry pending HTML extraction or process a newly installed extractor version."""
    console = ctx.obj["CONSOLE"]
    db_path = database_path(db_path)
    if not db_path.is_file():
        raise click.ClickException(f"Database not found: {db_path}")
    progress = CLIProgress(console, no_progress=no_progress)
    pending = 0
    try:
        with progress:
            prepare_database(db_path, progress=progress)
            with Store(db_path) as store:
                snapshots = list(
                    store.conn.execute(
                        "SELECT id,source_uri FROM snapshots ORDER BY id"
                    )
                )
                progress.display.update(progress.files_task, total=len(snapshots))
                try:
                    for snapshot in snapshots:
                        extract_pending(store, snapshot["id"], progress=progress)
                        progress(
                            Event("input", "completed", source=snapshot["source_uri"])
                        )
                finally:
                    pending = pending_extractions(store)
    except KeyboardInterrupt:
        progress.results.interrupted = True
        progress.summary(pending_extractions=pending, operation="Extraction")
        raise click.Abort() from None
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    progress.summary(pending_extractions=pending, operation="Extraction")
    if progress.results.failed_extractions:
        raise click.ClickException(
            "Some HTML extractions failed; source history was preserved."
        )


if __name__ == "__main__":
    cli()
