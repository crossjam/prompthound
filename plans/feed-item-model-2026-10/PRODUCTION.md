# Production acceptance

Verified on October 25, 2026, using the frozen `manifest.json` and the production
`prompthound ingest` Click command. The original analysis and prototype are
historical design evidence; production code lives under `src/prompthound`.

The ten-file results below establish the original persistence/extraction baseline.
A later audit of all available October captures found a section-text fidelity
defect, documented below. Successful extraction status and model round trips do
not establish that section text preserves all source text.

```bash
uv run python plans/feed-item-model-2026-10/verify_production.py \
  --dataset ../../datasets/pluralistic_feed_snapshots
uv run pytest -q -x
uv run poe type
uv run poe lint
```

The verifier checks the manifest before ingestion and uses a temporary SQLite
database that is removed on completion. It only reads the external RSS files.
The exact versions, counts and CLI summaries are captured in
[`production-verification.json`](production-verification.json). To regenerate
that artifact, redirect the verifier's stdout to its filename.

| Measurement | Production result |
| --- | ---: |
| Feeds / snapshots | 1 / 10 |
| Distinct item ledgers | 42 |
| Ordered observations | 300 |
| Revisions | 61 |
| Unchanged observations | 239 |
| Items with 1 / 2 / 3 / 4 revisions | 28 / 10 / 3 / 1 |
| Source summary/content blocks | 122 |
| Successful / failed / pending extractions | 122 / 0 / 0 |
| Extracted sections | 733 |
| Blocks with editorial comment metadata | 61 |
| Revision-owned tags | 603 |
| Latest / history / latest-content view rows | 42 / 61 / 84 |

Ledger counts match the prototype without policy adjustments. There are two
source constructs per revision in this sample: summary and full HTML. Extraction
now partitions section bodies and stores separate versioned attempts, so these
derived counts are new production measurements. Editorial metadata appears in
full-content blocks; RSS summaries remain separate.

All source checksums and stored-byte checksums matched. All snapshot and revision
models round-tripped through SQL/JSON; normalized SQL titles and fingerprints
matched retained models and parser payloads. HTML feature models round-tripped.
Foreign-key checks returned no rows and SQLite integrity returned `ok`. Replaying
all ten captures reported ten replays, zero new rows and zero extraction work;
every table/view count remained unchanged. Ingestion/replay stdout was empty and
stderr contained plain summaries without ANSI controls.

## Full October capture audit

On October 25, 2026, a separate run at commit `3157bf5` explicitly initialized a
temporary SQLite database, then ingested every file matching
`pluralistic_rss-2026-10-*.rss` in `../../datasets/pluralistic_feed_snapshots`,
sorted by filename. There were 96 files, spanning October 1–24, rather than the
entire calendar month. The source files were unchanged, verified by checksums
before and after the run. Existing databases and repository code were untouched.

The commands used the production CLI:

```bash
audit_dir=$(mktemp -d)
audit_db="$audit_dir/october-2026.sqlite3"
uv run prompthound init --db "$audit_db" --no-progress
uv run prompthound ingest --db "$audit_db" --no-progress \
  ../../datasets/pluralistic_feed_snapshots/pluralistic_rss-2026-10-*.rss
uv run prompthound extract --db "$audit_db" --no-progress
```

Here `audit_db` identifies a fresh temporary database path. Repeating the ingest
and init commands verified replay and repeat initialization. The audit also
independently compared all parsed source observations with stored payloads,
fingerprints, revision chains, normalized columns, tags, and content blocks.

| Measurement | Result |
| --- | ---: |
| Accepted / rejected input files | 93 / 3 |
| Feeds / item ledgers / revisions | 1 / 45 / 80 |
| Ordered / unchanged observations | 2,790 / 2,710 |
| Metadata-only / content-changing transitions | 25 / 10 |
| Source summary / full-content blocks | 80 / 80 |
| Successful / failed / pending extractions | 160 / 0 / 0 |
| Extracted sections / editorial metadata blocks | 961 / 80 |
| Revision-owned tags | 757 |
| Latest / history / latest-content view rows | 45 / 80 / 90 |

The rejected files were:

- `pluralistic_rss-2026-10-02T10:55:01+00:00.rss`: an HTML response titled
  `Error 504 | Deflect`, with no RSS/channel/item elements.
