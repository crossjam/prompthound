# Production acceptance

Verified on October 25, 2026, using the frozen `manifest.json` and the production
`prompthound ingest` Click command. The original analysis and prototype are
historical design evidence; production code lives under `src/prompthound`.

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

## Automated checks

The full project suite passed **104 tests**, with **one pre-existing skipped
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
