"""Source registry CRUD and authorization gate."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.registry.models import CrawlJob, Source
from src.shared.config import settings
from src.shared.logging import get_logger
from src.shared.models import SourceHealth

log = get_logger(__name__)


def _as_aware_utc(dt: datetime) -> datetime:
    """SQLite returns stored datetimes as offset-naive; normalize to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class AuthorizationError(Exception):
    """Raised when a source lacks a valid authorization record."""


class RegistryService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Source CRUD
    # ------------------------------------------------------------------

    def add_source(
        self,
        *,
        name: str,
        url: str,
        source_type: str = "other",
        auth_ref: str,
        auth_note: str = "",
        crawl_policy: dict[str, Any] | None = None,
        crawl_frequency_seconds: int = 3600,
    ) -> Source:
        """Register a new source. auth_ref is mandatory."""
        if not auth_ref:
            raise AuthorizationError("auth_ref is required to register a source")

        source_id = f"src_{uuid.uuid4().hex[:12]}"
        policy = crawl_policy or {
            "depth": 1,
            "rate_ppm": 30,
            "timeout_s": 45,
            "respect_robots": True,
            "max_pages": 100,
        }
        source = Source(
            id=source_id,
            name=name,
            url=url,
            source_type=source_type,
            auth_ref=auth_ref,
            auth_note=auth_note,
            is_authorized=True,
            crawl_policy=policy,
            crawl_frequency_seconds=crawl_frequency_seconds,
        )
        self._db.add(source)
        self._db.commit()
        self._db.refresh(source)
        log.info("source_registered", source_id=source_id, url=url)
        return source

    def get_source(self, source_id: str) -> Source | None:
        return self._db.query(Source).filter(Source.id == source_id).first()

    def get_source_by_url(self, url: str) -> Source | None:
        return self._db.query(Source).filter(Source.url == url).first()

    def list_queueable_sources(self) -> list[Source]:
        """Return only sources that pass the authorization hard gate."""
        return [
            s for s in self._db.query(Source).filter(Source.is_active == True).all()
            if s.is_queueable()
        ]

    def list_all_sources(self) -> list[Source]:
        return self._db.query(Source).all()

    def update_health(
        self,
        source_id: str,
        *,
        success: bool,
        response_ms: float = 0.0,
    ) -> Source | None:
        source = self.get_source(source_id)
        if not source:
            return None

        now = datetime.now(timezone.utc)
        source.last_crawl_at = now

        # Capture the prior failure count BEFORE mutating so _classify_health
        # can detect the unavailable -> success (recovered) transition.
        prev_consecutive_failures = source.consecutive_failures

        if success:
            source.consecutive_failures = 0
            source.success_count += 1
            source.last_success_at = now
        else:
            source.consecutive_failures += 1
            source.failure_count += 1

        # Update rolling average response time
        total = source.success_count + source.failure_count
        source.avg_response_ms = (
            (source.avg_response_ms * (total - 1) + response_ms) / total
            if total > 0
            else response_ms
        )

        # Classify health
        source.health_status = self._classify_health(
            source, prev_consecutive_failures, response_ms=response_ms, now=now
        )
        self._db.commit()
        return source

    @staticmethod
    def _classify_health(
        source: Source,
        prev_consecutive_failures: int,
        response_ms: float = 0.0,
        now: datetime | None = None,
    ) -> str:
        """Classify health from success ratio, just-succeeded response time,
        and last-success age.

        Success path (latest event was a success -> consecutive_failures == 0):
          RECOVERED when the source was previously unavailable (>=3 failures),
          else DEGRADED on a slow response or low success ratio, else HEALTHY.
        Failure path (latest event was a failure -> consecutive_failures >= 1):
          UNAVAILABLE when failures are sustained (>=3) or the last success is
          stale, else DEGRADED.
        """
        if now is None:
            now = datetime.now(timezone.utc)

        total = source.success_count + source.failure_count
        ratio = (source.success_count / total) if total > 0 else 1.0
        last = source.last_success_at
        last_stale = last is not None and (
            now - _as_aware_utc(last)
        ).total_seconds() > settings.health_stale_success_age_s

        if source.consecutive_failures == 0:
            # latest event was a success
            if prev_consecutive_failures >= 3:
                return SourceHealth.RECOVERED.value
            if (
                response_ms > settings.health_slow_response_ms
                or ratio < settings.health_low_success_ratio
            ):
                return SourceHealth.DEGRADED.value
            return SourceHealth.HEALTHY.value

        # latest event was a failure
        if source.consecutive_failures >= 3 or last_stale:
            return SourceHealth.UNAVAILABLE.value
        return SourceHealth.DEGRADED.value

    def evaluate_health(self, source_id: str, now: datetime | None = None) -> str | None:
        """Recompute the health state from last-success age alone (no crawl event).

        Used by the scheduler / probe between crawls: a source whose last
        success is stale (older than ``health_stale_success_age_s``) or has
        never succeeded degrades / becomes unavailable; otherwise the stored
        state is returned. Returns ``None`` if the source does not exist.
        """
        source = self.get_source(source_id)
        if not source:
            return None
        if now is None:
            now = datetime.now(timezone.utc)

        last = source.last_success_at
        stale = last is None or (
            now - _as_aware_utc(last)
        ).total_seconds() > settings.health_stale_success_age_s
        if stale:
            if source.consecutive_failures >= 3:
                return SourceHealth.UNAVAILABLE.value
            return SourceHealth.DEGRADED.value
        return source.health_status

    def update_cursor(self, source_id: str, cursor: dict[str, Any]) -> None:
        source = self.get_source(source_id)
        if source:
            source.crawl_cursor = cursor
            self._db.commit()

    def update_etag(
        self, source_id: str, etag: str | None, last_modified: str | None
    ) -> None:
        source = self.get_source(source_id)
        if source:
            source.etag = etag
            source.last_modified = last_modified
            self._db.commit()

    # ------------------------------------------------------------------
    # Crawl jobs
    # ------------------------------------------------------------------

    def create_job(self, source_id: str, run_id: str, kind: str = "scheduled") -> CrawlJob:
        """Create a crawl job. Raises AuthorizationError if source is not queueable."""
        source = self.get_source(source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        if not source.is_queueable():
            raise AuthorizationError(
                f"Source {source_id} cannot be queued: missing auth_ref or not authorized"
            )
        job = CrawlJob(
            id=f"job_{uuid.uuid4().hex[:12]}",
            source_id=source_id,
            crawl_run_id=run_id,
            kind=kind,
        )
        self._db.add(job)
        self._db.commit()
        self._db.refresh(job)
        return job

    def update_job(self, job_id: str, **kwargs: Any) -> CrawlJob | None:
        job = self._db.query(CrawlJob).filter(CrawlJob.id == job_id).first()
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            self._db.commit()
        return job
