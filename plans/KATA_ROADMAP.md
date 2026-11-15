# Kata implementation roadmap

Kata is the execution record for these plans. This index maps the source documents
to 23 open issues: three epics and 20 implementation tasks. Twenty-two issues were
created in this breakdown; the existing MCP epic was reused. Each task includes
scope and acceptance criteria, with tests attached to the behavior it implements.
Issue status and subsequent scope changes should be maintained in Kata.

Parent links group tasks. Dependency links identify prerequisites and gate readiness;
the three epics are related rather than blocking each other wholesale. The model
and ledger code under plans is a prototype, and these issues describe remaining
production work. Previously completed dependency additions and exploratory sampling
are context, not newly opened implementation tasks.

## Production feed ingestion and append-only history

Epic: [prompthound#g9js](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD516JAH7ZVKZ54V8NG9JS#direct=1). [Model, ledger, SQLite, HTML, and Rich plan](feed-item-model-2026-10/README.md), with [the original ingestion plan](issue-4-plan-20251120.md).

| Issue | Task | Prerequisites |
| --- | --- | --- |
| [prompthound#0k7g](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5F9J0ATJZBJ9S8K10K7G#direct=1) | Promote feed models and fingerprint contracts into the package | None |
| [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1) | Define progress events and committed-result accounting for feed workflows | None |
| [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1) | Create versioned shared SQLite schema and integrity constraints | [prompthound#0k7g](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5F9J0ATJZBJ9S8K10K7G#direct=1) |
| [prompthound#8v2p](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5HE4EYE5WH664YJ98V2P#direct=1) | Fix custom database initialization and truthful ingest error reporting | None |
| [prompthound#eje0](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5J3JPTV4EN0JKFFZEJE0#direct=1) | Persist transactional append-only ledgers and snapshot observations | [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1), [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1) |
| [prompthound#g717](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5JW7TGQHTVCTG5Q9G717#direct=1) | Migrate legacy per-feed databases without losing source data | [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1), [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1) |
| [prompthound#tw7t](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5KF8Z4EQRBX9NXSXTW7T#direct=1) | Extract versioned HTML structure, editorial metadata, text, and Markdown | [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1), [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1) |
| [prompthound#c7xe](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5M3VAT11D5A0NY4JC7XE#direct=1) | Expose latest-entry, revision-history, and selected-content query views | [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1) |
| [prompthound#0gtn](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5MRDEDY4XZEXXXD60GTN#direct=1) | Render Rich progress for ingestion, extraction, and migrations | [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1) |
| [prompthound#jr88](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5NBMXZP95HGWJFW2JR88#direct=1) | Wire production CLI ingestion through models, ledger storage, and extraction | [prompthound#eje0](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5J3JPTV4EN0JKFFZEJE0#direct=1), [prompthound#g717](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5JW7TGQHTVCTG5Q9G717#direct=1), [prompthound#tw7t](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5KF8Z4EQRBX9NXSXTW7T#direct=1), [prompthound#0gtn](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5MRDEDY4XZEXXXD60GTN#direct=1), [prompthound#8v2p](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5HE4EYE5WH664YJ98V2P#direct=1) |
| [prompthound#gpbj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5NZZQZTXRB1VPR8BGPBJ#direct=1) | Verify the production RSS workflow and document ingestion/history behavior | [prompthound#jr88](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5NBMXZP95HGWJFW2JR88#direct=1), [prompthound#c7xe](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5M3VAT11D5A0NY4JC7XE#direct=1) |

## Read-only SQL CLI

Epic: [prompthound#290c](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5268QCWM6AFW3GKC290C#direct=1). [SQL subcommand draft](PLAN_SQL_SUBCOMMAND.md). This draft originated in scrobbledb; the issues adapt its requirements to Prompthound and require verification of upstream signatures and side effects.

| Issue | Task | Prerequisites |
| --- | --- | --- |
| [prompthound#szv5](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5PPKFGDBRNRYGAFGSZV5#direct=1) | Implement core sql wrappers for query, tables, schema, and rows | None |
| [prompthound#y9kt](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5QARRGD30CTQV1K5Y9KT#direct=1) | Add sql schema inspection, export, and column-analysis wrappers | [prompthound#szv5](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5PPKFGDBRNRYGAFGSZV5#direct=1) |
| [prompthound#4w8m](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5R12HT5SCGEDYPMR4W8M#direct=1) | Add revision-aware full-text indexing and sql search support | [prompthound#parj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5GR7PYX6G6V1TS6PPARJ#direct=1), [prompthound#tw7t](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5KF8Z4EQRBX9NXSXTW7T#direct=1), [prompthound#szv5](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5PPKFGDBRNRYGAFGSZV5#direct=1) |
| [prompthound#jazf](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5RPDXNTT7XVT8T79JAZF#direct=1) | Adapt memory and plugins commands without injecting a database path | [prompthound#szv5](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5PPKFGDBRNRYGAFGSZV5#direct=1) |

## Datasette-backed HTTP and stdio MCP server

Epic: [prompthound#wshj](https://katatracker-bmd.exe.xyz/kata?issue=01M5RBVGSVYXB17J2Y9AMBWSHJ#direct=1). The existing user-requested MCP issue, reused and split into shared services, domain tools, transports, and final verification. The requested ../scrobblechat directory was absent; the issue documents the related ../scrobbledb reference.

| Issue | Task | Prerequisites |
| --- | --- | --- |
| [prompthound#nvst](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5SEB6RQQSW2XHPJANVST#direct=1) | Build shared embedded Datasette service for MCP tools | None |
| [prompthound#q8hm](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5T6HK7NW300RYRSMQ8HM#direct=1) | Expose feed, content, and revision-history MCP tools through Datasette | [prompthound#nvst](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5SEB6RQQSW2XHPJANVST#direct=1), [prompthound#c7xe](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5M3VAT11D5A0NY4JC7XE#direct=1), [prompthound#tw7t](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5KF8Z4EQRBX9NXSXTW7T#direct=1) |
| [prompthound#adez](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5V4SWKYFJYZSTDKFADEZ#direct=1) | Serve MCP over HTTP with Datasette-backed shared tools | [prompthound#nvst](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5SEB6RQQSW2XHPJANVST#direct=1) |
| [prompthound#jcxf](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5W52GBFSK76G5CQ0JCXF#direct=1) | Serve MCP over stdio using the same Datasette query service | [prompthound#nvst](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5SEB6RQQSW2XHPJANVST#direct=1) |
| [prompthound#sksg](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5X3QN53X2RH8ZN6PSKSG#direct=1) | Verify MCP transport parity and document client setup | [prompthound#q8hm](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5T6HK7NW300RYRSMQ8HM#direct=1), [prompthound#adez](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5V4SWKYFJYZSTDKFADEZ#direct=1), [prompthound#jcxf](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5W52GBFSK76G5CQ0JCXF#direct=1) |

## Available starting tasks

The verified initial prerequisite-free tasks are:

- [prompthound#0k7g](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5F9J0ATJZBJ9S8K10K7G#direct=1): Promote feed models and fingerprint contracts into the package.
- [prompthound#fc8j](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5G211DYXA6E11JNAFC8J#direct=1): Define progress events and committed-result accounting for feed workflows.
- [prompthound#8v2p](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5HE4EYE5WH664YJ98V2P#direct=1): Fix custom database initialization and truthful ingest error reporting.
- [prompthound#szv5](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5PPKFGDBRNRYGAFGSZV5#direct=1): Implement core sql wrappers for query, tables, schema, and rows.
- [prompthound#nvst](https://katatracker-bmd.exe.xyz/kata?issue=01M5RD5SEB6RQQSW2XHPJANVST#direct=1): Build shared embedded Datasette service for MCP tools.

Use `kata show <ref> --agent` for a task's full contract and relationships, and
`kata ready --agent` to discover current ready work. Claim a task only when starting
implementation. Verification-only children remain blocked until their implementation
prerequisites are complete.

The ingestion epic corresponds to the expanded PR #6 scope. SQL and MCP are related
follow-on deliverables. Writable SQL commands and the old SQL draft's speculative
later phases remain outside this breakdown.
