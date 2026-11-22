"""
Normalization stage: converts a CrawlResult + ExtractionResult into
the versioned NormalizedRecord.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timezone

from src.crawler.engine import CrawlResult
from src.extraction.parsers import ExtractionResult
from src.shared.logging import get_logger
from src.shared.models import NormalizedRecord, RecordProvenance, SourceType

log = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_text(text: str) -> str:
    """Unicode normalization, control char removal, whitespace collapse."""
    if not text:
        return ""
    # Unicode NFKC normalization
    text = unicodedata.normalize("NFKC", text)
    # Strip control characters (keep \n, \r, \t)
    text = _CONTROL_CHARS_RE.sub("", text)
    # Collapse multiple whitespace runs (preserve single newlines)
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def _infer_source_type(source_type_str: str) -> SourceType:
    try:
        return SourceType(source_type_str.lower())
    except ValueError:
        return SourceType.OTHER


def normalize(
    crawl_result: CrawlResult,
    extraction: ExtractionResult,
    source_type: str = "other",
) -> NormalizedRecord:
    """
    Produce a NormalizedRecord from raw crawl + extraction outputs.
    Every record gets full provenance attached.
    """
    provenance = RecordProvenance(
        source_id=crawl_result.source_id,
        source_url=crawl_result.url,
        fetched_url=crawl_result.url,
        fetch_time=crawl_result.fetch_time,
        crawl_run_id=crawl_result.crawl_run_id,
        http_status=crawl_result.http_status,
        content_hash_sha256=crawl_result.content_hash_sha256 if crawl_result.raw_bytes else None,
    )

    content = _clean_text(extraction.content)
    title = _clean_text(extraction.title or "")
    author = _clean_text(extraction.author or "") or None

    # Handle published_at timezone
    published_at = extraction.published_at
    if published_at is not None and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    record = NormalizedRecord(
        source=crawl_result.source_id,
        url=crawl_result.url,
        content=content if content else " ",  # prevent empty-content validation error; will be caught by quality
        title=title or None,
        author=author,
        published_at=published_at,
        source_type=_infer_source_type(source_type),
        links=extraction.links or [],
        tags=extraction.tags or [],
        extra=extraction.extra or {},
        provenance=provenance,
    )

    # Flag empty content explicitly
    if not content:
        record.validation_errors.append("content_empty_after_normalization")
        record.is_valid = False

    if extraction.error:
        record.validation_errors.append(f"extraction_error:{extraction.error}")
        record.is_valid = False

    log.info(
        "normalized",
        record_id=record.record_id,
        source_id=crawl_result.source_id,
        content_len=len(content),
        is_valid=record.is_valid,
    )
    return record
