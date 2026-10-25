# Feed item model proposal from October 2026 snapshots

This document preserves the original exploratory proposal and its measurements.
Production models, persistence, migrations, extraction, CLI integration and Rich
progress now live under `src/prompthound`. See [production acceptance](PRODUCTION.md),
[usage and recovery](../../README.md), and [storage contracts](../../docs/ingestion-contract.md)
for the implemented behavior. In production, source `ContentBlock` values and
versioned extraction results are separate records; the prototype combined them.

Implementation is tracked in Kata under `prompthound#g9js`; see the
[issue hierarchy and prerequisite roadmap](../KATA_ROADMAP.md). The checklist below
is design context; maintain execution status in the linked Kata tasks.

## Sample and validation

- Population: 96 files matching `pluralistic_rss-2026-10-*.rss`, from October 1
  through October 24, 2026. This is the available portion of October, not a full month.
- Simple random sample without replacement: `ceil(96 * 0.10) = 10` files (10.42%),
  using `random.Random(202610)` on sorted filenames.
- `manifest.json` freezes the selected filenames and SHA-256 checksums. Later runs
  replay that sample even if additional snapshots arrive.
- Parsed with the installed `feedparser 6.0.11`: 10 RSS 2.0 documents, 30 entries
  each, 300 occurrences, 42 distinct GUIDs, no bozo errors.
- All 300 normalized entries, 10 feeds, and 10 snapshot records passed Pydantic
  validation and equality checks after JSON serialization/deserialization.
- The two Python proposal files passed Ruff linting.

Selection is by **snapshot capture month**, not entry publication month. Posts in
the sample were published from August 8 through October 19. Repeated snapshots
are correlated; the 300 occurrences are not 300 independent articles. Two selected
files even have identical bytes. Results describe this publisher and this sample,
not every RSS/Atom feed.

Run from the repository root:

```bash
uv run python plans/feed-item-model-2026-10/analyze.py
```

The script only reads the ten selected RSS files and writes analysis artifacts in
this directory. It makes no HTTP requests. Its source path is relative to the
repository's location alongside the user's `datasets` directory.

## Observed metadata

Every occurrence has all of the following fields:

| Feedparser field | Proposed representation | Reason |
| --- | --- | --- |
| `id`, `guidislink` | `Entry.id`, `id_is_permalink` | Preserve the opaque GUID and its semantics separately from the article URL. |
| `link`, `links` | `url`, `list[Link]` | Retain the article URL and typed feed links. |
| `title`, `title_detail` | `TextConstruct` | Preserve the value, MIME type, language, and base URI. |
| `author`, `authors`, `author_detail` | `list[Person]` plus preserved parser fields | Avoid assuming there can only be one author. |
| `published`, `published_parsed` | `AwareDatetime` plus original parser fields | Preserve the original date string while exposing a UTC datetime. |
| `tags` | `list[Tag]` | Retain each term, scheme, and label; observed 1–18 tags per distinct post. |
| `summary`, `summary_detail` | Optional `ContentBlock` | Summary is not a reliably short abstract. |
| `content` | `list[ContentBlock]` | There is one HTML block per occurrence here; retain a list to support alternate representations. |
| `comments` | `comments_url` | Preserve comment navigation independently of the article URL. |
| `wfw_commentrss` | `comments_feed_url` | Separate subscription endpoint for comments. |
| `slash_comments` | Nonnegative integer `comment_count` | Feedparser returns a numeric string. |
| `post-id` | Nonnegative integer `wordpress_post_id` | WordPress identity is useful but should not replace the publisher GUID. |

Example identity: `id = "https://pluralistic.net/?p=13338"`, while the article URL
is `https://pluralistic.net/2026/09/29/algorithmic-wage-theft/` and `guidislink` is
false. Use `(feed_id, id)` for logical identity, not the post title or HTML hash.
Strings preserve exact identifiers and URI spellings; do not force every GUID
or link into an HTTP-only URL type.

No entry has an explicit `updated` field in this sample. Keep that field optional;
do not invent an update time from publication or capture time. Feedparser's
parsed date tuples are UTC: the adapter uses `calendar.timegm`, not local-time
`time.mktime`, and Pydantic `AwareDatetime` requires timezone information.

The channel metadata includes title, subtitle, language (`en-US`), updated time,
self and alternate links, generator, image with dimensions, syndication period
and frequency, and a WordPress site ID. The channel has no `id`, so this proposal
uses its self-link, `https://pluralistic.net/feed/`, as application feed identity.
Preserve all channel fields in `Feed.parser_fields`, with commonly queried fields
also exposed directly. `Snapshot.namespaces` preserves the namespace map.

