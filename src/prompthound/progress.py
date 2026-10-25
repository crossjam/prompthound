"""Rich rendering at the CLI boundary; source services remain terminal-independent."""

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from .events import Event, Results


class CLIProgress:
    def __init__(
        self,
        console: Console,
        *,
        total_files: int | None = None,
        no_progress: bool = False,
    ):
        self.console = console
        self.results = Results()
        self.enabled = console.is_terminal and not no_progress
        self.display = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            disable=not self.enabled,
            transient=True,
            refresh_per_second=4,
        )
        self.files_task = self.display.add_task("Inputs", total=total_files)
        self.stage_task = self.display.add_task("Waiting", total=None)

    def __enter__(self):
        if self.enabled:
            self.display.start()
        return self

    def __exit__(self, exc_type, *_):
        if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
            self.results.interrupted = True
        if self.enabled:
            self.display.stop()

    def __call__(self, event: Event) -> None:
        self.results.consume(event)
        if event.stage == "migrate" and event.outcome == "completed" and event.message:
            self.console.print(
                event.message, markup=False, highlight=False, soft_wrap=True
            )
        if event.stage == "extract" and event.outcome == "failed" and event.message:
            self.console.print(
                f"HTML extraction failed for {event.source}: {event.message}",
                markup=False,
                highlight=False,
                soft_wrap=True,
            )
        if not self.enabled:
            return
        if event.stage == "input" and event.outcome in {
            "completed",
            "failed",
            "replayed",
        }:
            self.display.update(
                self.files_task,
                completed=self.results.files,
                description=f"Inputs ({self.results.failed_files} failed, {self.results.replayed_files} replayed)",
            )
        label = f"{event.stage.capitalize()}: {escape(event.source)} ({event.outcome})"
        if event.outcome == "started":
            self.display.reset(
                self.stage_task,
                total=event.total,
                completed=event.completed,
                description=label,
            )
        else:
            self.display.update(
                self.stage_task,
                total=event.total,
                completed=event.completed,
                description=label,
            )

    def summary(
        self, *, pending_extractions: int = 0, operation: str = "Ingestion"
    ) -> None:
        result = self.results
        if result.interrupted:
            title = f"{operation} interrupted."
        elif result.failed_files or result.failed_extractions:
            title = f"{operation} finished with errors."
        else:
            title = f"{operation} complete."
        self.console.print(
            f"{title} {result.files} inputs; {result.new_items} new items, {result.revisions} revisions, "
            f"{result.unchanged} unchanged observations; {result.replayed_files} replayed, "
            f"{result.failed_files} failed inputs; {result.extracted} extracted, "
            f"{result.failed_extractions} failed extractions, {pending_extractions} pending extractions.",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
