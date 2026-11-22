"""
Crawl engine: fetch authorized sources with rate limiting, retries,
incremental support (ETag/Last-Modified/content digest), and structured logging.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.shared.config import settings
from src.shared.logging import get_logger

log = get_logger(__name__)


@dataclass
class CrawlResult:
    """Raw output of one HTTP fetch."""
    source_id: str
    url: str
    crawl_run_id: str
    http_status: int | None
    raw_bytes: bytes
    headers: dict[str, str]
    fetch_time: datetime
    response_ms: float
    error: str | None = None
    skipped: bool = False          # True when incremental check says "not modified"
    skip_reason: str | None = None

    @property
    def content_hash_sha256(self) -> str:
        return hashlib.sha256(self.raw_bytes).hexdigest()

    @property
    def success(self) -> bool:
        return (
            self.error is None
            and not self.skipped
            and self.http_status is not None
            and 200 <= self.http_status < 400
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "crawl_run_id": self.crawl_run_id,
            "http_status": self.http_status,
            "content_hash_sha256": self.content_hash_sha256,
            "fetch_time": self.fetch_time.isoformat(),
            "response_ms": self.response_ms,
            "error": self.error,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "content_length": len(self.raw_bytes),
        }


class RateLimiter:
    """Token-bucket rate limiter (pages per minute)."""

    def __init__(self, rate_ppm: int) -> None:
        self._rate_ppm = max(1, rate_ppm)
        self._interval = 60.0 / self._rate_ppm
        self._last_call: float = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        sleep_for = self._interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last_call = time.monotonic()


class CrawlEngine:
    """
    Fetches one URL at a time with:
    - Tor SOCKS proxy if configured
    - Rate limiting (pages per minute)
    - Incremental support via ETag / Last-Modified / content digest
    - Exponential-backoff retries (max 3)
    - Structured provenance logging
    """

    def __init__(self) -> None:
        # httpx 0.28 removed proxies=; store a single proxy scalar and pass proxy=.
        self._proxy = settings.tor_socks_proxy or None
        self._timeout = settings.request_timeout
        self._rate_limiter = RateLimiter(settings.crawl_rate_limit)

    def _build_client(self) -> httpx.Client:
        kwargs: dict[str, Any] = {
            "timeout": self._timeout,
            "follow_redirects": True,
            "headers": {
                "User-Agent": "CyArt-M2-Crawler/1.0 (authorized research)",
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy
        return httpx.Client(**kwargs)

    def fetch(
        self,
        source_id: str,
        url: str,
        crawl_run_id: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        previous_hash: str | None = None,
    ) -> CrawlResult:
        """Fetch a single URL. Returns CrawlResult with skipped=True if unchanged."""
        crawl_run_id = crawl_run_id or str(uuid.uuid4())[:8]

        self._rate_limiter.wait()

        log.info(
            "crawl_fetch_start",
            source_id=source_id,
            url=url,
            crawl_run_id=crawl_run_id,
        )

        t0 = time.monotonic()
        try:
            result = self._fetch_with_retry(
                source_id=source_id,
                url=url,
                crawl_run_id=crawl_run_id,
                etag=etag,
                last_modified=last_modified,
                previous_hash=previous_hash,
                t0=t0,
            )
        except RetryError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log.error("crawl_fetch_failed", source_id=source_id, url=url, error=str(exc))
            result = CrawlResult(
                source_id=source_id,
                url=url,
                crawl_run_id=crawl_run_id,
                http_status=None,
                raw_bytes=b"",
                headers={},
                fetch_time=datetime.now(timezone.utc),
                response_ms=elapsed_ms,
                error=str(exc),
            )

        log.info(
            "crawl_fetch_done",
            source_id=source_id,
            url=url,
            success=result.success,
            skipped=result.skipped,
            status=result.http_status,
            response_ms=round(result.response_ms, 1),
        )
        return result

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=settings.retry_backoff_base, min=2, max=30),
        reraise=False,
    )
    def _fetch_with_retry(
        self,
        source_id: str,
        url: str,
        crawl_run_id: str,
        etag: str | None,
        last_modified: str | None,
        previous_hash: str | None,
        t0: float,
    ) -> CrawlResult:
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        with self._build_client() as client:
            try:
                resp = client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError):
                raise
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000
                return CrawlResult(
                    source_id=source_id,
                    url=url,
                    crawl_run_id=crawl_run_id,
                    http_status=None,
                    raw_bytes=b"",
                    headers={},
                    fetch_time=datetime.now(timezone.utc),
                    response_ms=elapsed_ms,
                    error=str(exc),
                )

        elapsed_ms = (time.monotonic() - t0) * 1000
        fetch_time = datetime.now(timezone.utc)

        # 304 Not Modified
        if resp.status_code == 304:
            return CrawlResult(
                source_id=source_id,
                url=url,
                crawl_run_id=crawl_run_id,
                http_status=304,
                raw_bytes=b"",
                headers=dict(resp.headers),
                fetch_time=fetch_time,
                response_ms=elapsed_ms,
                skipped=True,
                skip_reason="304_not_modified",
            )

        raw_bytes = resp.content

        # Digest-based skip
        if previous_hash and previous_hash == hashlib.sha256(raw_bytes).hexdigest():
            return CrawlResult(
                source_id=source_id,
                url=url,
                crawl_run_id=crawl_run_id,
                http_status=resp.status_code,
                raw_bytes=raw_bytes,
                headers=dict(resp.headers),
                fetch_time=fetch_time,
                response_ms=elapsed_ms,
                skipped=True,
                skip_reason="content_unchanged",
            )

        return CrawlResult(
            source_id=source_id,
            url=url,
            crawl_run_id=crawl_run_id,
            http_status=resp.status_code,
            raw_bytes=raw_bytes,
            headers=dict(resp.headers),
            fetch_time=fetch_time,
            response_ms=elapsed_ms,
        )