## HTML findings and representation

Every content block is `text/html`; every summary is also reported as `text/html`,
even though the inspected summary reads like flattened article text. In the first
observed version of each of the 42 distinct posts:

| Property | Minimum | Median | Maximum |
| --- | ---: | ---: | ---: |
| HTML characters | 22,349 | 33,178.5 | 47,591 |
| Extracted text characters | 13,137 | 21,689.5 | 34,403 |
| RSS summary characters | 13,191 | 21,748.5 | 34,517 |
| Links | 61 | 81.5 | 115 |
| Images | 10 | 10 | 17 |
| Headings | 10 | 10 | 11 |

All headings observed in content are `h1`. This HTML is a daily collection with
several sections, not one undifferentiated article. Typical structure:

1. A masthead image and a “Today's links” table of contents.
2. The main essay, with a named anchor and heading permalink.
3. “Hey look at this” and “Object permanence.”
4. Upcoming/recent appearances, books, and a colophon.
5. Subscription instructions.

Each of the 300 HTML blocks also contains an HTML comment with the same seven
editorial metadata keys: `Tags`, `Summary`, `URL`, `Title`, `Bullet`, `Separator`,
and `Top Sources`. The editorial summary is a short section list, distinct from
the RSS summary. `Top Sources` is the literal string `None` throughout the sample;
this gives no evidence for a structured list of sources yet. Preserve the comment
and its fields, rather than guessing a richer type or silently treating the string
as JSON null. `Bullet` can be an HTML character reference; retain its original
string and decode it separately if needed for display.

Recommended parsing pipeline:

1. Keep the original XML snapshot and its hash. Feedparser output is normalized
   data, not an exact copy of the source XML or HTML.
2. Respect each construct's MIME type, language, and base URI. Keep summary and
   full content independently, even when they substantially overlap.
3. Store feedparser-normalized HTML in `ContentBlock.value`. Extract the metadata
   comment before text/Markdown conversion discards comments. Keep comment tags
   separate from RSS tags; they need not be identical.
4. Parse markup structurally, resolving links against `base` or the article URL.
   Preserve anchor targets, link text, image sources/alt text/dimensions, headings,
   emphasis, lists, paragraphs, and blockquotes. Empty image alt text should stay
   empty, rather than being invented from surrounding prose.
5. Derive plain text for search and Markdown for downstream reading/AI use. Preserve
   paragraph/list boundaries, omit comments and script/style bodies from prose,
   and retain HTML as the source for future extraction changes. The prototype uses
   Python's `HTMLParser` for inspection and the existing `htmd-py` dependency for
   Markdown; no new HTML dependency is needed for this experiment.
6. Before article-level retrieval/chunking, add a DOM-aware section-body extractor
   that follows table-of-contents fragments, named anchors, and heading permalinks.
   Preserve section order and separate essay text from recurring announcements and
   book lists. The prototype extracts an outline and anchors; it does not yet
   partition section bodies or label boilerplate.

Disabling sanitization for comparison changed the HTML in all 42 first-observed
post bodies. The first inspected difference was removal of image attributes
`data-recalc-dims` and `decoding`. Thus `parser_fields` preserves feedparser's
representation, not every original XML attribute. Actual rendering should apply
the application's HTML rendering policy; text/plain constructs must be escaped.

## Proposed model boundaries

See `proposed_models.py` for the complete executable definitions:

- `Feed`: stable source identity and channel metadata.
- `Entry`: publisher identity, article URL, authors, tags, dates, comments metadata,
  summary, content blocks, and a JSON-safe copy of all parsed entry fields.
- `EntryRevision`: an append-only ledger record for one `(feed_id, entry_id)`,
  with sequence number, previous sequence number, fingerprint, immutable payload
  JSON, capture provenance, and recording time.
- `ContentBlock`: source construct plus derived text, Markdown, and `HTMLFeatures`.
- `HTMLFeatures`: links, images, section headings, anchors, comments, and optional
  publisher-specific `EditorialMetadata`.
- `Snapshot`: capture timestamp from the filename, source filename/hash,
  parser/version/policy, namespaces, diagnostics, and observations of entry revisions.

The common base uses `extra="forbid"` to catch mistakes in the normalized adapter.
Unknown publisher/feedparser metadata belongs in `parser_fields`, rather than being
discarded. A production adapter should project known nested fields explicitly and
preserve unfamiliar ones in that raw mapping. The sample validates this adapter,
not arbitrary new publishers or extensions.

