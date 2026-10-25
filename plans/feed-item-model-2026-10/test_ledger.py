"""Behavioral checks for the proposed ledger, independent of the local dataset."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from proposed_models import append_revision


def observe(latest=None, *, title="A", feed_id="feed", fields=None, **kwargs):
    return append_revision(
        latest,
        feed_id=feed_id,
        entry_id="item",
        parser_fields=fields if fields is not None else {"id": "item", "title": title},
        feedparser_version=kwargs.pop("feedparser_version", "6.0.11"),
        source_file=kwargs.pop("source_file", "snapshot.rss"),
        source_sha256="a" * 64,
        captured_at=kwargs.pop(
            "captured_at", datetime(2026, 10, 1, tzinfo=timezone.utc)
        ),
        recorded_at=datetime(2026, 10, 24, tzinfo=timezone.utc),
        **kwargs,
    )


def test_first_observation_creates_revision_one():
    revision, appended = observe()
    assert appended
    assert revision.revision_number == 1
    assert revision.previous_revision_number is None
    assert len(revision.fingerprint) == 64
    with pytest.raises(ValidationError):
        revision.payload_json = "changed"


def test_identical_item_in_new_capture_reuses_latest_without_mutation():
    first, _ = observe()
    again, appended = observe(
        first,
        source_file="later.rss",
        captured_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    assert not appended
    assert again is first
    assert first.source_file == "snapshot.rss"


def test_reverting_to_an_earlier_payload_appends_another_revision():
    first, _ = observe(title="A")
    second, _ = observe(first, title="B")
    third, appended = observe(second, title="A")
    assert appended
    assert [first.revision_number, second.revision_number, third.revision_number] == [
        1,
        2,
        3,
    ]
    assert third.previous_revision_number == 2
    assert first.fingerprint == third.fingerprint != second.fingerprint


def test_dictionary_key_order_does_not_create_a_revision():
    first, _ = observe(fields={"id": "item", "extension": {"a": 1, "b": 2}})
    again, appended = observe(
        first, fields={"extension": {"b": 2, "a": 1}, "id": "item"}
    )
    assert not appended
    assert again is first


def test_comment_metadata_changes_create_revisions_without_html_changes():
    first, _ = observe(fields={"id": "item", "content": [], "slash_comments": "0"})
    second, appended = observe(
        first, fields={"id": "item", "content": [], "slash_comments": "1"}
    )
    assert appended
    assert second.revision_number == 2


def test_guid_is_scoped_to_feed_identity():
    first, _ = observe(feed_id="feed-a")
    other, appended = observe(feed_id="feed-b")
    assert appended
    assert first.revision_number == other.revision_number == 1
    with pytest.raises(ValueError, match="another item"):
        observe(first, feed_id="feed-b")


def test_payload_identity_must_match_the_ledger():
    with pytest.raises(ValueError, match="identity"):
        observe(fields={"id": "other"})


def test_parser_upgrade_is_an_explicit_representation_change():
    first, _ = observe()
    second, appended = observe(first, feedparser_version="next-version")
    assert appended
    assert second.feedparser_version != first.feedparser_version
