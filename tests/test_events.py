import pytest

from prompthound.events import Counts, Event, Results, emit


def test_provisional_and_rolled_back_work_never_counts_as_persisted():
    results = Results()
    for event in [
        Event("persist", "started", total=10),
        Event("persist", "progress", completed=5, total=10),
        Event("persist", "rolled_back"),
        Event("input", "failed"),
    ]:
        results.consume(event)
    assert results.files == results.failed_files == 1
    assert results.new_items == results.revisions == results.observations == 0
    with pytest.raises(ValueError, match="after commit"):
        Event("persist", "progress", counts=Counts(new_items=1))


def test_committed_changes_and_replay_are_accounted_separately():
    results = Results()
    results.consume(
        Event(
            "persist",
            "committed",
            counts=Counts(new_items=2, revisions=3, observations=5, unchanged=2),
        )
    )
    results.consume(Event("input", "completed"))
    results.consume(Event("persist", "replayed"))
    results.consume(Event("input", "replayed"))
    assert results.files == 2
    assert results.replayed_files == 1
    assert (
        results.new_items,
        results.revisions,
        results.observations,
        results.unchanged,
    ) == (2, 3, 5, 2)


def test_unknown_totals_recording_callbacks_and_noop_are_silent(capsys):
    recorded = []
    emit(recorded.append, "read", "started", source="stdin")
    emit(recorded.append, "validate", "progress", completed=2, total=3)
    emit(None, "input", "failed", message="No terminal needed")
    assert recorded[0].total is None
    assert recorded[1].completed == 2
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_extraction_failure_and_interruption_preserve_commit_counts():
    results = Results()
    results.consume(
        Event("persist", "committed", counts=Counts(revisions=1, observations=1))
    )
    results.consume(Event("extract", "completed"))
    results.consume(Event("extract", "failed"))
    results.consume(Event("input", "interrupted"))
    assert results.revisions == 1
    assert results.extracted == results.failed_extractions == 1
    assert results.interrupted
    assert results.files == 1
    assert results.failed_files == results.replayed_files == 0


@pytest.mark.parametrize("kwargs", [{"completed": -1}, {"completed": 2, "total": 1}])
def test_progress_totals_are_consistent(kwargs):
    with pytest.raises(ValueError, match="count"):
        Event("migrate", "progress", **kwargs)
