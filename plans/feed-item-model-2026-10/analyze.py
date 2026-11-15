"""Reproduce the October sample and inspect feedparser metadata and HTML.

Run from the repository root: uv run python plans/feed-item-model-2026-10/analyze.py
Only the ten selected source files are opened; source data is never modified.
"""

import calendar
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import random
import re
import statistics
from urllib.parse import urljoin

import feedparser
import htmd

from proposed_models import (
    ContentBlock,
    Entry,
    EntryRevision,
    Feed,
    Snapshot,
    append_revision,
)


HERE = Path(__file__).resolve().parent
SOURCE = Path(__file__).resolve().parents[4] / "datasets/pluralistic_feed_snapshots"
SEED = 202610


class InspectHTML(HTMLParser):
    """Collect evidence, not a production article extractor or sanitizer."""

    def __init__(self, base=""):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.tags = Counter()
        self.comments = []
        self.headings = []
        self.links = []
        self.images = []
        self.anchors = []
        self.text = []
        self.heading = None
        self.link = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags[tag] += 1
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "div", "br", "li", "hr", "blockquote"} or re.fullmatch(
            r"h[1-6]", tag
        ):
            self.text.append("\n")
        if attrs.get("id") or (tag == "a" and attrs.get("name")):
            self.anchors.append(attrs.get("id") or attrs["name"])
        if re.fullmatch(r"h[1-6]", tag):
            self.heading = {
                "level": int(tag[1]),
                "text": "",
                "class": attrs.get("class"),
                "permalink": None,
            }
        if tag == "a" and attrs.get("href"):
            self.link = {
                "href": urljoin(self.base, attrs["href"]),
                "text": "",
                "rel": attrs.get("rel"),
            }
            self.links.append(self.link)
            if self.heading is not None:
                self.heading["permalink"] = self.link["href"]
        if tag == "img":
            self.images.append(
                {
                    k: attrs.get(k)
                    for k in ("src", "alt", "title", "width", "height", "srcset")
                }
            )

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a":
            self.link = None
        if re.fullmatch(r"h[1-6]", tag) and self.heading is not None:
            self.heading["text"] = " ".join(self.heading["text"].split())
            self.headings.append(self.heading)
            self.heading = None
        if tag in {"p", "div", "li", "blockquote"} or re.fullmatch(r"h[1-6]", tag):
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)
            if self.heading is not None:
                self.heading["text"] += data
            if self.link is not None:
                self.link["text"] += data

    def handle_comment(self, data):
        self.comments.append(data)

    @property
    def plain_text(self):
        return "\n".join(
            " ".join(line.split())
            for line in "".join(self.text).splitlines()
            if line.strip()
        )


def digest(value):
    return hashlib.sha256(value).hexdigest()


def utc_date(value):
    return (
        datetime.fromtimestamp(calendar.timegm(value), timezone.utc).isoformat()
        if value
        else None
    )


def editorial_fields(comments):
    # Preserve the full comment separately; this is only a publisher-specific projection.
    fields = {}
    for comment in comments:
        parts = re.split(r"(?m)^([A-Za-z][A-Za-z ]+):\s*\n", comment)
        for key, value in zip(parts[1::2], parts[2::2]):
            fields[key] = value.strip()
    return fields


def construct(value, detail=None):
    if value is None:
        return None
    return dict(detail) if detail else {"value": value, "type": "text/plain"}


