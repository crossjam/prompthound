"""HTML projections and append-only extraction attempts, outside source transactions."""

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import version
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
import htmd

from .events import ProgressCallback, emit
from .models import (
    ContentBlock,
    ExtractedContent,
    Heading,
    HTMLFeatures,
    HTMLLink,
    Image,
    Model,
    Section,
)
from .parsing import canonical_json
from .storage import Store

EXTRACTOR_VERSION = "html-v2;" + ";".join(
    f"{name}={version(name)}" for name in ("beautifulsoup4", "lxml", "htmd-py")
)
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = HEADINGS | {
    "p",
    "div",
    "li",
    "ul",
    "ol",
    "blockquote",
    "pre",
    "section",
    "article",
    "table",
    "tr",
    "hr",
}


class ExtractionConfig(Model):
    sections: bool = True


@dataclass(frozen=True)
class ExtractionResult:
    completed: int = 0
    failed: int = 0
    pending: int = 0


@dataclass
class _SectionBuffer:
    title: str = "Introduction"
    anchor: str | None = None
    parts: list[str] = field(default_factory=list)


def attribute(node: Tag, name: str) -> str | None:
    value = node.get(name)
    return (
        " ".join(value)
        if isinstance(value, list)
        else str(value)
        if value is not None
        else None
    )


def plain_text(node) -> str:
    """Keep block boundaries without splitting every inline emphasis into a new line."""

    def walk(part):
        if isinstance(part, Comment):
            return ""
        if isinstance(part, NavigableString):
            return re.sub(r"\s+", " ", str(part))
        if not isinstance(part, Tag) or part.name in {"script", "style", "template"}:
            return ""
        if part.name == "br":
            return "\n"
        text = "".join(walk(child) for child in part.children)
        return "\n" + text + "\n" if part.name in BLOCKS else text

    text = walk(node)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def heading_title(node: Tag) -> str:
    clone = copy.deepcopy(node)
    for link in clone.find_all("a"):
        if link.get_text(strip=True).lower() == "permalink":
            link.decompose()
    return " ".join(re.sub(r"\(\s*\)", "", clone.get_text(" ", strip=True)).split())


def section_kind(title: str, anchor: str | None):
    label = title.lower()
    if anchor == "linkdump" or label == "hey look at this":
        return "links"
    if anchor == "retro" or label == "object permanence":
        return "archive"
    if anchor in {"upcoming", "recent"} or label in {
        "upcoming appearances",
        "recent appearances",
    }:
        return "appearances"
    if anchor in {"latest", "upcoming-books"} or label in {
        "latest books",
        "upcoming books",
    }:
        return "books"
    if anchor == "bragsheet" or label == "colophon":
        return "colophon"
    if label in {"introduction", "today's links"} or label.startswith("how to get"):
        return "other"
    return "essay"


def sections(soup, headings: dict[int, Heading], base: str) -> list[Section]:
    """Partition on heading/permalink and named-anchor boundaries, preserving order."""
    toc = {}
    for link in soup.select("ul.toc a[href], nav a[href]"):
        fragment = urlsplit(urljoin(base, attribute(link, "href") or "")).fragment
        if fragment:
            toc[fragment] = link.get_text(" ", strip=True)
    targets = set(toc)
    targets.update(
        attribute(a, "name") or attribute(a, "id")
        for a in soup.select("a[name], a[id]")
    )
    targets.discard(None)
    result = []
    current = _SectionBuffer()

    def flush():
        html = "".join(current.parts).strip()
        if not html:
            return
        fragment = BeautifulSoup(html, "lxml")
        result.append(
            Section(
                position=len(result),
                title=current.title,
                anchor=current.anchor,
                kind=section_kind(current.title, current.anchor),
                html=html,
                text=plain_text(fragment),
                markdown=htmd.convert_html(html),
            )
        )

    def walk(node):
        def has_nested_boundary():
            return (
                node.find(
                    lambda descendant: descendant.name in HEADINGS
                    or (attribute(descendant, "id") or attribute(descendant, "name"))
                    in targets
                )
                is not None
            )

        if isinstance(node, Tag) and node.name in HEADINGS:
            heading = headings[id(node)]
            if current.anchor is None or current.anchor != heading.anchor:
                flush()
                current.parts = []
                current.anchor = heading.anchor
            current.title = heading.title
            current.parts.append(str(node))
        elif (
            isinstance(node, Tag)
            and (attribute(node, "id") or attribute(node, "name")) in targets
        ):
            anchor = attribute(node, "id") or attribute(node, "name")
            if anchor != current.anchor:
                flush()
                current.title = toc.get(anchor, anchor or "Section")
                current.anchor = anchor
                current.parts = []
            if has_nested_boundary():
                for child in node.children:
                    walk(child)
            else:
                current.parts.append(str(node))
        elif isinstance(node, Tag) and has_nested_boundary():
            for child in node.children:
                walk(child)
        else:
            current.parts.append(str(node))

    for child in (soup.body or soup).children:
        walk(child)
    flush()
    return result


