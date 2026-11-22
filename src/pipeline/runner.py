"""
Pipeline runner: coordinates the full Source → Backend flow for one source.

Flow:
  Source Registry (auth gate)
  → Crawl Engine (fetch)
  → Extraction (parse)
  → Normalization (normalize)
  → Quality (validate + dedup)
  → Backend Ingestion (ingest valid, non-duplicate records)
  → Metrics update
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from src.crawler.engine import CrawlEngine, CrawlResult
from src.crawler.checkpoint import CrawlCheckpointStore
from src.extraction.parsers import ExtractionResult, get_parser_for
from src.ingestion.client import BackendIngestionClient
from src.monitoring.metrics import PipelineMetrics
from src.normalization.normalizer import normalize
from src.quality.validator import QualityChecker
from src.registry.db import SessionLocal
from src.registry.service import AuthorizationError, RegistryService
from src.shared.config import settings
from src.shared.logging import get_logger, configure_logging

log = get_logger(__name__)

# Module-level shared instances (reused across tasks in the same worker)
_crawl_engine = CrawlEngine()
_ingest_client = BackendIngestionClient()

# Lazy module-level checkpoint store (file-based SQLite) for crash/resume.
# Built on first use from settings.crawl_checkpoint_db; tests pass their own.
_checkpoint_store: CrawlCheckpointStore | None = None


def _get_default_checkpoint_store() -> CrawlCheckpointStore:
    global _checkpoint_store
    if _checkpoint_store is None:
        _checkpoint_store = CrawlCheckpointStore(settings.crawl_checkpoint_db)
    return _checkpoint_store


def run_pipeline_for_source(
    source_id: str,
    run_id: str | None = None,
    checkpoint_store: CrawlCheckpointStore | None = None,
) -> dict[str, Any]:
    """
    Run the complete pipeline for one source.
    Returns a metrics dict.
    Raises AuthorizationError if the source has no valid auth record.
    """
    run_id = run_id or str(uuid.uuid4())[:8]
    configure_logging(run_id=run_id)

    metrics = PipelineMetrics(run_id=run_id)
    db = SessionLocal()
    registry = RegistryService(db)
    quality = QualityChecker()
    store = checkpoint_store if checkpoint_store is not None else _get_default_checkpoint_store()

    try:
        # 1. Authorization gate
        source = registry.get_source(source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        if not source.is_queueable():
            raise AuthorizationError(
                f"Source {source_id} is not authorized for crawling"
            )

        log.info("pipeline_start", source_id=source_id, url=source.url, run_id=run_id)

        # Create job record
        job = registry.create_job(source_id=source_id, run_id=run_id)

        # Record an in-flight checkpoint so an interrupted run can resume.
        store.save_checkpoint(source_id, run_id, source.url, "running")

        # 2. Fetch
        crawl_result: CrawlResult = _crawl_engine.fetch(
            source_id=source_id,
            url=source.url,
            crawl_run_id=run_id,
            etag=source.etag,
            last_modified=source.last_modified,
            previous_hash=(source.crawl_cursor or {}).get("last_content_hash"),
        )

        # Update source health
        registry.update_health(
            source_id=source_id,
            success=crawl_result.success,
            response_ms=crawl_result.response_ms,
        )

        # Update ETag / Last-Modified for incremental
        new_etag = crawl_result.headers.get("etag") or crawl_result.headers.get("ETag")
        new_lm = crawl_result.headers.get("last-modified") or crawl_result.headers.get("Last-Modified")
        registry.update_etag(source_id, new_etag, new_lm)

        metrics.record_fetch(
            success=crawl_result.success,
            skipped=crawl_result.skipped,
            response_ms=crawl_result.response_ms,
        )

        if crawl_result.skipped:
            log.info("pipeline_skipped_incremental", source_id=source_id, reason=crawl_result.skip_reason)
            registry.update_job(job.id, state="done", stats=metrics.to_dict(),
                                finished_at=datetime.now(timezone.utc))
            store.mark_complete(source_id, run_id)
            return metrics.to_dict()

        if not crawl_result.success:
            log.warning("pipeline_fetch_failed", source_id=source_id, error=crawl_result.error)
            registry.update_job(job.id, state="failed", error=crawl_result.error,
                                finished_at=datetime.now(timezone.utc))
            store.save_checkpoint(source_id, run_id, source.url, "failed")
            return metrics.to_dict()

        # Update incremental cursor
        registry.update_cursor(source_id, {
            "last_content_hash": crawl_result.content_hash_sha256,
            "last_run_id": run_id,
        })

        # 3. Extract (format-aware: detect rss/atom/json from content-type/URL/sniff,
        # else fall back to the source-type parser)
        parser = get_parser_for(
            source.source_type,
            content_type=crawl_result.headers.get("content-type") or crawl_result.headers.get("Content-Type"),
            url=crawl_result.url,
            raw_bytes=crawl_result.raw_bytes,
        )
        extraction: ExtractionResult = parser(
            crawl_result.raw_bytes, crawl_result.url, source.source_type
        )
        metrics.record_extraction(success=extraction.success)

        if not extraction.success:
            log.warning("pipeline_extraction_failed", source_id=source_id, error=extraction.error)
            # Still continue to normalization; normalizer will flag it

        # 4. Normalize
        record = normalize(crawl_result, extraction, source_type=source.source_type)
        metrics.record_normalization(success=record.is_valid)

        # 5. Quality / dedup
        qr = quality.check(record)
        metrics.record_quality(
            valid=qr.is_valid,
            duplicate=qr.is_duplicate,
            dup_type=qr.duplicate_type,
        )

        # 6. Ingest (only valid, non-duplicate records)
        if qr.is_valid and not qr.is_duplicate:
            ingest_result = _ingest_client.ingest(record)
            metrics.record_ingestion(success=ingest_result.success, skipped=ingest_result.skipped)
        else:
            reason = "invalid" if not qr.is_valid else "duplicate"
            log.info("pipeline_record_skipped", source_id=source_id, reason=reason,
                     errors=qr.validation_errors)

        registry.update_job(
            job.id,
            state="done",
            stats=metrics.to_dict(),
            finished_at=datetime.now(timezone.utc),
        )

        store.mark_complete(source_id, run_id)
        log.info("pipeline_done", source_id=source_id, **metrics.to_dict())
        return metrics.to_dict()

    except AuthorizationError:
        # Gate blocked the run before any checkpoint was written; nothing to resume.
        raise
    except Exception as exc:
        # A crash leaves the 'running' checkpoint in place so resume can pick it up.
        log.error("pipeline_error", source_id=source_id, error=str(exc))
        raise
    finally:
        db.close()


def resume_interrupted_crawls(
    checkpoint_store: CrawlCheckpointStore | None = None,
) -> dict[str, Any]:
    """Re-run crawls that were interrupted (left in 'running'/'checkpointed').

    Lists interrupted checkpoints and re-executes each via ``run_pipeline_for_source``
    in-process, reusing the original run_id so the checkpoint row transitions to
    'done' on success. Does not touch the authorization gate or flow shape.
    """
    store = checkpoint_store if checkpoint_store is not None else _get_default_checkpoint_store()
    interrupted = store.list_interrupted()
    results: dict[str, Any] = {
        "interrupted": len(interrupted),
        "rerun": [],
        "errors": [],
    }
    log.info("resume_interrupted_start", interrupted=len(interrupted))

    for cp in interrupted:
        source_id = cp["source_id"]
        run_id = cp["run_id"]
        try:
            metrics = run_pipeline_for_source(
                source_id=source_id,
                run_id=run_id,
                checkpoint_store=store,
            )
            results["rerun"].append(
                {"source_id": source_id, "run_id": run_id, "metrics": metrics}
            )
        except Exception as exc:
            log.error(
                "resume_interrupted_error",
                source_id=source_id,
                run_id=run_id,
                error=str(exc),
            )
            results["errors"].append(
                {"source_id": source_id, "run_id": run_id, "error": str(exc)}
            )

    log.info(
        "resume_interrupted_done",
        rerun=len(results["rerun"]),
        errors=len(results["errors"]),
    )
    return results


def probe_source_health(source_id: str) -> dict[str, Any]:
    """Light probe: HEAD request only, updates health state."""
    db = SessionLocal()
    registry = RegistryService(db)
    try:
        source = registry.get_source(source_id)
        if not source:
            return {"error": "not_found"}

        import httpx
        import time
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.head(source.url)
            success = resp.status_code < 400
            response_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            success = False
            response_ms = (time.monotonic() - t0) * 1000

        source = registry.update_health(source_id, success=success, response_ms=response_ms)
        return {"source_id": source_id, "health": source.health_status if source else "unknown"}
    finally:
        db.close()


def mark_source_dead(source_id: str, error: str) -> None:
    """Mark a source as dead after max retries exceeded."""
    db = SessionLocal()
    registry = RegistryService(db)
    try:
        source = registry.get_source(source_id)
        if source:
            source.is_active = False
            source.health_status = "unavailable"
            db.commit()
            log.error("source_marked_dead", source_id=source_id, error=error)
    finally:
        db.close()


def schedule_due_sources() -> dict[str, Any]:
    """Find sources due for crawling and enqueue them."""
    from src.crawler.queue import crawl_source

    db = SessionLocal()
    registry = RegistryService(db)
    run_id = str(uuid.uuid4())[:8]

    try:
        sources = registry.list_queueable_sources()
        enqueued = 0
        skipped_unavailable = 0

        for source in sources:
            # Skip unavailable sources (they get a health probe instead)
            if source.health_status == "unavailable":
                skipped_unavailable += 1
                continue

            crawl_source.apply_async(
                kwargs={"source_id": source.id, "run_id": run_id},
                queue="crawl",
            )
            enqueued += 1

        log.info("scheduler_done", enqueued=enqueued, skipped_unavailable=skipped_unavailable)
        return {"run_id": run_id, "enqueued": enqueued, "skipped_unavailable": skipped_unavailable}
    finally:
        db.close()
