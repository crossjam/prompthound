import copy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from prompthound.models import Entry, Snapshot
from prompthound.parsing import (
    FeedError,
    entry_from_fields,
    fingerprint,
    parse_snapshot,
)


RSS = b"""<rss version="2.0"><channel><title>Feed</title><link>https://example.com</link>
<description>Example</description><item><guid isPermaLink="false">opaque:one</guid>
<title>One</title><link>https://example.com/post</link><pubDate>Tue, 20 Oct 2026 12:00:00 +0200</pubDate>
<description><![CDATA[<p>A <em>body</em></p>]]></description></item></channel></rss>"""


def test_parsed_models_roundtrip_preserve_identity_and_utc():
    snapshot = parse_snapshot(RSS, source_uri="memory:test")
    entry = snapshot.entries[0]
    assert entry.id == "opaque:one"
    assert entry.url == "https://example.com/post"
    assert entry.id_is_permalink is False
    assert entry.published == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    assert entry.updated is None
    assert snapshot.captured_at is None
    assert Snapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_unfamiliar_nested_metadata_is_preserved():
    fields = {
        "id": "x",
        "authors": [{"name": "A", "extension": "keep"}],
        "links": [{"href": "mailto:a@example.com", "custom": [1, 2]}],
        "tags": [{"term": "tag", "extension": {"value": 3}}],
        "extra": {"new": True},
    }
    entry = entry_from_fields(fields, "feed")
    assert entry.authors[0].name == "A"
    assert entry.parser_fields == fields
    assert Entry.model_validate_json(entry.model_dump_json()) == entry


def test_fingerprint_ignores_dictionary_key_order_but_tracks_metadata_and_parser():
    first = entry_from_fields({"id": "x", "custom": {"a": 1, "b": 2}}, "feed")
    reordered = entry_from_fields({"custom": {"b": 2, "a": 1}, "id": "x"}, "feed")
    assert fingerprint(first, "v1") == fingerprint(reordered, "v1")
    changed = first.model_copy(deep=True)
    changed.parser_fields["slash_comments"] = "1"
    assert fingerprint(changed, "v1") != fingerprint(first, "v1")
    assert fingerprint(first, "v2") != fingerprint(first, "v1")


def test_source_identity_is_separate_from_item_fingerprint():
    first = parse_snapshot(RSS, source_uri="file:///snapshot-1.rss")
    second = parse_snapshot(RSS, source_uri="file:///snapshot-2.rss")
    assert first.capture_key != second.capture_key
    assert fingerprint(first.entries[0], first.feedparser_version) == fingerprint(
        second.entries[0], second.feedparser_version
    )
    assert (
        parse_snapshot(RSS, source_uri="stdin").capture_key
        == parse_snapshot(RSS, source_uri="stdin").capture_key
    )
    assert (
        parse_snapshot(RSS, source_uri="stdin", capture_id="second").capture_key
        == "second"
    )


def test_duplicate_ids_remain_ordered_and_nullable_metadata_is_valid():
    duplicated = RSS.replace(
        b"</channel>",
        b'<item><guid isPermaLink="false">opaque:one</guid><title>Another</title></item></channel>',
    )
    snapshot = parse_snapshot(duplicated, source_uri="test")
    assert [entry.id for entry in snapshot.entries] == ["opaque:one", "opaque:one"]
    assert snapshot.entries[1].published is None
    assert snapshot.entries[1].url is None


def test_url_fallback_and_missing_identity():
    assert (
        entry_from_fields({"link": "https://example.com"}, "feed").id
        == "https://example.com"
    )
    with pytest.raises(FeedError, match="neither"):
        entry_from_fields({"title": "No identity"}, "feed")


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not a feed",
        b"<rss>",
        b'<rss version="2.0"><channel><title>Empty</title></channel></rss>',
    ],
)
def test_unusable_feeds_fail_explicitly(content):
    with pytest.raises(FeedError):
        parse_snapshot(content, source_uri="test")


def test_capture_time_only_comes_from_explicit_provenance():
    snapshot = parse_snapshot(
        RSS, source_uri="pluralistic_rss-2026-10-01T04:55:01+00:00.rss"
    )
    assert snapshot.captured_at == datetime(2026, 10, 1, 4, 55, 1, tzinfo=timezone.utc)
    encoded = parse_snapshot(
        RSS, source_uri="file:///pluralistic_rss-2026-10-01T04%3A55%3A01%2B00%3A00.rss"
    )
    assert encoded.captured_at == snapshot.captured_at
    with pytest.raises(FeedError, match="timezone"):
        parse_snapshot(RSS, source_uri="test", captured_at=datetime(2026, 10, 1))


def test_revisions_can_recur_and_validate_aware_dates():
    entry = entry_from_fields({"id": "x", "title": "A"}, "feed")
    middle = copy.deepcopy(entry)
    middle.parser_fields["title"] = "B"
    assert fingerprint(entry, "v1") != fingerprint(middle, "v1")
    assert fingerprint(entry, "v1") == fingerprint(copy.deepcopy(entry), "v1")
    with pytest.raises(ValidationError):
        Entry(feed_id="feed", id="x", published=datetime(2026, 10, 1))
