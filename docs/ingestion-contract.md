# Feed ingestion contracts

The source models live in `prompthound.models`; `prompthound.parsing` adapts
feedparser dictionaries without discarding extensions. Unknown nested metadata is
retained in the enclosing `parser_fields` JSON. Source models contain no derived
HTML extraction results. Identifiers are opaque strings, never normalized HTTP
URLs. Feed identity prefers a publisher ID, then an RSS self link, then the site
link. Item identity prefers its GUID/Atom ID, then its article link. A missing
identity is an explicit input error.

Only explicit parsed dates become UTC-aware datetimes. Missing update dates stay
missing; they do not inherit publication time. Capture time comes from a supplied
timezone-aware value or an ISO timestamp in the source filename, including
percent-encoded file URIs. File mtime and publication time are not capture times.
Unknown historical capture times remain null.

Malformed XML, unrecognized input, empty input, and feeds with zero items fail
explicitly. Feedparser is given bytes, never an input path or URL, so parsing
cannot accidentally fetch an external resource.

## Capture and replay

Default capture identity hashes the source URI, source-byte SHA-256, and known
capture time. Changing a file at the same path creates a distinct capture. Two
filenames with identical bytes are distinct captures. Replaying the same source
and bytes is idempotent. For stdin, the source URI is `stdin`; repeating identical
bytes is a replay unless the caller supplies an explicit `--capture-id`.

Explicit capture IDs are scoped to a feed. Reusing one with different source bytes
is a conflict, not permission to overwrite history. A parser version/policy change
is a new interpretation of a capture and may produce representation revisions;
database parse identity includes the capture ID, parser version, and parse policy.
Source artifacts remain available independently of that interpretation.

## Revision fingerprint

`feedparser-entry-json-v1` hashes a UTF-8 JSON envelope containing the policy,
feedparser version, and the complete parsed item. JSON object keys are sorted,
separators are compact, Unicode remains unescaped, and NaN/Infinity are rejected.
Array order is preserved. This is an application serialization contract, not an
implementation of RFC 8785. Capture location/time and derived HTML/text/Markdown
are excluded. Policy changes require a new policy identifier.

Compare with the latest revision of `(feed_id, item_id)`. `A, A, B, B, A` yields
three revisions, `A, B, A`; the fingerprint is not a unique revision key. Repeated
IDs inside one feed document remain separate observations in their source order,
including distinct revisions when their item values differ. Ledger sequence is
ingestion order. Late historical imports do not claim the publisher reverted at
the current time; capture and recording provenance are independently visible.

## Legacy records

Migration preserves the legacy tables and backs up the database before changes.
Legacy rows are represented by explicitly labeled `legacy-json` source exports,
retaining original table/row keys and all unfamiliar columns. Hashes identify
those actual exports, not an invented original XML document. Missing capture times
are null. Ambiguous row-to-feed associations must be preserved and reported or
cause a recoverable migration error; they must never be silently assigned to a
different feed. Migration and replay policies are covered by database tests.

## Progress events

Services emit `events.Event` values through an optional callback; omitting a
callback produces no terminal output. Stages are input, read, parse, validate,
persist, extract, and migrate. Totals can remain unknown while reading/parsing,
then become item/block/row counts. Progress is provisional until persistence
commits. Only a persist/committed event can carry `Counts`; rollback and replay
events never add rows to `Results`. Input terminal outcomes advance file counts,
including failures and replays. Extraction successes/failures and interruptions
remain distinct from already committed source records. Rich consumes this contract
at the CLI boundary; persistence services do not import terminal renderers.

## SQLite persistence

The database carries an application ID and a versioned `schema_migrations` table.
Every connection enables foreign keys and recursive triggers. Source identities,
snapshots (including exact bytes), revisions, observations, tags, blocks, and
extraction attempts are shared across feeds and protected from updates/deletes.
Existing nonempty databases require explicit legacy migration; initialization
never silently replaces their tables. Future schema versions fail explicitly.

`Store.ingest` prepares model/JSON/fingerprint data outside the write transaction,
then checks replay identity and appends rows under `BEGIN IMMEDIATE`. Revision
sequence and same-item/feed relationships are enforced by SQLite as well as the
adapter. It publishes committed counters only after commit. A failure or interrupt
rolls back all changes for the snapshot. Busy/locked writes retry at most three
times; capture replay and revision comparison are repeated inside each transaction.
Content extraction runs separately from these source writes.
