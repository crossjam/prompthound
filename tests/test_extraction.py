import pytest

from prompthound.extraction import ExtractionConfig, extract_block, extract_pending
from prompthound.models import ContentBlock
from prompthound.parsing import parse_snapshot
from prompthound.storage import Store


HTML = """<!--
Tags:
editorial, metadata

Summary:
A short editorial synopsis

Top Sources:
None
--><p>Masthead</p><h1>Today's links</h1><ul class="toc">
<li><a href="#essay">An essay</a></li><li><a href="#upcoming">Upcoming appearances</a></li></ul>
<p><a name="essay"></a><img src="images/cover.png" alt="" width="100"></p>
<h1>An essay (<a href="#essay">permalink</a>)</h1><p>A <em>bold</em> statement &amp; a link
<a href="/reference" rel="nofollow">Reference</a>.</p><blockquote>A quotation</blockquote>
<p><a name="upcoming"></a></p><h1>Upcoming appearances (<a href="#upcoming">permalink</a>)</h1>
<ul><li>Boston</li><li>London</li></ul><script>hidden()</script><style>hidden-style</style>"""


def test_html_comments_entities_links_images_and_inline_boundaries():
    result = extract_block(
        ContentBlock(value=HTML, type="text/html"),
        article_url="https://example.com/posts/one",
    )
    assert result.features.editorial["Tags"] == "editorial, metadata"
    assert result.features.editorial["Top Sources"] == "None"
    assert "A bold statement & a link" in result.text
    assert [line for line in result.text.splitlines() if line][-2:] == [
        "Boston",
        "London",
    ]
    assert "hidden" not in result.text
    assert "A short editorial synopsis" not in result.text
    assert "A short editorial synopsis" not in result.markdown
    assert any(
        link.href == "https://example.com/reference" for link in result.features.links
    )
    assert result.features.images[0].src == "https://example.com/posts/images/cover.png"
    assert result.features.images[0].alt == ""
    assert result.features.images[0].width == "100"
    assert result.features.headings[1].title == "An essay"
    assert "essay" in result.features.anchors


def test_sections_follow_toc_and_named_anchor_boundaries():
    result = extract_block(
        ContentBlock(value=HTML, type="text/html"),
        article_url="https://example.com/posts/one",
    )
    essay = next(
        section for section in result.features.sections if section.anchor == "essay"
    )
    appearances = next(
        section for section in result.features.sections if section.anchor == "upcoming"
    )
    assert essay.kind == "essay" and essay.title == "An essay"
    assert "bold statement" in essay.text and "Boston" not in essay.text
    assert "cover.png" in essay.html
    assert appearances.kind == "appearances"
    assert "Boston" in appearances.text
    assert [section.position for section in result.features.sections] == list(
        range(len(result.features.sections))
    )


def test_malformed_html_and_missing_alt_are_supported():
    result = extract_block(
        ContentBlock(value="<div><h2>Heading<p>Text<img src='/x'>", type="text/html")
    )
    assert "Text" in result.text
    assert result.features.images[0].alt is None


def test_toc_wrappers_visit_nested_headings_and_anchors_without_duplicate_body():
    result = extract_block(
        ContentBlock(
            type="text/html",
            value='<nav><a href="#essay">TOC label</a><a href="#nested">Nested</a></nav>'
            '<section id="essay"><h2>Actual essay title</h2><p>First body</p>'
            "<h3>Subheading</h3><p>Second body</p>"
            '<div><div id="nested"><p>Nested body</p></div></div></section>'
            "<h2>Following section</h2><p>Final body</p>",
        )
    )
    sections = result.features.sections
    essay = next(section for section in sections if section.anchor == "essay")
    assert essay.title == "Actual essay title"
    assert "First body" in essay.text and "Second body" not in essay.text
    subheading = next(section for section in sections if section.title == "Subheading")
    assert subheading.anchor is None
    assert "Second body" in subheading.text and "Nested body" not in subheading.text
    assert (
        next(section for section in sections if section.anchor == "nested").title
        == "Nested"
    )
    assert sections[-1].title == "Following section" and sections[-1].anchor is None
    assert result.features.headings[0].anchor == "essay"
    combined = "\n".join(section.text for section in sections)
    for text in ("First body", "Second body", "Nested body", "Final body"):
        assert combined.count(text) == 1