- `pluralistic_rss-2026-10-22T16:55:01+00:00.rss`: empty.
- `pluralistic_rss-2026-10-24T10:55:01+00:00.rss`: empty.

The batch continued through all 96 inputs, created no snapshot rows for the three
invalid files, and exited 1 with accurate committed counts. Replaying it reported
93 replays and the same three input failures; all table/view counts and logical
row digests were unchanged. Repeating init and extract also left rows unchanged.
Stdout was empty and redirected stderr contained no ANSI controls.

All 93 retained artifacts matched their exact source bytes and checksums. The
valid captures included 38 distinct source checksums, confirming that separate
captures with identical bytes remain distinct. The independent audit passed
14,457 persistence/model/view assertions, including UTC provenance, complete
metadata, revision predecessors, latest-state selection and extraction selection.
Update/delete probes on all eight protected tables were rejected without changing
rows. Foreign-key checks were empty and SQLite integrity returned `ok`.
Representative history/content queries used the expected indexes.

This single-publisher run contained no reversions or duplicate IDs within a
capture. Legacy migration, competing writers, cancellation, rollback and failed
extraction recovery were covered by the isolated test suite, rather than by this
fresh-database import. Initial ingestion took 53.1 seconds; replay took 48.8 seconds
because inputs are still parsed before capture replay detection.

### Known section-extraction defect

Comparing each block's text with its concatenated ordered section text, ignoring
whitespace, found mismatches in **41 of 80 summaries**. All 41 are explained by an
additional HTML-entity decode. All 80 full-content blocks passed the same check.
The original source content and block-level text remain intact.

The affected extractor is
`html-v3;beautifulsoup4=4.15.0;lxml=6.1.3;htmd-py=0.1.0`.
The section walker serializes bare Beautiful Soup text with `str(node)` and then
reparses it as HTML. Literal angle-bracketed text can consequently become markup
and disappear. These portable examples reproduce the problem:

```python
from prompthound.extraction import extract_block
from prompthound.models import ContentBlock

for source in ("A &amp;amp; B", "Use &lt;literal&gt; here."):
    result = extract_block(ContentBlock(type="text/html", value=source))
    print(result.text, [section.text for section in result.features.sections])
```

The first example produces block text `A &amp; B` but section text `A & B`.
The second produces block text `Use <literal> here.` but section text `Use here.`.
The full test suite still passes, so this is an uncovered fidelity gap.

The fix is tracked in
[prompthound#hxv4](https://katatracker-bmd.exe.xyz/kata?issue=01M5SX1H658NVY20KFJW2NHXV4#direct=1).
It requires portable regression tests, safe text serialization, an extractor
version bump, upgrade/re-extraction checks that preserve source history, and a
section-coverage check in the acceptance verifier. Until that fix ships, rerunning
`extract` with the same version skips these completed results. The original
ten-file verifier checks models and extraction statuses but does not perform this
section-text coverage comparison.

## Automated checks

The full project suite passed **109 tests**, with **one pre-existing skipped
tar.gz archive test**. Portable tests exercise production file/stdin ingestion,
gzip artifacts, capture conflicts, changes/reversions, duplicate IDs, metadata
changes, late captures, concurrent writers, lock retries, atomic rollback,
interruption, append-only constraints, legacy migration/backups/recovery,
versioned extraction and latest/history views. They do not require external data.
The skipped historical test is unrelated to reading individual gzip RSS files,
which is covered by a passing production CLI test.

`uv run poe type` passes. Ruff passes for all non-vendored production modules,
the new/updated tests, and `verify_production.py`. The full `uv run poe lint`
still reports ten pre-existing findings in vendored feed-to-sqlite/slugify code
and `tests/test_vendor.py`: unused imports/re-export, bare except, star import,
Python 2 `unicode`, and a lambda assignment. The initial audit found eleven;
removing the retired CLI's unused logger removed one. These remaining findings
are outside this ingestion change. Feedparser 6.0.11 emits its existing Python
3.13 HTML regex deprecation warnings during tests.

Implementation and verification evidence are tracked in Kata epic
`prompthound#g9js` and its children. The final full-branch roborev result is
recorded there after the requested refinement. Separate SQL/FTS and MCP epics
remain outside this acceptance scope.
