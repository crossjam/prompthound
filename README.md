# prompthound

[Pluralistic](https://pluralistic.net), 
[(\[_defn_\])](https://en.wiktionary.org/wiki/pluralistic) experimentation
with AI models for the discerning
[craphound](https://craphound.com/place/Cory_Doctorow_-_Craphound.txt).
Exploring implications for [memex
methods](https://pluralistic.net/2021/05/09/the-memex-method/).

## Feed ingestion

Install with `uv sync`. Ingest local RSS/Atom files into SQLite:

```bash
uv run prompthound init --db ./feeds.db
uv run prompthound ingest --db ./feeds.db snapshot-1.rss snapshot-2.rss
cat snapshot.rss | uv run prompthound ingest --db ./feeds.db --capture-id capture-123
uv run prompthound ingest --db ./feeds.db --no-progress snapshot.rss.gz
uv run prompthound extract --db ./feeds.db --no-progress
```

`init` is optional: `ingest` initializes or migrates the selected database and
creates its parent directories. Repeating `init` preserves data without an
overwrite prompt. `init --dry-run` reports paths without changing anything.
Without `--db`, commands use `prompthound.db` in the platformdirs user data
directory for `dev.pirateninja.prompthound` (author `pirateninja.dev`). Custom paths
do not touch that default database.

Supply any number of files; omitted files or `-` read binary stdin. Gzip-compressed
RSS is detected by its bytes. Parsing uses feedparser, validation uses Pydantic v2,
and no feed URLs are fetched. Empty feeds, malformed XML, missing identities,
unreadable files and database errors fail explicitly. A batch continues after an
ordinary input failure, retains successful snapshots, and exits nonzero if any
input or extraction failed. Ctrl-C stops the command with a nonzero exit; the
current source transaction rolls back and earlier commits remain available.

Rich shows live progress on interactive stderr; `--no-progress` disables animation.
Redirected stderr receives plain summaries. Stdout stays empty for ingestion and
extraction. Counts describe committed source rows, distinguish unchanged
observations from capture replays, and include failed/pending extractions. Global
`--log-level` defaults to `WARNING`; `--log-file` also writes diagnostics to a file.
Use `uv run prompthound --help` or a command's `--help` for options.

## Identity and history

Shared tables hold feeds, items, captures, item revisions, ordered observations,
source content blocks, tags, and extraction attempts. Feed identity prefers its
publisher ID, self link, then site link. Item identity is its opaque GUID/Atom ID,
falling back to its article URL, scoped to the feed.

Each item has an append-only ledger. SHA-256 fingerprints cover the complete
feedparser item and parser version/policy, including metadata changes. A new
capture whose state matches the latest revision adds an observation without a
revision. Captures observing `A, A, B, B, A` produce revisions `A, B, A`.
Disappearance from a rolling feed does not delete an item. Updates/deletes to
source records are rejected by database triggers.

Exact input bytes, source URI and SHA-256 are retained. Default capture identity
combines URI, bytes and known capture time: repeating the same input is a replay,
while identical bytes in different files represent distinct captures. Replaying
an old capture after newer ingestion leaves the latest revision unchanged. To
record a new capture of identical stdin bytes, supply a new `--capture-id` (one
input only). Reusing that ID with different bytes fails. A parser version/policy
change can create another interpretation and representation revision of a capture.

`captured_at` comes from an ISO timestamp with timezone in the filename, or from
`--captured-at 2026-10-01T04:55:01+00:00`. Unknown times remain null. Filesystem
mtime and article publication time are never substituted. `recorded_at` is the
database ingestion time. Ledger order is ingestion order, so importing older
captures later can append revisions without implying a recent publisher edit.

## HTML extraction

Summaries and full content remain separate constructs with MIME type, language
and base URI. Beautiful Soup 4 with the explicit lxml backend extracts text,
resolved links/images, headings, anchors and editorial comments; htmd converts
Markdown. Section bodies retain order and distinguish essays from recurring
links, appearances, book lists and other sections. Comment metadata stays
separate from RSS tags and summaries. Feedparser may normalize/sanitize HTML;
exact original XML remains in the capture artifact.

Derived results are append-only and versioned by the extractor and its library
versions/configuration. They run after the source commit. Failed conversions
record error attempts; a database write failure can leave a block pending without
an attempt. Either case reports a nonzero exit while preserving source history.
`extract` retries pending blocks across all snapshots and processes new extractor
versions. It never appends publisher revisions and keeps earlier results.
`init` migrates source data; run `extract` to process migrated historical blocks.

## Querying SQLite

The views support SQLite clients and Datasette:

| View | Row granularity |
| --- | --- |
| `latest_entries` | One latest ledger revision per item, with first/last observation provenance. |
| `entry_history` | One row per revision, including capture and recording times. |
| `latest_entry_content` | One row per current summary/content block, or a null-block row if none exists. |

The content view selects the latest successfully appended extraction per block;
failed attempts never hide a usable result. Its extractor version/configuration
are visible. Query `content_extractions` for a specific historical version.
Examples using the external SQLite command:

```sql
SELECT feed_identifier, item_identifier, revision_number, title
FROM latest_entries ORDER BY published_at DESC;

SELECT revision_number, title, captured_at, recorded_at, source_uri
FROM entry_history WHERE item_identifier = 'opaque:1'
ORDER BY revision_number;

SELECT item_identifier, role, text, extractor_version
FROM latest_entry_content WHERE role = 'content';
```

The separate SQL CLI, full-text search, and HTTP/stdio MCP server remain follow-on
Kata epics. This PR supplies their storage and views.

## Migration and recovery

Recognized legacy per-feed databases migrate transactionally. Before changing
tables, migration creates and checks an adjacent backup named
`<database>.pre-ledger-<UTC timestamp>.sqlite3`. The CLI prints that path. Original
tables remain under `__prompthound_legacy_...`; unrelated tables remain available.
Ambiguous feed associations or unknown layouts fail without replacing the data.
Rerunning migration is safe. Legacy snapshots are labeled `legacy-json` exports
with original columns retained, rather than fabricated XML or capture times.

If migration fails or is interrupted, its changes roll back. To restore a backup,
stop all database writers, retain the failed database and any WAL/SHM sidecar files
together under a different name, then copy the printed backup to the original
database path. Open that restored file with a SQLite client to inspect the old
schema, or rerun `init` after addressing the error. Backups contain all old tables
and data; successful ingestion performed after migration is not in that backup.
Application migrations never delete them automatically.

## Development and verification

```bash
uv run pytest -q -x
uv run poe type
uv run poe lint
uv run python plans/feed-item-model-2026-10/verify_production.py \
  --dataset ../../datasets/pluralistic_feed_snapshots
```

Normal tests use portable fixtures and isolated databases. The optional sample
command reads the frozen ten-file manifest, verifies source checksums, and runs
production ingestion/replay in a disposable database without modifying datasets.
See the [production acceptance results](plans/feed-item-model-2026-10/PRODUCTION.md)
and [storage contracts](docs/ingestion-contract.md). The original model proposal
and its measurements remain under `plans/feed-item-model-2026-10`.
