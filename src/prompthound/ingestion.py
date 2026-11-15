"""Coordinate parsing, committed source history, and independent HTML extraction."""

from dataclasses import dataclass
from datetime import datetime

from .events import ProgressCallback, emit
from .extraction import (
    EXTRACTOR_VERSION,
    ExtractionConfig,
    ExtractionResult,
    extract_pending,
)
from .parsing import canonical_json, parse_snapshot
from .storage import IngestResult, Store


@dataclass(frozen=True)
class SourceResult:
    ingestion: IngestResult
    extraction: ExtractionResult


def pending_extractions(store: Store) -> int:
    return store.conn.execute(
        """SELECT COUNT(*) FROM content_blocks b WHERE NOT EXISTS (
        SELECT 1 FROM content_extractions e WHERE e.block_id=b.id AND e.extractor_version=?
        AND e.config_json=? AND e.status='completed')""",
        (EXTRACTOR_VERSION, canonical_json(ExtractionConfig().model_dump())),
    ).fetchone()[0]


def ingest_source(
    store: Store,
    content: bytes,
    *,
    source_uri: str,
    capture_id: str | None = None,
    captured_at: datetime | None = None,
    progress: ProgressCallback | None = None,
) -> SourceResult:
    emit(progress, "parse", "started", source=source_uri)
    snapshot = parse_snapshot(
        content, source_uri=source_uri, capture_id=capture_id, captured_at=captured_at
    )
    emit(
        progress,
        "validate",
        "completed",
        source=source_uri,
        completed=len(snapshot.entries),
        total=len(snapshot.entries),
    )
    result = store.ingest(snapshot, content, progress=progress)
    try:
        extracted = extract_pending(store, result.snapshot_id, progress=progress)
    except Exception as exc:
        # A presentation/derived-write failure cannot undo already committed source history.
        emit(progress, "extract", "failed", source=source_uri, message=str(exc))
        extracted = ExtractionResult(failed=1, pending=pending_extractions(store))
    return SourceResult(result, extracted)