## Append-only item ledgers

Treat each `(feed_id, entry_id)` as its own append-only ledger. An `Entry` is the
item state held by a revision, not a row to overwrite on every ingestion.

For every item observed in a captured feed:

1. Canonicalize the complete feedparser entry to UTF-8 JSON with sorted object
   keys, compact separators, unescaped Unicode, and no non-JSON numeric values.
   Array order is retained. This is an explicitly versioned application format,
   not a claim of implementing the RFC 8785 canonicalization standard.
2. Compute SHA-256 over an envelope containing that payload, the fingerprint
   policy (`feedparser-entry-json-v1`), and the feedparser version. Capture time,
   filenames, and derived text/Markdown/HTML-extraction results are excluded.
3. Compare with the **latest revision for this item**. If there is none, append
   revision 1. If the fingerprint matches, retain the latest revision and append
   only the snapshot observation. If it differs, append revision N+1 with a
   reference to N. Never update or delete earlier revisions.
4. Record each snapshot observation's `(entry_id, revision_number, fingerprint)`
   so unchanged items still have evidence that they appeared in that capture.

For captures containing `A, A, B, B, A`, the item ledger contains `A, B, A` with
revision numbers `1, 2, 3`. A hash can repeat across revisions: a uniqueness
constraint on `(feed_id, entry_id, fingerprint)` would incorrectly discard
reversions. The revision key is `(feed_id, entry_id, revision_number)`.

`append_revision()` in the proposal demonstrates this decision logic;
`test_ledger.py` covers repeated captures, reversions, reordered dictionary keys,
metadata-only changes, feed-scoped identity, and parser-version changes.
`EntryRevision` is frozen and its payload is immutable JSON text.
All eight ledger tests pass. Replaying the sampled captures produced 61 revisions
across 42 item ledgers and 239 unchanged observations; all 61 revisions also passed
Pydantic validation and JSON round-trip checks.

Persisted ingestion must compare the latest state and insert within a single
transaction, serializing competing writers to the same item. Enforce the revision
key in SQLite and reject updates/deletes of ledger rows. These database constraints
are implemented in the production SQLite schema; they are not guarantees supplied
by a Pydantic model or this in-memory prototype. Re-ingesting a previously processed snapshot
must be idempotent by snapshot identity before replaying its observations.

The sequence records observation/ingestion order. Keep `captured_at` separately
from `recorded_at`; a late historical import is not proof the publisher changed
back at the time of ingestion. The analysis replays its fixed sample in capture
order. An item disappearing from a rolling RSS window is not a deletion event.
There is no reliable entry-level `updated` timestamp in this sample.

Because the envelope includes the parser version/policy, an intentional parser
upgrade can introduce a representation revision even if source bytes did not
change. Record that provenance and distinguish it from a known publisher edit.
Changing the separate HTML extractor or Markdown converter does not change this
fingerprint. Recompute derived representations from retained revision payloads.

Snapshot history matters: 14 of the 42 posts have multiple parsed representations;
13 have changing comment counts, seven have changing comment URLs, and six have
changing content and summaries. These groups overlap. No entry update timestamp
accompanies these changes. Metadata-only changes belong in the ledger too, so the
fingerprint covers all parsed item fields rather than just its HTML body.

## SQLite persistence design

Use explicit versioned SQL migrations and small adapters between validated
Pydantic objects and `sqlite-utils`. Pydantic defines the application contracts;
SQL defines keys, relationships, constraints, indexes, and append-only enforcement.
Use shared tables across feeds, replacing the current per-feed entry-table layout.

| Table | Responsibility and key fields |
| --- | --- |
| `feeds` | Stable feed identity: internal integer primary key, feed URL, publisher identifier. |
| `entries` | Stable item identity: integer primary key, `feed_id`, publisher `item_id`; unique on `(feed_id, item_id)`. |
| `snapshots` | Capture provenance: `feed_id`, source location, source SHA-256, `captured_at`, `recorded_at`, channel metadata JSON, namespaces, parser version/policy, and diagnostics. |
| `entry_revisions` | Append-only item state: integer primary key, `entry_id`, revision number, predecessor, fingerprint/policy, parser version, canonical payload JSON, first-observed snapshot, and recording time. |
| `snapshot_entries` | Observations: snapshot, entry revision, and original item position; primary key on `(snapshot_id, position)` preserves repeated IDs within one feed document. |
| `content_blocks` | Revision-owned summary and body constructs: role, position, MIME type, language, base URI, and value; retain separate summary and full-content blocks. |
| `content_extractions` | Derived text, Markdown, HTML features JSON, extractor version/configuration, and extraction time, referencing the source content block. |
| `revision_tags` | Revision-owned tag term, scheme, label, and position for filtering while retaining order. |