def extract_block(
    block: ContentBlock,
    *,
    article_url: str = "",
    config: ExtractionConfig | None = None,
) -> ExtractedContent:
    config = config or ExtractionConfig()
    if block.type not in {"text/html", "application/xhtml+xml"}:
        # Plain text stays literal; no HTML or comments are interpreted.
        return ExtractedContent(
            text=block.value,
            markdown=block.value.replace("<", "\\<").replace(">", "\\>"),
        )
    base = block.base or article_url
    soup = BeautifulSoup(block.value, "lxml")
    features = HTMLFeatures()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        features.comments.append(str(comment))
        parts = re.split(r"(?m)^([A-Za-z][A-Za-z ]+):\s*\n", str(comment))
        for key, value in zip(parts[1::2], parts[2::2]):
            features.editorial.setdefault(key, value.strip())
        comment.extract()
    for hidden in soup.select("script, style, template"):
        hidden.decompose()
    for link in soup.select("a[href]"):
        href = urljoin(base, attribute(link, "href") or "")
        link["href"] = href
        features.links.append(
            HTMLLink(
                href=href,
                text=link.get_text(" ", strip=True),
                rel=(attribute(link, "rel") or "").split(),
            )
        )
    for image in soup.find_all("img"):
        src = attribute(image, "src")
        if src:
            image["src"] = urljoin(base, src)
        features.images.append(
            Image(**{key: attribute(image, key) for key in Image.model_fields})
        )
    for node in soup.select("[id], a[name]"):
        anchor = attribute(node, "id") or attribute(node, "name")
        if anchor is not None:
            features.anchors.append(anchor)
    headings = {}
    for node in soup.find_all(list(HEADINGS)):
        permalink = next(
            (
                attribute(a, "href")
                for a in node.select("a[href]")
                if urlsplit(attribute(a, "href") or "").fragment
            ),
            None,
        )
        previous_anchor = node.find_previous(
            lambda candidate: candidate.name in HEADINGS
            or (candidate.name == "a" and candidate.has_attr("name"))
        )
        anchor = attribute(node, "id") or (
            urlsplit(permalink).fragment if permalink else None
        )
        if not anchor:
            # A wrapper ID labels its first heading, not every later subheading.
            wrapper = node.find_parent(id=True)
            if wrapper is not None and wrapper.find(list(HEADINGS)) is node:
                anchor = attribute(wrapper, "id")
        if not anchor and previous_anchor is not None and previous_anchor.name == "a":
            anchor = attribute(previous_anchor, "name")
        heading = Heading(
            level=int(node.name[1]),
            title=heading_title(node),
            anchor=anchor,
            permalink=permalink,
        )
        headings[id(node)] = heading
        features.headings.append(heading)
    if config.sections:
        features.sections = sections(soup, headings, base)
    return ExtractedContent(
        text=plain_text(soup), markdown=htmd.convert_html(str(soup)), features=features
    )


def extract_pending(
    store: Store,
    snapshot_id: int,
    *,
    extractor_version: str = EXTRACTOR_VERSION,
    config: ExtractionConfig | None = None,
    progress: ProgressCallback | None = None,
) -> ExtractionResult:
    if store.conn.in_transaction:
        raise ValueError("HTML extraction cannot run inside a source write transaction")
    config = config or ExtractionConfig()
    config_json = canonical_json(config.model_dump())
    source = store.conn.execute(
        "SELECT source_uri FROM snapshots WHERE id=?", (snapshot_id,)
    ).fetchone()
    if source is None:
        raise KeyError(snapshot_id)
    rows = list(
        store.conn.execute(
            """SELECT DISTINCT b.*,r.url FROM content_blocks b
        JOIN entry_revisions r ON r.id=b.revision_id JOIN snapshot_entries o ON o.revision_id=r.id
        WHERE o.snapshot_id=? AND NOT EXISTS (SELECT 1 FROM content_extractions e
            WHERE e.block_id=b.id AND e.extractor_version=? AND e.config_json=? AND e.status='completed')
        ORDER BY b.id""",
            (snapshot_id, extractor_version, config_json),
        )
    )
    emit(progress, "extract", "started", source=source[0], total=len(rows))
    completed = failed = 0
    for position, row in enumerate(rows):
        try:
            content = extract_block(
                ContentBlock(**{key: row[key] for key in ContentBlock.model_fields}),
                article_url=row["url"] or "",
                config=config,
            )
        except Exception as exc:
            store.conn.execute(
                """INSERT INTO content_extractions(block_id,extractor_version,config_json,
                recorded_at,status,error) VALUES (?,?,?,?,?,?)""",
                (
                    row["id"],
                    extractor_version,
                    config_json,
                    datetime.now(timezone.utc).isoformat(),
                    "failed",
                    str(exc),
                ),
            )
            failed += 1
            emit(
                progress,
                "extract",
                "failed",
                source=source[0],
                completed=position + 1,
                total=len(rows),
                message=str(exc),
            )
        else:
            store.conn.execute(
                """INSERT INTO content_extractions(block_id,extractor_version,config_json,
                recorded_at,status,text,markdown,features_json) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    row["id"],
                    extractor_version,
                    config_json,
                    datetime.now(timezone.utc).isoformat(),
                    "completed",
                    content.text,
                    content.markdown,
                    content.features.model_dump_json(),
                ),
            )
            completed += 1
            emit(
                progress,
                "extract",
                "completed",
                source=source[0],
                completed=position + 1,
                total=len(rows),
            )
    return ExtractionResult(completed, failed, failed)
