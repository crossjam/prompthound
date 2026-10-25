"""Terminal-independent workflow events and commit-aware result accounting."""

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Literal

Stage = Literal["input", "read", "parse", "validate", "persist", "extract", "migrate"]
Outcome = Literal[
    "started",
    "progress",
    "committed",
    "replayed",
    "completed",
    "failed",
    "rolled_back",
    "interrupted",
]


@dataclass(frozen=True)
class Counts:
    new_items: int = 0
    revisions: int = 0
    observations: int = 0
    unchanged: int = 0
    content_blocks: int = 0

    def __post_init__(self):
        if any(getattr(self, field.name) < 0 for field in fields(self)):
            raise ValueError("Committed counters cannot be negative")
        if self.unchanged > self.observations:
            raise ValueError("Unchanged observations exceed total observations")


@dataclass(frozen=True)
class Event:
    stage: Stage
    outcome: Outcome
    source: str = ""
    completed: int = 0
    total: int | None = None
    message: str = ""
    counts: Counts | None = None

    def __post_init__(self):
        if self.completed < 0 or (
            self.total is not None and self.total < self.completed
        ):
            raise ValueError("Invalid progress count")
        if self.counts is not None and (self.stage, self.outcome) != (
            "persist",
            "committed",
        ):
            raise ValueError("Persisted counters are only published after commit")


ProgressCallback = Callable[[Event], None]


def emit(
    callback: ProgressCallback | None, stage: Stage, outcome: Outcome, **kwargs
) -> None:
    """Default to no output; services never import a renderer or write to stdout."""
    if callback is not None:
        callback(Event(stage, outcome, **kwargs))


@dataclass
class Results:
    """Aggregate events without ever turning provisional progress into row counts."""

    files: int = 0
    failed_files: int = 0
    replayed_files: int = 0
    interrupted: bool = False
    new_items: int = 0
    revisions: int = 0
    observations: int = 0
    unchanged: int = 0
    extracted: int = 0
    failed_extractions: int = 0

    def consume(self, event: Event) -> None:
        if event.stage == "input" and event.outcome in {
            "completed",
            "failed",
            "replayed",
        }:
            self.files += 1
            self.failed_files += event.outcome == "failed"
            self.replayed_files += event.outcome == "replayed"
        if event.outcome == "interrupted":
            self.interrupted = True
        if event.stage == "persist" and event.outcome == "committed" and event.counts:
            for name in ("new_items", "revisions", "observations", "unchanged"):
                setattr(self, name, getattr(self, name) + getattr(event.counts, name))
        if event.stage == "extract":
            self.extracted += event.outcome == "completed"
            self.failed_extractions += event.outcome == "failed"
