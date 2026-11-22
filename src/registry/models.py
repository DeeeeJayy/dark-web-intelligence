"""ORM models for the source registry."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.registry.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(String(32), default="other")

    # Authorization — a source with no auth_ref CANNOT be queued (hard gate)
    auth_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_authorized: Mapped[bool] = mapped_column(Boolean, default=False)

    # Crawl policy
    crawl_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "depth": 1,
            "rate_ppm": 30,
            "timeout_s": 45,
            "respect_robots": True,
            "max_pages": 100,
        },
    )
    crawl_frequency_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Health
    health_status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_crawl_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_response_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # Incremental crawl state
    crawl_cursor: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    def is_queueable(self) -> bool:
        """Hard gate: a source MUST have auth_ref and is_authorized=True to crawl."""
        return bool(self.auth_ref) and self.is_authorized and self.is_active

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "source_type": self.source_type,
            "auth_ref": self.auth_ref,
            "is_authorized": self.is_authorized,
            "crawl_policy": self.crawl_policy,
            "crawl_frequency_seconds": self.crawl_frequency_seconds,
            "is_active": self.is_active,
            "health_status": self.health_status,
            "last_crawl_at": self.last_crawl_at.isoformat() if self.last_crawl_at else None,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "crawl_cursor": self.crawl_cursor,
        }


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    crawl_run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="scheduled")  # scheduled|incremental|manual
    state: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|done|failed|dead
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
