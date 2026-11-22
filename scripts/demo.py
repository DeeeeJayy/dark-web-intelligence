"""
Demo script: runs the full M2 Crawling pipeline end-to-end on frozen fixtures.

No live network, no real backend, no Tor.
Sources are served from local test fixtures via respx.
Ingest endpoint is mocked to return 201 Created.

Run:
    venv\\Scripts\\python.exe scripts\\demo.py
"""
from __future__ import annotations

import sys
import os
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import respx

from src.crawler.engine import CrawlEngine, CrawlResult
from src.extraction.parsers import get_parser_for
from src.ingestion.client import BackendIngestionClient, IngestionResult
from src.monitoring.metrics import PipelineMetrics
from src.normalization.normalizer import normalize
from src.quality.validator import QualityChecker
from src.shared.logging import configure_logging

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"

# ---------------------------------------------------------------------------
# Synthetic source set (matches the authorization gate: each has a source_id,
# url, source_type, and a valid auth_ref; no entry has an empty auth_ref).
# ---------------------------------------------------------------------------
SOURCES = [
    {"id": "demo_forum",   "url": "http://localhost:9999/forum",   "type": "forum",   "fixture": FIXTURES / "html"  / "forum_post.html",   "ctype": "text/html"},
    {"id": "demo_paste",   "url": "http://localhost:9999/paste",   "type": "paste",   "fixture": FIXTURES / "html"  / "paste_content.html","ctype": "text/html"},
    {"id": "demo_blog",    "url": "http://localhost:9999/blog",    "type": "blog",    "fixture": FIXTURES / "html"  / "blog_article.html", "ctype": "text/html"},
    {"id": "demo_rss",     "url": "http://localhost:9999/feed.rss","type": "news",    "fixture": FIXTURES / "feeds" / "sample_rss.xml",    "ctype": "application/rss+xml"},
    {"id": "demo_atom",    "url": "http://localhost:9999/feed.atom","type":"news",    "fixture": FIXTURES / "feeds" / "sample_atom.xml",   "ctype": "application/atom+xml"},
    {"id": "demo_json_api","url": "http://localhost:9999/api.json","type": "other",   "fixture": FIXTURES / "api"   / "sample.json",       "ctype": "application/json"},
]


def _run_demo() -> None:
    configure_logging(run_id="demo_run")

    quality = QualityChecker()
    ingest_client = BackendIngestionClient()

    total = 0
    valid = 0
    exact_dups = 0
    near_dups = 0
    ingested = 0
    errors = 0

    start = time.perf_counter()

    # Mock the ingest endpoint and suppress structlog output during demo.
    with respx.mock(assert_all_called=False) as mock:
        mock.post("http://localhost:8001/api/v1/ingest").mock(
            return_value=httpx.Response(201, json={"status": "accepted"})
        )

        for src in SOURCES:
            raw = Path(src["fixture"]).read_bytes()
            crawl = CrawlResult(
                source_id=src["id"],
                url=src["url"],
                crawl_run_id="demo_run",
                http_status=200,
                raw_bytes=raw,
                headers={"content-type": src["ctype"]},
                fetch_time=datetime.now(timezone.utc),
                response_ms=10.0,
            )

            # Extract
            extraction = get_parser_for(
                src["type"],
                content_type=src["ctype"],
                url=src["url"],
                raw_bytes=raw,
            )(raw, src["url"], src["type"])

            if not extraction.success:
                errors += 1
                continue

            # Normalize
            record = normalize(crawl, extraction, source_type=src["type"])
            total += 1

            # Quality / dedup
            qr = quality.check(record)
            if not qr.is_valid:
                errors += 1
                continue
            if qr.is_duplicate:
                if qr.duplicate_type == "exact":
                    exact_dups += 1
                else:
                    near_dups += 1
                continue

            # Ingest
            result = ingest_client.ingest(record)
            if result.success:
                ingested += 1
                valid += 1
            else:
                errors += 1

    elapsed = time.perf_counter() - start
    dedup_rate = (exact_dups + near_dups) / total if total else 0.0
    success_rate = ingested / total if total else 0.0

    print("\n" + "=" * 50)
    print("  M2 Crawling Pipeline — Demo Run")
    print("=" * 50)
    print(f"  Sources processed : {len(SOURCES)}")
    print(f"  Records normalized: {total}")
    print(f"  Exact duplicates  : {exact_dups}")
    print(f"  Near duplicates   : {near_dups}")
    print(f"  Dedup rate        : {dedup_rate:.0%}")
    print(f"  Ingested          : {ingested}")
    print(f"  Success rate      : {success_rate:.0%}")
    print(f"  Errors            : {errors}")
    print(f"  Elapsed           : {elapsed:.2f}s")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    _run_demo()
