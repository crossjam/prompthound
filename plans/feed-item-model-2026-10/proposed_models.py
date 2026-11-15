"""Proposal only: normalized feed data plus separately derived HTML features.

These classes do not change the application's ingestion or SQLite schema.
"""

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Person(Model):
    name: str | None = None
    email: str | None = None
    href: str | None = None


class Link(Model):
    href: str
    rel: str | None = None
    type: str | None = None
    title: str | None = None
    hreflang: str | None = None
    length: str | int | None = None


class Tag(Model):
    term: str
    scheme: str | None = None
    label: str | None = None


class TextConstruct(Model):
    value: str
    type: str = "text/plain"
    language: str | None = None
    base: str | None = None


class HTMLLink(Model):
    href: str
    text: str = ""
    rel: str | None = None


class Image(Model):
    src: str | None = None
    alt: str | None = None
    title: str | None = None
    width: str | None = None
    height: str | None = None
    srcset: str | None = None


class SectionHeading(Model):
    """An outline; section bodies need a DOM-aware extraction step."""

    level: int = Field(ge=1, le=6)
    title: str
    permalink: str | None = None
    css_class: str | None = None


class EditorialMetadata(Model):
    """Publisher-specific comment metadata; retain source strings and comments."""

    tags: list[str] = Field(default_factory=list)
    summary: str | None = None
    url: str | None = None
    title: str | None = None
    bullet: str | None = None
    separator: str | None = None
    top_sources: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)


class HTMLFeatures(Model):
    links: list[HTMLLink] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    headings: list[SectionHeading] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    editorial: EditorialMetadata | None = None


class ContentBlock(TextConstruct):
    """value is feedparser's normalized content, not the original XML bytes."""

    text: str | None = None
    markdown: str | None = None
    html_features: HTMLFeatures | None = None


class Feed(Model):
    # Prefer RSS self-link as stable application identity; RSS channel lacks id here.
    id: str
    source_id: str | None = None
    url: str | None = None
    title: TextConstruct | None = None
    subtitle: TextConstruct | None = None
    language: str | None = None
    updated: AwareDatetime | None = None
    links: list[Link] = Field(default_factory=list)
    # Includes image, generator, syndication hints, site ID, raw date strings, etc.
    parser_fields: dict[str, JsonValue] = Field(default_factory=dict)


class Entry(Model):
    feed_id: str
    id: str  # Opaque publisher GUID, distinct from the article URL.
    id_is_permalink: bool | None = None
    url: str | None = None
    title: TextConstruct | None = None
    authors: list[Person] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    published: AwareDatetime | None = None
    updated: AwareDatetime | None = None
    summary: ContentBlock | None = None
    content: list[ContentBlock] = Field(default_factory=list)
    comments_url: str | None = None
    comments_feed_url: str | None = None
    comment_count: int | None = Field(default=None, ge=0)
    wordpress_post_id: int | None = Field(default=None, ge=0)
    # Preserve all feedparser fields, including namespaces and original date strings.
    parser_fields: dict[str, JsonValue] = Field(default_factory=dict)


FINGERPRINT_POLICY = "feedparser-entry-json-v1"


class EntryRevision(Model):
    """An immutable record to INSERT; never replace an earlier revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    feed_id: str
    entry_id: str
    revision_number: int = Field(ge=1)
    previous_revision_number: int | None = Field(default=None, ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fingerprint_policy: Literal["feedparser-entry-json-v1"] = FINGERPRINT_POLICY
    feedparser_version: str
    # JSON text keeps the stored payload immutable as well as reproducible.
    payload_json: str
    source_file: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: AwareDatetime
    recorded_at: AwareDatetime


def append_revision(
    latest: EntryRevision | None,
    *,
    feed_id: str,
    entry_id: str,
    parser_fields: dict[str, JsonValue],
    feedparser_version: str,
    source_file: str,
    source_sha256: str,
    captured_at: datetime,
    recorded_at: datetime,
) -> tuple[EntryRevision, bool]:
    """Prototype decision logic; a database must make compare-and-insert atomic.

    Compare only the latest revision. A -> B -> A must append three revisions.
    Capture metadata and derived HTML/text/Markdown are excluded from the hash.
    """
    if latest is not None and (latest.feed_id, latest.entry_id) != (feed_id, entry_id):
        raise ValueError("Latest revision belongs to another item")
    if (parser_fields.get("id") or parser_fields.get("link")) != entry_id:
        raise ValueError("Payload identity does not match the ledger item")
    payload_json = json.dumps(
        parser_fields,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    envelope = json.dumps(
        {
            "policy": FINGERPRINT_POLICY,
            "feedparser_version": feedparser_version,
            "entry": json.loads(payload_json),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    fingerprint = hashlib.sha256(envelope.encode("utf-8")).hexdigest()
    if latest is not None and latest.fingerprint == fingerprint:
        return latest, False
    return EntryRevision(
        feed_id=feed_id,
        entry_id=entry_id,
        revision_number=latest.revision_number + 1 if latest else 1,
        previous_revision_number=latest.revision_number if latest else None,
        fingerprint=fingerprint,
        feedparser_version=feedparser_version,
        payload_json=payload_json,
        source_file=source_file,
        source_sha256=source_sha256,
        captured_at=captured_at,
        recorded_at=recorded_at,
    ), True


class EntryObservation(Model):
    entry_id: str
    revision_number: int = Field(ge=1)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class Snapshot(Model):
    source_file: str
    source_sha256: str
    # Timestamp in filename is capture time, distinct from feed.updated/publication.
    captured_at: AwareDatetime
    feed_id: str
    feedparser_version: str
    parse_version: str = "proposal-1"
    html_policy: Literal["feedparser-default"] = "feedparser-default"
    format: str
    encoding: str | None = None
    namespaces: dict[str, str] = Field(default_factory=dict)
    bozo: bool
    parse_error: str | None = None
    observations: list[EntryObservation] = Field(default_factory=list)
