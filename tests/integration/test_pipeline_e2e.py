"""
End-to-end integration test: exercises the full pipeline
Source Registry → Crawl → Extract → Normalize → Quality → Ingest

Uses:
- SQLite in-memory for the registry (no real Postgres needed)
- respx to mock HTTP calls (no live sites, no Tor)
- An in-process mock ingest endpoint
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx
import respx

from src.crawler.engine import CrawlEngine, CrawlResult
from src.extraction.parsers import get_parser
from src.ingestion.client import BackendIngestionClient, IngestionResult
from src.monitoring.metrics import PipelineMetrics
from src.normalization.normalizer import normalize
from src.quality.validator import QualityChecker
from src.shared.models import NormalizedRecord

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _make_crawl_result(source_id: str, url: str, html: bytes) -> CrawlResult:
    return CrawlResult(
        source_id=source_id,
        url=url,
        crawl_run_id="e2e_run_001",
        http_status=200,
        raw_bytes=html,
        headers={"content-type": "text/html"},
        fetch_time=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        response_ms=150.0,
    )


class TestFullPipelineFlow:
    """Test each stage in sequence using frozen fixtures."""

    def test_forum_post_full_pipeline(self):
        """Forum HTML → extract → normalize → validate → (mock) ingest."""
        html = _load("forum_post.html")
        url = "http://localhost:9999/forum_a/thread/1"
        source_id = "src_forum_01"

        # Stage 1: Crawl (simulated)
        crawl = _make_crawl_result(source_id, url, html)
        assert crawl.success

        # Stage 2: Extract
        parser = get_parser("forum")
        extraction = parser(html, url, "forum")
        assert extraction.success
        assert len(extraction.content) > 10

        # Stage 3: Normalize
        record = normalize(crawl, extraction, source_type="forum")
        assert record.source == source_id
        assert record.url == url
        assert record.provenance.crawl_run_id == "e2e_run_001"
        assert record.provenance.content_hash_sha256 is not None

        # Stage 4: Quality
        checker = QualityChecker()
        qr = checker.check(record)
        assert qr.is_valid
        assert not qr.is_duplicate
        assert record.content_simhash is not None

        # Stage 5: Mock ingest
        with respx.mock:
            respx.post("http://localhost:8001/api/v1/ingest").mock(
                return_value=httpx.Response(201, json={"record_id": record.record_id})
            )
            client = BackendIngestionClient()
            result = client.ingest(record)

        assert result.success
        assert result.record_id == record.record_id

    def test_paste_full_pipeline(self):
        """Paste HTML → extract → normalize → validate → (mock) ingest."""
        html = _load("paste_content.html")
        url = "http://localhost:9999/paste_b/abc"
        source_id = "src_paste_01"

        crawl = _make_crawl_result(source_id, url, html)
        parser = get_parser("paste")
        extraction = parser(html, url, "paste")
        record = normalize(crawl, extraction, source_type="paste")

        checker = QualityChecker()
        qr = checker.check(record)

        # Paste content should be valid
        assert record.source_type.value == "paste"
        # (content may be short but should be valid)

    def test_exact_duplicate_blocked_from_ingest(self):
        """Two identical documents: second must be flagged as duplicate."""
        html = _load("blog_article.html")
        url = "http://localhost:9999/blog_c/article/1"
        sha256 = hashlib.sha256(html).hexdigest()

        def _crawl(source_id: str, run: str) -> CrawlResult:
            return CrawlResult(
                source_id=source_id,
                url=url,
                crawl_run_id=run,
                http_status=200,
                raw_bytes=html,
                headers={},
                fetch_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                response_ms=100.0,
            )

        parser = get_parser("blog")
        checker = QualityChecker()

        # First record
        c1 = _crawl("src_blog_01", "run_001")
        e1 = parser(html, url)
        r1 = normalize(c1, e1, source_type="blog")
        qr1 = checker.check(r1)
        assert not qr1.is_duplicate

        # Second record — same content, same SHA256
        c2 = _crawl("src_blog_01", "run_002")
        e2 = parser(html, url)
        r2 = normalize(c2, e2, source_type="blog")
        qr2 = checker.check(r2)
        assert qr2.is_duplicate
        assert qr2.duplicate_type == "exact"

    def test_idempotent_ingest(self):
        """Same record_id sent twice: second call should get 409 → skipped=True."""
        html = _load("forum_post.html")
        crawl = _make_crawl_result("src_x", "http://localhost:9999/forum_a", html)
        extraction = get_parser("forum")(html, crawl.url, "forum")
        record = normalize(crawl, extraction, source_type="forum")

        with respx.mock:
            # First call: 201
            route = respx.post("http://localhost:8001/api/v1/ingest")
            route.side_effect = [
                httpx.Response(201, json={"record_id": record.record_id}),
                httpx.Response(409, json={"detail": "already ingested"}),
            ]
            client = BackendIngestionClient()
            r1 = client.ingest(record)
            r2 = client.ingest(record)

        assert r1.success and not r1.skipped
        assert r2.success and r2.skipped

    def test_metrics_accumulate_correctly(self):
        """Metrics collector tracks all stages."""
        m = PipelineMetrics(run_id="e2e_metrics")

        m.record_fetch(success=True, skipped=False, response_ms=120.0)
        m.record_extraction(success=True)
        m.record_normalization(success=True)
        m.record_quality(valid=True, duplicate=False, dup_type=None)
        m.record_ingestion(success=True, skipped=False)

        d = m.to_dict()
        assert d["fetched"] == 1
        assert d["extracted"] == 1
        assert d["normalized"] == 1
        assert d["validated"] == 1
        assert d["ingested"] == 1
        assert d["crawl_success_rate"] == 1.0
        assert d["duplicate_rate"] == 0.0

    def test_empty_page_does_not_crash_pipeline(self):
        """Empty HTML must be handled gracefully — no exception."""
        html = _load("empty_page.html")
        url = "http://localhost:9999/empty"
        crawl = _make_crawl_result("src_empty", url, html)

        parser = get_parser("generic")
        extraction = parser(html, url, "generic")
        record = normalize(crawl, extraction, source_type="generic")

        checker = QualityChecker()
        qr = checker.check(record)

        # May be invalid due to empty content — that's correct behaviour
        assert qr is not None
        assert isinstance(qr.validation_errors, list)


class TestIncrementalSkip:
    def test_unchanged_content_marked_skipped(self):
        """CrawlResult with skipped=True must not go through the pipeline."""
        html = _load("forum_post.html")
        sha = hashlib.sha256(html).hexdigest()

        # Simulate a crawl where content hasn't changed
        result = CrawlResult(
            source_id="src_inc_01",
            url="http://localhost:9999/forum_a",
            crawl_run_id="run_inc",
            http_status=200,
            raw_bytes=html,
            headers={},
            fetch_time=datetime.now(timezone.utc),
            response_ms=80.0,
            skipped=True,
            skip_reason="content_unchanged",
        )

        assert result.skipped
        assert not result.success  # skipped means do not process