Keep changing attributes on `entry_revisions`: title, article URL, publication and
update times, comment URLs/count, and WordPress post ID. Preserve title construct
details, authors, typed links, and unfamiliar fields as JSON alongside useful
query columns. Keep the complete canonical parser payload for fidelity and model
reconstruction. The adapter must write columns and JSON from the same validated
state in one transaction. Channel metadata belongs to snapshots so feed title,
image, and generator changes remain historically accessible.

Store timezone-aware dates as consistently formatted UTC ISO 8601 text, booleans
as integers constrained to 0/1, and JSON as text checked with `json_valid()`.
Optional Pydantic fields map to nullable columns; required identity and provenance
fields are `NOT NULL`. A separate internal primary key keeps foreign keys compact
while retaining publisher identifiers verbatim.

Database constraints and indexes:

- Enforce unique `(entry_id, revision_number)`, positive revision numbers, and
  nonnegative comment counts. A predecessor must belong to the same item; use
  composite foreign keys and validate consecutive revision numbering.
- Enforce that each observed revision belongs to the snapshot's feed. Enable
  `PRAGMA foreign_keys = ON` on every connection, before opening transactions.
- Reject updates/deletes to ledger records and their source-state children, using
  database triggers. Preserve historical rows when serving or reprocessing data.
- Give fingerprints a nonunique index. Repeated fingerprints are valid revisions
  when an item reverts to a previous state.
- Index revision lookup by `(entry_id, revision_number DESC)`, snapshot lookup by
  `(feed_id, captured_at)`, observations by revision, and tags by term and revision.
  Add date indexes to support actual query patterns rather than indexing all JSON.
- Give captures an explicit stable identity for replay detection; do not make
  source SHA alone unique. Two captures can contain identical bytes. Historical
  imports retain their original capture times independently of ingestion order.

For each snapshot, validate and fingerprint outside the write transaction where
possible, then use `BEGIN IMMEDIATE` to atomically check replay identity, find or
create items, compare latest fingerprints, append revisions and source children,
record all observations, and commit. Check replay identity again inside the
transaction to handle competing writers. Roll back the entire snapshot on failure;
retry lock contention without allocating duplicate revisions. Keep expensive HTML
extraction outside the write transaction and store its versioned results separately.

Expose stable query views for Datasette and MCP:

- `latest_entries`: the highest ledger revision for each item.
- `entry_history`: revisions with capture and recording provenance.
- `latest_entry_content`: current item state with a selected extraction version.

"Latest" means ledger observation order, not publication time or maximum capture
time. Add a rebuildable FTS5 index over selected extracted text when implementing
full-text search; make whether a query searches current or historical revisions
explicit. Re-extraction creates new derived records without appending publisher
revisions. Record the extractor version and configuration so results can be
reproduced and a preferred version selected deterministically.

Migration must preserve the existing database and leave a rollback/recovery path.
Map legacy feeds and per-feed tables into stable identities and initial revisions,
preserving fields that cannot yet be modeled. Mark missing historical provenance
as unknown; do not invent capture times. Define legacy-import provenance explicitly
before enforcing capture requirements on migrated rows. Validate counts,
relationships, and model round trips before switching ingestion and query paths.
This was the migration design; production migration and recovery are now verified
by portable database and CLI tests described in [production acceptance](PRODUCTION.md).

