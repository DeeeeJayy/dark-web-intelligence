"""
Celery app + task definitions for the crawl queue.

Tasks:
- crawl_source: fetch one source URL, run full pipeline, ingest result
- health_check_source: probe one source and update health state
- run_scheduler: scan registry for due sources, enqueue crawl tasks
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from celery import Celery
from celery.utils.log import get_task_logger

from src.shared.config import settings

celery_app = Celery(
    "cyart_m2",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "src.crawler.queue.crawl_source": {"queue": "crawl"},
        "src.crawler.queue.health_check_source": {"queue": "health"},
        "src.crawler.queue.run_scheduler": {"queue": "scheduler"},
    },
)

logger = get_task_logger(__name__)


@celery_app.task(
    bind=True,
    name="src.crawler.queue.crawl_source",
    max_retries=settings.max_retries,
    default_retry_delay=settings.retry_backoff_base,
)
def crawl_source(self, source_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Full pipeline task for one source."""
    run_id = run_id or str(uuid.uuid4())[:8]
    logger.info(f"[{run_id}] crawl_source start: {source_id}")

    # Import here to avoid circular imports at module load
    from src.pipeline.runner import run_pipeline_for_source

    try:
        result = run_pipeline_for_source(source_id=source_id, run_id=run_id)
        logger.info(f"[{run_id}] crawl_source done: {source_id} → {result}")
        return result
    except Exception as exc:
        logger.error(f"[{run_id}] crawl_source error: {source_id}: {exc}")
        try:
            raise self.retry(exc=exc, countdown=int(settings.retry_backoff_base ** self.request.retries))
        except self.MaxRetriesExceededError:
            # Dead-letter: mark as dead in DB
            from src.pipeline.runner import mark_source_dead
            mark_source_dead(source_id=source_id, error=str(exc))
            raise


@celery_app.task(name="src.crawler.queue.health_check_source")
def health_check_source(source_id: str) -> dict[str, Any]:
    """Probe a source and update its health state."""
    from src.pipeline.runner import probe_source_health
    return probe_source_health(source_id=source_id)


@celery_app.task(name="src.crawler.queue.run_scheduler")
def run_scheduler() -> dict[str, Any]:
    """Scan registry for due sources and enqueue crawl tasks."""
    from src.pipeline.runner import schedule_due_sources
    return schedule_due_sources()
