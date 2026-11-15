# Using the CLI

Prompthound reads local RSS/Atom snapshots, keeps their item history in SQLite,
and extracts text, Markdown, and HTML structure. Run commands from the repository
with `uv`; no virtualenv activation is needed.

## Get started

```bash
uv sync
uv run prompthound --help
uv run prompthound init --db ./feeds.sqlite3
uv run prompthound ingest --db ./feeds.sqlite3 snapshot.rss
```

`ingest` creates the database and its parent directories if needed, so explicit
initialization is optional. Repeating `init` preserves existing data. Recognized
legacy databases migrate with a backup; see [migration and recovery](../README.md#migration-and-recovery).

Use `--db` on each command to select a database. Without it, commands use
`prompthound.db` in the application's platform-specific user data directory.
To see the default location without writing anything:

```bash
uv run prompthound init --dry-run
```

## Ingest files or stdin

Pass one or more filenames, including gzip-compressed RSS:

```bash
uv run prompthound ingest --db ./feeds.sqlite3 first.rss second.rss
uv run prompthound ingest --db ./feeds.sqlite3 snapshot.rss.gz
uv run prompthound ingest --db ./feeds.sqlite3 captures/pluralistic_rss-2026-10-*.rss
```

Files are processed in argument order. For historical imports, supply captures in
the order you want recorded in the ledger. The timestamped filenames above sort
in capture order when expanded by the shell.

Omit filenames, or use `-`, to read stdin:

```bash
cat snapshot.rss | uv run prompthound ingest --db ./feeds.sqlite3
cat snapshot.rss | uv run prompthound ingest --db ./feeds.sqlite3 -
```

Repeated ingestion of the same file contents at the same path, with the same
capture options, is a capture replay.
Identical contents at different paths are separate captures. Items whose parsed
state changes get new revisions; unchanged items get observations. This includes
changes to comment metadata. A new capture observing a return from state B to A
appends another revision of A. Replaying an older capture leaves the ledger intact.

## Set capture provenance

Repeating identical stdin bytes is a replay by default. Use a new `--capture-id`
to record a distinct capture. It applies to exactly one input, and reusing it with
different bytes for the same feed fails. Capture IDs are scoped to a feed.

When the capture time is known, supply an ISO timestamp with a timezone:

```bash
cat snapshot.rss | uv run prompthound ingest --db ./feeds.sqlite3 \
  --capture-id capture-2026-10-25-01 \
  --captured-at '2026-10-25T12:00:00+00:00'
```

Without `--captured-at`, the CLI recognizes timezone-bearing timestamps in source
filenames. Otherwise capture time stays unknown. Publication time and file mtime
are separate from capture time; database recording time is stored independently.

## Retry HTML extraction

Ingestion extracts summaries and full content after committing source history.
To retry pending or failed extraction, or process an installed extractor upgrade:

```bash
uv run prompthound extract --db ./feeds.sqlite3
```

This scans stored captures, skips completed results for the current version and
configuration, and appends new derived results. Source revisions and earlier
extractions remain available. Run it after migrating a legacy database to extract
historical content.

The current `html-v3` extractor has a known section-text fidelity defect: bare
text nodes can decode entities twice or lose literal angle-bracketed text. The
October audit found this in 41 summary blocks; original content and block-level
text remain intact. A corrected extractor version is pending. Repeating `extract`
with the same version does not repair those completed results. See the
[verification findings](../plans/feed-item-model-2026-10/PRODUCTION.md).

## Progress, logging, and failures

Interactive stderr shows live Rich progress. Add `--no-progress` to `init`,
`ingest`, or `extract` to disable animation. Redirected stderr uses plain
diagnostics and summaries; stdout stays empty.

```bash
uv run prompthound ingest --db ./feeds.sqlite3 --no-progress snapshot.rss
uv run prompthound --log-level INFO --log-file ingest.log \
  ingest --db ./feeds.sqlite3 snapshot.rss
```

Logging options belong before the subcommand; their default level is `WARNING`.
Summaries distinguish committed revisions, unchanged observations, replayed
captures, failed inputs, and failed/pending extractions.

An ingestion batch continues after an ordinary bad input and retains successful
captures. Empty inputs, malformed feeds, I/O/database failures, and extraction
failures produce a nonzero exit. Extraction failures preserve committed source
history. Ctrl-C stops processing; an active source transaction rolls back while
earlier commits remain available. Exit codes are 0 for success, 1 for processing
failure or cancellation, and 2 for invalid command usage.

Use `uv run prompthound ingest --help` or `uv run prompthound extract --help`
for the full option list.
For current/history query views, see [querying SQLite](../README.md#querying-sqlite).