References: [SQLite foreign keys](https://www.sqlite.org/foreignkeys.html),
[JSON functions](https://www.sqlite.org/json1.html),
[transactions](https://www.sqlite.org/lang_transaction.html), and
[FTS5](https://www.sqlite.org/fts5.html).

## Rich progress indicators

Use `rich.progress.Progress` with the CLI's shared console on stderr. Declare
`rich` as a direct dependency (now implemented); the original CLI
received it transitively. Keep the persistence
and parsing layers independent of terminal rendering by emitting progress events
or accepting a callback that the CLI translates into Rich updates.

Progress should cover the work users wait for:

- An overall snapshot/file task with a known total for filename input, showing
  completed/total, a progress bar, elapsed time, and estimated remaining time.
- A current-snapshot task that reports reading, parsing, validating, and committing.
  Use an indeterminate spinner while feedparser is parsing or stdin has an unknown
  total; switch to an item count once parsed entries are available. Do not parse
  twice or buffer all input files merely to calculate a progress denominator.
- Item progress for normalization and ledger comparison, followed by explicit
  commit status. Track new items, appended revisions, unchanged observations,
  replayed snapshots, and failed snapshots separately.
- Separate progress for HTML extraction/re-extraction and database migrations,
  with block/row totals when available. Rendering must not keep SQLite write
  transactions open while HTML extraction runs.

Advance the overall file task when a file reaches a terminal outcome, including
failure or replay skip, and label that outcome accurately. Publish persisted item
and revision counters only after the snapshot commits; discard provisional counts
on rollback. Do not report 100% as success when failures occurred. End with a
concise summary of committed changes, unchanged/replayed inputs, failures, and any
pending or failed extractions. Preserve interruption/error exit semantics and stop
the live display cleanly on exceptions or Ctrl-C.

Enable live animation only for an interactive stderr terminal. Add `--no-progress`
to suppress progress rendering; redirected output should get a concise plain
summary without cursor-control sequences. Keep stdout available for structured
results and future stdio MCP messages. Progress from shared ingestion code must
remain disabled inside MCP protocol output. Integrate diagnostics through the
shared console so messages do not corrupt the display, and throttle refreshes and
callbacks to avoid per-item rendering overhead.

References: [Rich Progress](https://rich.readthedocs.io/en/stable/progress.html)
and [Console API](https://rich.readthedocs.io/en/stable/console.html).

## Implementation sequence and acceptance checks

- [x] Finalize production models/adapters, canonical fingerprint policy, capture
  identity, legacy provenance, and versioned schema migrations.
- [x] Implement shared tables, constraints, indexes, ledger transactions, replay
  detection, and the three query views; migrate legacy databases with recovery.
- [x] Adopt Beautiful Soup 4 with `lxml` for HTML metadata and section extraction;
  persist source blocks and separately versioned text/Markdown/HTML features.
- [x] Add Rich progress events and CLI rendering for ingestion, extraction, and
  migration, including unknown-total stdin and `--no-progress` behavior.
- [x] Verify ledger persistence with unchanged captures, metadata-only changes,
  `A -> B -> A` reversions, duplicate IDs, identical bytes in separate captures,
  late imports, replay, concurrent writers, rollback, and rejected ledger mutation.
- [x] Verify model/SQL round trips, nullable fields, foreign-key consistency,
  migration recovery, revision-owned tags/content, and latest/history queries.
- [x] Verify extraction upgrades leave publisher revision counts unchanged and
  retain access to earlier extraction results.
- [x] Verify progress events and committed counters with success, replay, failure,
  rollback, and interruption; test terminal/nonterminal stderr, clean stdout, and
  disabled progress without asserting timing-sensitive animation frames.
- [x] Update CLI documentation and examples, then rerun the fixed RSS sample and
  appropriate project checks against the production ingestion path.

## Artifacts and references

- `analysis.json`: field coverage, counts, changes, channel metadata, and HTML
  observations for the first sampled occurrence of each distinct post.
- `manifest.json`: reproducible selection and source checksums.
- `example-entry.json`: normalized example, with explicitly truncated body/summary
  values and link/image lists; the complete model was validated before truncation.
- `analyze.py`: sampling, inspection, normalization, and validation.
- `proposed_models.py`: the Pydantic proposal.
- `test_ledger.py`: ledger decision-logic tests, independent of the source dataset.

For production section extraction, use Beautiful Soup 4 with an explicitly selected
`lxml` backend. Its tree traversal and comment nodes fit the editorial-comment,
table-of-contents, and section-boundary work; keep `htmd-py` for Markdown conversion.
Beautiful Soup 4 and `lxml` are now direct project dependencies; the inspection
prototype still uses the standard library. See the [Beautiful Soup documentation](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)
and [lxml HTML parsing documentation](https://lxml.de/parsing.html).

Library semantics were checked against the official documentation:
[feedparser dates](https://feedparser.readthedocs.io/en/stable/date-parsing.html),
[sanitization](https://feedparser.readthedocs.io/en/stable/html-sanitization.html),
[relative links](https://feedparser.readthedocs.io/en/stable/resolving-relative-links.html),
and [Pydantic AwareDatetime](https://docs.pydantic.dev/2.11/api/types/#pydantic.types.AwareDatetime).
Measurements use the installed feedparser version recorded above, rather than
assuming its behavior matches every version of the documentation.
