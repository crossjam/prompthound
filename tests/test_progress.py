from io import StringIO

import pytest
from rich.console import Console

from prompthound.events import Counts, Event
from prompthound.progress import CLIProgress


def test_unknown_input_totals_switch_to_discovered_items_without_committing_counts():
    output = StringIO()
    console = Console(file=output, force_terminal=True)
    renderer = CLIProgress(console, total_files=1)
    renderer.display.auto_refresh = False
    with renderer:
        renderer(Event("read", "started", source="stdin"))
        assert renderer.display.tasks[renderer.stage_task].total is None
        renderer(Event("persist", "started", source="stdin", total=3))
        renderer(Event("persist", "progress", source="stdin", completed=2, total=3))
        assert renderer.display.tasks[renderer.stage_task].completed == 2
        assert renderer.results.revisions == 0
        renderer(Event("persist", "rolled_back", source="stdin"))
        renderer(Event("input", "failed", source="stdin"))
        assert renderer.display.tasks[renderer.files_task].completed == 1
    renderer.summary()
    assert "Ingestion finished with errors." in output.getvalue()
    assert renderer.results.revisions == 0


@pytest.mark.parametrize("terminal,no_progress", [(False, False), (True, True)])
def test_redirected_or_disabled_progress_is_plain_and_keeps_stdout_clean(
    capsys, terminal, no_progress
):
    output = StringIO()
    console = Console(file=output, force_terminal=terminal)
    with CLIProgress(console, total_files=2, no_progress=no_progress) as renderer:
        renderer(Event("input", "started", source="a[red].rss"))
        renderer(
            Event(
                "persist",
                "committed",
                counts=Counts(new_items=1, revisions=1, observations=2, unchanged=1),
            )
        )
        renderer(Event("input", "completed"))
        renderer(Event("input", "replayed"))
    renderer.summary()
    assert "\x1b" not in output.getvalue()
    assert "1 new items, 1 revisions, 1 unchanged observations" in output.getvalue()
    assert "1 replayed" in output.getvalue()
    assert capsys.readouterr().out == ""


def test_extraction_errors_and_pending_work_do_not_erase_committed_changes():
    output = StringIO()
    with CLIProgress(Console(file=output, force_terminal=False)) as renderer:
        renderer(
            Event("persist", "committed", counts=Counts(revisions=1, observations=1))
        )
        renderer(Event("extract", "failed", source="input", total=1, completed=1))
        renderer(Event("input", "completed"))
    renderer.summary(pending_extractions=1)
    assert "Ingestion finished with errors." in output.getvalue()
    assert "1 revisions" in output.getvalue()
    assert "1 failed extractions, 1 pending extractions" in output.getvalue()


def test_interrupt_always_stops_the_live_display():
    output = StringIO()
    renderer = CLIProgress(Console(file=output, force_terminal=True))
    renderer.display.auto_refresh = False
    with pytest.raises(KeyboardInterrupt):
        with renderer:
            renderer(Event("migrate", "started"))
            renderer(Event("input", "interrupted", source="current.rss"))
            assert renderer.display.tasks[renderer.files_task].completed == 1
            raise KeyboardInterrupt
    assert renderer.results.interrupted
    assert not renderer.display.live.is_started
    renderer.summary()
    assert "Ingestion interrupted." in output.getvalue()