@pytest.mark.parametrize("target", ["legacy", "modern"])
def test_distinct_anchor_name_and_id_both_support_toc_boundaries(target):
    result = extract_block(
        ContentBlock(
            type="text/html",
            value=f'<nav><a href="#{target}">Target title</a></nav>'
            "<h2>Before</h2><p>Before body</p>"
            '<a id="modern" name="legacy"></a><p>Target body</p>',
        )
    )
    section = next(
        section for section in result.features.sections if section.anchor == target
    )
    assert section.title == "Target title"
    assert "Target body" in section.text and "Before body" not in section.text
    assert {"modern", "legacy"}.issubset(result.features.anchors)


@pytest.mark.parametrize("target", ["legacy", "modern"])
def test_heading_after_dual_identifier_anchor_uses_toc_alias(target):
    result = extract_block(
        ContentBlock(
            type="text/html",
            value=f'<nav><a href="#{target}">Target title</a></nav>'
            '<a id="modern" name="legacy"></a><h2>Heading title</h2><p>Body</p>',
        )
    )
    section = next(
        section for section in result.features.sections if section.anchor == target
    )
    assert section.title == "Heading title" and "Body" in section.text
    assert result.features.headings[0].anchor == target


def test_later_headings_do_not_inherit_a_stale_named_anchor():
    result = extract_block(
        ContentBlock(
            value='<a name="essay"></a><h1>Books worth reading</h1><p>Essay</p>'
            "<h1>How to get Pluralistic:</h1><p>Subscribe</p>",
            type="text/html",
        )
    )
    assert result.features.sections[0].kind == "essay"
    assert result.features.sections[-1].title == "How to get Pluralistic:"
    assert result.features.sections[-1].anchor is None
    assert result.features.sections[-1].kind == "other"


def test_plain_text_is_not_interpreted_as_html_and_base_overrides_article_url():
    value = "Literal <p>text</p> <!-- comment -->"
    result = extract_block(ContentBlock(value=value, type="text/plain"))
    assert result.text == value and not result.features.links
    assert "\\<p\\>" in result.markdown
    html = extract_block(
        ContentBlock(
            value='<a href="ref">R</a>',
            type="text/html",
            base="https://base.example/root/",
        ),
        article_url="https://other.example/",
    )
    assert html.features.links[0].href == "https://base.example/root/ref"


def source_with_html():
    xml = f"""<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
        <channel><title>Feed</title><link>https://example.com</link><item><guid>x</guid>
        <description>Separate summary</description><content:encoded><![CDATA[{HTML}]]></content:encoded>
        </item></channel></rss>""".encode()
    return parse_snapshot(xml, source_uri="test"), xml


def test_extraction_versions_keep_source_revisions_and_old_results(tmp_path):
    model, xml = source_with_html()
    with Store(tmp_path / "extract.db") as store:
        result = store.ingest(model, xml)
        assert (
            extract_pending(store, result.snapshot_id, extractor_version="v1").completed
            == 2
        )
        assert (
            extract_pending(store, result.snapshot_id, extractor_version="v1").completed
            == 0
        )
        assert (
            extract_pending(store, result.snapshot_id, extractor_version="v2").completed
            == 2
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM content_extractions").fetchone()[0]
            == 4
        )
        assert (
            store.conn.execute(
                "SELECT COUNT(DISTINCT role) FROM content_blocks"
            ).fetchone()[0]
            == 2
        )
        assert (
            extract_pending(
                store,
                result.snapshot_id,
                extractor_version="v2",
                config=ExtractionConfig(sections=False),
            ).completed
            == 2
        )


def test_failed_extractions_remain_visible_and_can_be_retried(tmp_path, monkeypatch):
    model, xml = source_with_html()
    with Store(tmp_path / "extract.db") as store:
        result = store.ingest(model, xml)
        real_extract = extract_block

        def fail(*args, **kwargs):
            raise ValueError("injected extractor failure")

        monkeypatch.setattr("prompthound.extraction.extract_block", fail)
        attempt = extract_pending(store, result.snapshot_id, extractor_version="v1")
        assert attempt.failed == attempt.pending == 2
        monkeypatch.setattr("prompthound.extraction.extract_block", real_extract)
        assert (
            extract_pending(store, result.snapshot_id, extractor_version="v1").completed
            == 2
        )
        assert (
            store.conn.execute(
                "SELECT COUNT(*) FROM content_extractions WHERE status='failed'"
            ).fetchone()[0]
            == 2
        )
        assert (
            store.conn.execute("SELECT COUNT(*) FROM entry_revisions").fetchone()[0]
            == 1
        )


def test_extraction_rejects_held_source_transaction(tmp_path):
    model, xml = source_with_html()
    with Store(tmp_path / "extract.db") as store:
        result = store.ingest(model, xml)
        store.conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="transaction"):
            extract_pending(store, result.snapshot_id)
        store.conn.rollback()
