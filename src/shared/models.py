"""
Normalized record schema — the versioned data contract between pipeline stages.

This is the SINGLE source of truth for the shape of a crawled, extracted,
and normalized document. No stage may alter the field set after Phase 1.

Schema version: 1.0
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


SCHEMA_VERSION = "1.0"


class SourceType(str, Enum):
    FORUM = "forum"
    PASTE = "paste"
    BLOG = "blog"
    MARKETPLACE = "marketplace"
    NEWS = "news"
    OTHER = "other"


class SourceHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    RECOVERED = "recovered"
    UNKNOWN = "unknown"


class RecordProvenance(BaseModel):
    """Immutable provenance attached to every record."""
    source_id: str = Field(..., description="Registry source ID")
    source_url: str = Field(..., description="Canonical source URL")
    fetched_url: str = Field(..., description="Actual URL fetched")
    fetch_time: datetime = Field(..., description="UTC fetch timestamp")
    crawl_run_id: str = Field(..., description="Crawl run / intent ID")
    http_status: int | None = Field(None, description="HTTP status code")
    content_hash_sha256: str | None = Field(None, description="SHA-256 of raw bytes")

    @field_validator("fetch_time", mode="before")
    @classmethod
    def ensure_utc(cls, v: Any) -> datetime:
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v


class NormalizedRecord(BaseModel):
    """
    The canonical output of the M2 pipeline.
    Produced by the normalization stage; consumed by quality/dedup and backend ingestion.
    """
    schema_version: str = Field(default=SCHEMA_VERSION)
    record_id: str = Field(default_factory=lambda: str(uuid4()))

    # --- required fields ---
    source: str = Field(..., min_length=1, description="Source ID from registry")
    url: str = Field(..., min_length=1, description="Canonical URL of this document")
    content: str = Field(..., min_length=1, description="Extracted main text content")

    # --- timestamps ---
    published_at: datetime | None = Field(None, description="Publication timestamp from source")
    collected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when collected",
    )

    # --- metadata ---
    title: str | None = Field(None)
    author: str | None = Field(None)
    language: str | None = Field(None, description="ISO 639-1 language code")
    source_type: SourceType = Field(default=SourceType.OTHER)
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list, description="Extracted hyperlinks")
    extra: dict[str, Any] = Field(default_factory=dict, description="Source-specific metadata")

    # --- provenance ---
    provenance: RecordProvenance

    # --- quality / dedup fields (set by quality stage) ---
    content_simhash: str | None = Field(None, description="SimHash fingerprint")
    is_duplicate: bool = Field(default=False)
    duplicate_of: str | None = Field(None, description="record_id of original if duplicate")
    validation_errors: list[str] = Field(default_factory=list)
    is_valid: bool = Field(default=True)

    @field_validator("collected_at", "published_at", mode="before")
    @classmethod
    def ensure_utc_timestamps(cls, v: Any) -> Any:
        if v is None:
            return v
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if isinstance(v, datetime) and v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def content_not_whitespace_only(self) -> "NormalizedRecord":
        if not self.content.strip():
            self.validation_errors.append("content is whitespace-only")
            self.is_valid = False
        return self

    def to_ingest_dict(self) -> dict[str, Any]:
        """Serialise for backend ingestion. Excludes internal quality fields."""
        d = self.model_dump(mode="json")
        # Remove internal pipeline fields not needed downstream
        d.pop("is_duplicate", None)
        d.pop("duplicate_of", None)
        d.pop("validation_errors", None)
        d.pop("is_valid", None)
        return d
