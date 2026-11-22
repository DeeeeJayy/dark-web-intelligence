"""
Backend ingestion adapter.

Sends validated, non-duplicate NormalizedRecords to the backend via HTTP POST.
Idempotency: uses record_id as the idempotency key so retries never double-ingest.

The real backend interface is documented in docs/INTERFACE.md.
When BACKEND_INGEST_URL points to the mock, the mock echoes back the record_id.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.shared.config import settings
from src.shared.logging import get_logger
from src.shared.models import NormalizedRecord

log = get_logger(__name__)


@dataclass
class IngestionResult:
    record_id: str
    success: bool
    status_code: int | None = None
    error: str | None = None
    skipped: bool = False   # True if backend said "already ingested"


class BackendIngestionClient:
    """
    HTTP client for backend ingestion.
    - Attaches X-Idempotency-Key: <record_id>
    - Attaches X-API-Key from config
    - Retries on transient errors (3 attempts, exponential backoff)
    """

    def __init__(self) -> None:
        self._url = settings.backend_ingest_url
        self._api_key = settings.backend_api_key

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=10),
    )
    def ingest(self, record: NormalizedRecord) -> IngestionResult:
        """POST a single record. Only call for valid, non-duplicate records."""
        payload = record.to_ingest_dict()

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    self._url,
                    json=payload,
                    headers={
                        "X-Idempotency-Key": record.record_id,
                        "X-API-Key": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            raise
        except Exception as exc:
            log.error("ingest_error", record_id=record.record_id, error=str(exc))
            return IngestionResult(record_id=record.record_id, success=False, error=str(exc))

        if resp.status_code == 409:
            # Already ingested (idempotent)
            log.info("ingest_skipped_duplicate", record_id=record.record_id)
            return IngestionResult(
                record_id=record.record_id,
                success=True,
                status_code=resp.status_code,
                skipped=True,
            )

        if resp.status_code in (200, 201):
            log.info("ingest_success", record_id=record.record_id, status=resp.status_code)
            return IngestionResult(
                record_id=record.record_id,
                success=True,
                status_code=resp.status_code,
            )

        log.error(
            "ingest_failed",
            record_id=record.record_id,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return IngestionResult(
            record_id=record.record_id,
            success=False,
            status_code=resp.status_code,
            error=resp.text[:200],
        )

    def ingest_batch(self, records: list[NormalizedRecord]) -> list[IngestionResult]:
        results = []
        for rec in records:
            results.append(self.ingest(rec))
        return results
