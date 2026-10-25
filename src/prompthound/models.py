"""Validated source records and separately versioned derived content."""

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


class ContentBlock(TextConstruct):
    """Feedparser-normalized source value, with no extraction-derived fields."""


class Feed(Model):
    id: str = Field(min_length=1)
    source_id: str | None = None
    url: str | None = None
    title: TextConstruct | None = None
    subtitle: TextConstruct | None = None
    language: str | None = None
    updated: AwareDatetime | None = None
    links: list[Link] = Field(default_factory=list)
    parser_fields: dict[str, JsonValue] = Field(default_factory=dict)


class Entry(Model):
    feed_id: str = Field(min_length=1)
    id: str = Field(min_length=1)
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
    parser_fields: dict[str, JsonValue] = Field(default_factory=dict)


class Snapshot(Model):
    feed: Feed
    entries: list[Entry]
    source_uri: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capture_key: str = Field(min_length=1)
    captured_at: AwareDatetime | None = None
    feedparser_version: str
    parse_policy: str = "feedparser-default-v1"
    format: str
    encoding: str | None = None
    namespaces: dict[str, str] = Field(default_factory=dict)
    bozo: bool = False
    parse_error: str | None = None


class EntryRevision(Model):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: int
    revision_number: int = Field(ge=1)
    previous_revision_id: int | None = None
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fingerprint_policy: str
    feedparser_version: str
    payload_json: str
    entry_json: str
    snapshot_id: int
    recorded_at: AwareDatetime


class HTMLLink(Model):
    href: str
    text: str = ""
    rel: list[str] = Field(default_factory=list)


class Image(Model):
    src: str | None = None
    alt: str | None = None
    title: str | None = None
    width: str | None = None
    height: str | None = None
    srcset: str | None = None


class Heading(Model):
    level: int = Field(ge=1, le=6)
    title: str
    anchor: str | None = None
    permalink: str | None = None


class Section(Model):
    position: int = Field(ge=0)
    title: str
    anchor: str | None = None
    kind: Literal[
        "essay", "links", "archive", "appearances", "books", "colophon", "other"
    ] = "other"
    html: str
    text: str
    markdown: str


class HTMLFeatures(Model):
    links: list[HTMLLink] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    headings: list[Heading] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    editorial: dict[str, str] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)


class ExtractedContent(Model):
    text: str
    markdown: str
    features: HTMLFeatures = Field(default_factory=HTMLFeatures)
