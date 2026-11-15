"""Local RSS/Atom parsing, source identity, and versioned item fingerprints."""

import calendar
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import gzip
import json
import re
from typing import Any, TypeVar
from urllib.parse import unquote

import feedparser
from pydantic import BaseModel

from .models import (
    ContentBlock,
    Entry,
    Feed,
    Link,
    Person,
    Snapshot,
    Tag,
    TextConstruct,
)

FINGERPRINT_POLICY = "feedparser-entry-json-v1"
PARSE_POLICY = "feedparser-default-v1"
T = TypeVar("T", bound=BaseModel)


class FeedError(ValueError):
    """Input cannot be represented as a usable feed snapshot."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def fingerprint(entry: Entry, parser_version: str) -> str:
    envelope = {
        "policy": FINGERPRINT_POLICY,
        "feedparser_version": parser_version,
        "entry": entry.parser_fields,
    }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def project(model: type[T], value: Mapping[str, Any]) -> T:
    """Unknown nested fields remain available in the parent parser_fields copy."""
    return model.model_validate(
        {key: val for key, val in value.items() if key in model.model_fields}
    )


def parsed_date(fields: Mapping[str, Any], name: str) -> datetime | None:
    # Membership checks avoid feedparser's deprecated updated -> published alias.
    key = name + "_parsed"
    if key not in fields or not fields[key]:
        return None
    return datetime.fromtimestamp(calendar.timegm(fields[key]), timezone.utc)


def construct(fields: Mapping[str, Any], name: str) -> TextConstruct | None:
    if name not in fields or fields[name] is None:
        return None
    detail = dict(fields.get(name + "_detail") or {})
    detail.setdefault("value", fields[name])
    return project(TextConstruct, detail)


def entry_from_fields(fields: Mapping[str, Any], feed_id: str) -> Entry:
    item_id = fields.get("id") or fields.get("link")
    if not item_id:
        raise FeedError("Feed item has neither a publisher ID nor a link")
    raw = json.loads(canonical_json(fields))
    summary = construct(fields, "summary")
    authors = fields.get("authors") or (
        [fields["author_detail"]] if fields.get("author_detail") else []
    )
    if not authors and fields.get("author"):
        authors = [{"name": fields["author"]}]
    return Entry(
        feed_id=feed_id,
        id=item_id,
        id_is_permalink=fields.get("guidislink"),
        url=fields.get("link"),
        title=construct(fields, "title"),
        authors=[project(Person, value) for value in authors],
        tags=[project(Tag, value) for value in fields.get("tags", [])],
        links=[project(Link, value) for value in fields.get("links", [])],
        published=parsed_date(fields, "published"),
        updated=parsed_date(fields, "updated"),
        summary=ContentBlock(**summary.model_dump()) if summary else None,
        content=[project(ContentBlock, value) for value in fields.get("content", [])],
        comments_url=fields.get("comments"),
        comments_feed_url=fields.get("wfw_commentrss"),
        comment_count=fields.get("slash_comments"),
        wordpress_post_id=fields.get("post-id"),
        parser_fields=raw,
    )


def captured_time(source_uri: str) -> datetime | None:
    """Recognize an explicit timezone-bearing snapshot filename, never mtime/pubDate."""
    match = re.search(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2}))",
        unquote(source_uri),
    )
    if not match:
        return None
    return datetime.fromisoformat(match[1].replace("Z", "+00:00"))


def parse_snapshot(
    content: bytes,
    *,
    source_uri: str,
    capture_id: str | None = None,
    captured_at: datetime | None = None,
) -> Snapshot:
    """Parse bytes once without fetching URLs. Preserve duplicate IDs in source order.

    Default replay identity combines source URI, bytes, and known capture time.
    Identical stdin bytes replay unless the caller supplies a distinct capture_id.
    Parser upgrades reuse capture identity but create a separate parse of it in SQL.
    """
    if not content.strip():
        raise FeedError("Feed input is empty")
    try:
        xml = gzip.decompress(content) if content.startswith(b"\x1f\x8b") else content
    except (OSError, EOFError) as exc:
        raise FeedError(f"Invalid gzip feed: {exc}") from exc
    parsed = feedparser.parse(xml)
    if parsed.bozo:
        raise FeedError(
            f"Malformed feed: {parsed.get('bozo_exception', 'parse error')}"
        )
    if not parsed.version:
        raise FeedError("Input is not a recognized RSS or Atom feed")
    if not parsed.entries:
        raise FeedError("Feed contains no items")
    fields = parsed.feed
    self_link = next(
        (link["href"] for link in fields.get("links", []) if link.get("rel") == "self"),
        None,
    )
    feed_id = fields.get("id") or self_link or fields.get("link")
    if not feed_id:
        raise FeedError("Feed has no ID, self link, or site link")
    feed = Feed(
        id=feed_id,
        source_id=fields.get("id"),
        url=fields.get("link"),
        title=construct(fields, "title"),
        subtitle=construct(fields, "subtitle"),
        language=fields.get("language"),
        updated=parsed_date(fields, "updated"),
        links=[project(Link, value) for value in fields.get("links", [])],
        parser_fields=json.loads(canonical_json(fields)),
    )
    source_sha = hashlib.sha256(content).hexdigest()
    capture_time = captured_at if captured_at is not None else captured_time(source_uri)
    if capture_time is not None and capture_time.tzinfo is None:
        raise FeedError("Capture time must include a timezone")
    identity = {
        "source_uri": source_uri,
        "sha256": source_sha,
        "captured_at": capture_time.isoformat() if capture_time else None,
    }
    return Snapshot(
        feed=feed,
        entries=[entry_from_fields(entry, feed_id) for entry in parsed.entries],
        source_uri=source_uri,
        source_sha256=source_sha,
        capture_key=capture_id
        if capture_id is not None
        else hashlib.sha256(canonical_json(identity).encode()).hexdigest(),
        captured_at=capture_time,
        feedparser_version=feedparser.__version__,
        parse_policy=PARSE_POLICY,
        format=parsed.version,
        encoding=parsed.get("encoding"),
        namespaces=parsed.get("namespaces", {}),
    )