def content_model(block, base):
    fields = dict(block)
    if block.get("type") in {"text/html", "application/xhtml+xml"}:
        html = InspectHTML(block.get("base") or base)
        html.feed(block["value"])
        html.close()
        editorial = editorial_fields(html.comments)
        fields.update(
            text=html.plain_text,
            markdown=htmd.convert_html(block["value"]),
            html_features={
                "links": html.links,
                "images": html.images,
                "headings": [
                    {
                        "level": h["level"],
                        "title": h["text"],
                        "css_class": h["class"],
                        "permalink": h["permalink"],
                    }
                    for h in html.headings
                ],
                "anchors": html.anchors,
                "comments": html.comments,
                "editorial": {
                    "tags": [
                        t.strip()
                        for t in editorial.get("Tags", "").split(",")
                        if t.strip()
                    ],
                    **{
                        key: editorial.get(label)
                        for key, label in [
                            ("summary", "Summary"),
                            ("url", "URL"),
                            ("title", "Title"),
                            ("bullet", "Bullet"),
                            ("separator", "Separator"),
                            ("top_sources", "Top Sources"),
                        ]
                    },
                    "fields": editorial,
                }
                if editorial
                else None,
            },
        )
    else:
        fields["text"] = block["value"]
    return ContentBlock.model_validate(fields)


def entry_model(entry, feed_id):
    return Entry.model_validate(
        {
            "feed_id": feed_id,
            "id": entry.get("id") or entry["link"],
            "id_is_permalink": entry.get("guidislink"),
            "url": entry.get("link"),
            "title": construct(entry.get("title"), entry.get("title_detail")),
            "authors": entry.get("authors", []),
            "tags": entry.get("tags", []),
            "links": entry.get("links", []),
            "published": utc_date(entry.get("published_parsed")),
            "updated": utc_date(entry.get("updated_parsed")),
            "summary": content_model(
                construct(entry["summary"], entry.get("summary_detail")),
                entry.get("link", ""),
            )
            if "summary" in entry
            else None,
            "content": [
                content_model(block, entry.get("link", ""))
                for block in entry.get("content", [])
            ],
            "comments_url": entry.get("comments"),
            "comments_feed_url": entry.get("wfw_commentrss"),
            "comment_count": entry.get("slash_comments"),
            "wordpress_post_id": entry.get("post-id"),
            "parser_fields": json.loads(json.dumps(entry)),
        }
    )


def main():
    files = sorted(SOURCE.glob("pluralistic_rss-2026-10-*.rss"))
    if not files:
        raise SystemExit(f"No October snapshots found in {SOURCE}")
    # Once written, replay the same sample even if new snapshots arrive.
    manifest_path = HERE / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        sample = sorted(random.Random(SEED).sample(files, math.ceil(len(files) / 10)))
        manifest = {
            "population_count": len(files),
            "seed": SEED,
            "rounding": "ceil(10% of files)",
            "population_first": files[0].name,
            "population_last": files[-1].name,
            "files": [
                {"name": p.name, "sha256": digest(p.read_bytes())} for p in sample
            ],
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    field_counts, feed_counts, tags, editorial_keys = (
        Counter(),
        Counter(),
        Counter(),
        Counter(),
    )
    variants, body_variants = defaultdict(set), defaultdict(set)
    unique = {}
    snapshots, observations, errors = [], [], []
    content_types, summary_types, namespaces = Counter(), Counter(), {}
    values_by_id = defaultdict(lambda: defaultdict(set))
    validated_entries = 0
    normalized_example = None
    latest_revisions = {}
    ledger_revisions = []
    recorded_at = datetime.now(timezone.utc)
    for selected in manifest["files"]:
        path = SOURCE / selected["name"]
        xml = path.read_bytes()
        assert digest(xml) == selected["sha256"], f"Source changed: {path}"
        parsed = feedparser.parse(xml)
        unsanitized = feedparser.parse(xml, sanitize_html=False)
        namespaces.update(parsed.get("namespaces", {}))
        feed_counts.update(parsed.feed.keys())
        feed_id = next(
            (
                link["href"]
                for link in parsed.feed.get("links", [])
                if link.get("rel") == "self"
            ),
            parsed.feed.get("id") or parsed.feed.get("link"),
        )
        feed_model = Feed.model_validate(
            {
                "id": feed_id,
                "source_id": parsed.feed.get("id"),
                "url": parsed.feed.get("link"),
                "title": construct(
                    parsed.feed.get("title"), parsed.feed.get("title_detail")
                ),
                "subtitle": construct(
                    parsed.feed.get("subtitle"), parsed.feed.get("subtitle_detail")
                ),
                "language": parsed.feed.get("language"),
                "updated": utc_date(parsed.feed.get("updated_parsed")),
                "links": parsed.feed.get("links", []),
                "parser_fields": json.loads(json.dumps(parsed.feed)),
            }
        )
        assert Feed.model_validate_json(feed_model.model_dump_json()) == feed_model
        if parsed.bozo:
            errors.append(
                {"file": path.name, "error": str(parsed.get("bozo_exception"))}
            )
        captured_at = datetime.fromisoformat(path.stem.removeprefix("pluralistic_rss-"))
        entry_observations = []
        for entry in parsed.entries:
            entry_id = entry.get("id") or entry["link"]
            ledger_key = (feed_id, entry_id)
            revision, appended = append_revision(
                latest_revisions.get(ledger_key),
                feed_id=feed_id,
                entry_id=entry_id,
                parser_fields=json.loads(json.dumps(entry)),
                feedparser_version=feedparser.__version__,
                source_file=path.name,
                source_sha256=selected["sha256"],
                captured_at=captured_at,
                recorded_at=recorded_at,
            )
            latest_revisions[ledger_key] = revision
            if appended:
                assert (
                    EntryRevision.model_validate_json(revision.model_dump_json())
                    == revision
                )
                ledger_revisions.append(revision)
            entry_observations.append(
                {
                    "entry_id": entry_id,
                    "revision_number": revision.revision_number,
                    "fingerprint": revision.fingerprint,
                }
            )
        snapshot = Snapshot.model_validate(
            {
                "source_file": path.name,
                "source_sha256": selected["sha256"],
                "captured_at": path.stem.removeprefix("pluralistic_rss-"),
                "feed_id": feed_id,
                "feedparser_version": feedparser.__version__,
                "format": parsed.version,
                "encoding": parsed.encoding,
                "namespaces": parsed.get("namespaces", {}),
                "bozo": bool(parsed.bozo),
                "parse_error": str(parsed.get("bozo_exception"))
                if parsed.bozo
                else None,
                "observations": entry_observations,
            }
        )
        assert Snapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
        snapshots.append(
            {
                "file": path.name,
                "entries": len(parsed.entries),
                "version": parsed.version,
                "encoding": parsed.encoding,
                "bozo": bool(parsed.bozo),
                "feed": parsed.feed,
                "captured_at": snapshot.captured_at.isoformat(),
            }
        )
        for index, entry in enumerate(parsed.entries):
            field_counts.update(entry.keys())
            key = entry.get("id") or entry.get("link")
            assert key, "Entry without an identifier needs an explicit fallback policy"
            variants[key].add(digest(json.dumps(entry, sort_keys=True).encode()))
            for field, value in entry.items():
                values_by_id[key][field].add(json.dumps(value, sort_keys=True))
            body_variants[key].add(
                digest(json.dumps(entry.get("content", []), sort_keys=True).encode())
            )
            model = entry_model(entry, feed_id)
            assert Entry.model_validate_json(model.model_dump_json()) == model
            validated_entries += 1
            if normalized_example is None:
                normalized_example = model
            content = entry.get("content", [])
            summary_types.update(
                [entry.get("summary_detail", {}).get("type", "missing")]
            )
            blocks = []
            for i, block in enumerate(content):
                content_types.update([block.get("type", "missing")])
                html = InspectHTML(block.get("base") or entry.get("link", ""))
                html.feed(block["value"])
                html.close()
                fields = editorial_fields(html.comments)
                tags.update(html.tags)
                editorial_keys.update(fields.keys())
                blocks.append(
                    {
                        "chars": len(block["value"]),
                        "text_chars": len(html.plain_text),
                        "links": len(html.links),
                        "images": len(html.images),
                        "heading_count": len(html.headings),
                        "headings": html.headings,
                        "anchors": html.anchors,
                        "editorial": fields,
                        "comments": len(html.comments),
                        "tags": html.tags,
                        "sanitization_changed": block["value"]
                        != unsanitized.entries[index]["content"][i]["value"],
                    }
                )
            observation = {
                "snapshot": path.name,
                "id": key,
                "published": utc_date(entry.get("published_parsed")),
                "title": entry.get("title"),
                "summary_chars": len(entry.get("summary", "")),
                "tag_count": len(entry.get("tags", [])),
                "blocks": blocks,
            }
            observations.append(observation)
            unique.setdefault(key, observation)

    unique_blocks = [block for e in unique.values() for block in e["blocks"]]

    def ranges(values):
        return {
            "min": min(values),
            "median": statistics.median(values),
            "max": max(values),
        }

    report = {
        "manifest": manifest,
        "feedparser_version": feedparser.__version__,
        "entry_occurrences": len(observations),
        "unique_entry_ids": len(unique),
        "entry_field_presence": field_counts,
        "feed_field_presence": feed_counts,
        "content_types": content_types,
        "summary_types": summary_types,
        "html_tags": tags,
        "editorial_field_presence": editorial_keys,
        "namespaces": namespaces,
        "parse_errors": errors,
        "ids_with_metadata_variants": sum(len(v) > 1 for v in variants.values()),
        "ids_with_content_variants": sum(len(v) > 1 for v in body_variants.values()),
        "changed_fields_by_unique_id": Counter(
            field
            for fields in values_by_id.values()
            for field, values in fields.items()
            if len(values) > 1
        ),
        "unique_entry_statistics": {
            "published_first": min(e["published"] for e in unique.values()),
            "published_last": max(e["published"] for e in unique.values()),
            "summary_chars": ranges([e["summary_chars"] for e in unique.values()]),
            "tag_count": ranges([e["tag_count"] for e in unique.values()]),
            **{
                field: ranges([b[field] for b in unique_blocks])
                for field in ["chars", "text_chars", "links", "images", "heading_count"]
            },
            "html_changed_by_sanitization": sum(
                b["sanitization_changed"] for b in unique_blocks
            ),
        },
        "validation": {
            "entries_validated_and_json_roundtripped": validated_entries,
            "feeds_and_snapshots_validated_and_json_roundtripped": len(snapshots),
            "ledger_revisions_validated_and_json_roundtripped": len(ledger_revisions),
        },
        "ledger": {
            "item_count": len(latest_revisions),
            "revision_count": len(ledger_revisions),
            "unchanged_observations": len(observations) - len(ledger_revisions),
            "revisions": [
                r.model_dump(mode="json", exclude={"payload_json", "recorded_at"})
                for r in ledger_revisions
            ],
        },
        "snapshots": snapshots,
        "unique_entries": list(unique.values()),
    }
    (HERE / "analysis.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    # Full content remains in source snapshots; store only a small normalized illustration.
    example = normalized_example.model_dump(mode="json", exclude={"parser_fields"})
    for block in example["content"]:
        block["value"] = block["value"][:500] + " [TRUNCATED FOR EXAMPLE]"
        block["text"] = block["text"][:500] + " [TRUNCATED FOR EXAMPLE]"
        block["markdown"] = block["markdown"][:500] + " [TRUNCATED FOR EXAMPLE]"
        block["html_features"]["links"] = block["html_features"]["links"][:3]
        block["html_features"]["images"] = block["html_features"]["images"][:3]
    if example["summary"]:
        example["summary"]["value"] = (
            example["summary"]["value"][:500] + " [TRUNCATED FOR EXAMPLE]"
        )
        example["summary"]["text"] = (
            example["summary"]["text"][:500] + " [TRUNCATED FOR EXAMPLE]"
        )
        if example["summary"]["markdown"]:
            example["summary"]["markdown"] = (
                example["summary"]["markdown"][:500] + " [TRUNCATED FOR EXAMPLE]"
            )
    (HERE / "example-entry.json").write_text(
        json.dumps(example, indent=2, ensure_ascii=False) + "\n"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in {"snapshots", "unique_entries"}
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
