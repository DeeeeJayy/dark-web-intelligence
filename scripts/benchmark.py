"""
In-process baseline benchmark for the M2 pipeline (FEAT-004).

Builds a synthetic dataset (frozen HTML/feed/JSON fixtures + generated variants
with a known exact/near-duplicate distribution) and runs each item through the
REAL in-process pipeline:

    extract (get_parser_for) -> normalize -> QualityChecker.check

Ingestion is mocked with respx (no live network, no real backend) so ingest
latency is measured against the mock sink. Throughput, per-stage avg latency
(extract/normalize/quality/ingest), success rate, and dedup rate are MEASURED
with time.perf_counter and never fabricated.

Run:
    venv\\Scripts\\python.exe scripts\\benchmark.py
"""
from __future__ import annotations

import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import respx
import structlog

from src.crawler.engine import CrawlResult
from src.extraction.parsers import get_parser_for
from src.ingestion.client import BackendIngestionClient
from src.normalization.normalizer import normalize
from src.quality.validator import QualityChecker
from src.shared.config import settings

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _load(rel: str) -> bytes:
    return (FIXTURES / rel).read_bytes()


def _paste_html(token: str, repeats: int = 30, suffix: str = "") -> bytes:
    """A synthetic paste-style page whose <pre> body is a dominant repeated
    token. The extractor yields that body, so the SimHash is dominated by the
    token (distinct per token; near variants share it -> Hamming 0)."""
    body = " ".join([token] * repeats)
    if suffix:
        body += " " + suffix
    return (
        "<html><head><title>Bench</title></head>"
        f"<body><pre>{body}</pre></body></html>"
    ).encode("utf-8")


# Frozen fixtures: (relative_path, source_type, content_type, url_base)
_FIXTURES = [
    ("html/forum_post.html", "forum", "text/html", "http://localhost:9999/bench/forum"),
    ("html/paste_content.html", "paste", "text/html", "http://localhost:9999/bench/paste"),
    ("html/blog_article.html", "blog", "text/html", "http://localhost:9999/bench/blog"),
    ("feeds/sample_rss.xml", "news", "application/rss+xml", "http://localhost:9999/bench/rss"),
    ("feeds/sample_atom.xml", "news", "application/atom+xml", "http://localhost:9999/bench/atom"),
    ("api/sample.json", "other", "application/json", "http://localhost:9999/bench/json"),
]


def _build_dataset(n: int) -> list[dict]:
    """Synthetic dataset of n items: 6 real fixtures (format coverage) +
    generated synthetic uniques + near-duplicate pairs + exact-duplicate copies.

    Distribution (sized to n, originals precede their duplicates):
      - 6 frozen fixtures (one per format)            -> unique
      - synthetic unique paste docs                     -> unique
      - near-duplicate bases + variants                 -> variant is a near dup
      - exact-duplicate copies (reuse a unique's raw)   -> copy is an exact dup
    """
    items: list[dict] = []
    for rel, st, ct, base in _FIXTURES:
        items.append({"raw": _load(rel), "url": f"{base}/0", "source_type": st, "content_type": ct})

    fixture_count = len(items)
    n_near_pairs = max(0, n // 14)
    n_exact = max(0, n // 7)
    n_uniq = n - fixture_count - 2 * n_near_pairs - n_exact
    if n_uniq < 1:
        # Guarantee at least one synthetic unique (referenced by exact copies).
        n_exact = max(0, n - fixture_count - 2 * n_near_pairs - 1)
        n_uniq = n - fixture_count - 2 * n_near_pairs - n_exact

    # Synthetic unique paste docs (distinct dominant token -> distinct SimHash).
    for k in range(n_uniq):
        token = f"benchuniq{k}"
        items.append({
            "raw": _paste_html(token, 30),
            "url": f"http://localhost:9999/bench/uniq/{k}",
            "source_type": "paste",
            "content_type": "text/html",
        })

    uniq_start = fixture_count  # index of first synthetic unique

    # Near-duplicate bases.
    for k in range(n_near_pairs):
        token = f"benchnear{k}"
        items.append({
            "raw": _paste_html(token, 30),
            "url": f"http://localhost:9999/bench/near_base/{k}",
            "source_type": "paste",
            "content_type": "text/html",
        })

    # Near-duplicate variants (same dominant token + one extra word -> Hamming 0).
    for k in range(n_near_pairs):
        token = f"benchnear{k}"
        items.append({
            "raw": _paste_html(token, 30, suffix="update"),
            "url": f"http://localhost:9999/bench/near_var/{k}",
            "source_type": "paste",
            "content_type": "text/html",
        })

    # Exact-duplicate copies (reuse a synthetic unique's raw verbatim -> same sha256).
    uniq_count = max(1, n_uniq)
    for k in range(n_exact):
        src = items[uniq_start + (k % uniq_count)]
        items.append({
            "raw": src["raw"],
            "url": f"http://localhost:9999/bench/exact/{k}",
            "source_type": src["source_type"],
            "content_type": src["content_type"],
        })

    return items


def _crawl_result(item: dict, run_id: str) -> CrawlResult:
    return CrawlResult(
        source_id="bench_src",
        url=item["url"],
        crawl_run_id=run_id,
        http_status=200,
        raw_bytes=item["raw"],
        headers={"content-type": item["content_type"]},
        fetch_time=datetime.now(timezone.utc),
        response_ms=0.0,
    )


def run_benchmark(n: int = 300) -> dict:
    """Run the in-process pipeline over a synthetic dataset of n items and
    return MEASURED throughput / per-stage latency / success / dedup metrics.

    No live network: ingestion is mocked via respx. Numbers are real.
    """
    items = _build_dataset(n)
    run_id = "bench_run"
    quality = QualityChecker()
    client = BackendIngestionClient()
    ingest_url = settings.backend_ingest_url

    extract_ms: list[float] = []
    normalize_ms: list[float] = []
    quality_ms: list[float] = []
    ingest_ms: list[float] = []

    total = 0
    success = 0
    exact = 0
    near = 0
    ingested = 0

    with respx.mock:
        respx.post(ingest_url).mock(
            return_value=httpx.Response(201, json={"record_id": "bench"})
        )
        t_start = time.perf_counter()
        for item in items:
            crawl = _crawl_result(item, run_id)

            # Stage: extract (format-aware parser selection).
            t0 = time.perf_counter()
            parser = get_parser_for(
                item["source_type"],
                content_type=item["content_type"],
                url=item["url"],
                raw_bytes=item["raw"],
            )
            extraction = parser(item["raw"], item["url"], item["source_type"])
            extract_ms.append((time.perf_counter() - t0) * 1000.0)

            # Stage: normalize.
            t0 = time.perf_counter()
            record = normalize(crawl, extraction, source_type=item["source_type"])
            normalize_ms.append((time.perf_counter() - t0) * 1000.0)

            # Stage: quality (validate + dedup).
            t0 = time.perf_counter()
            qr = quality.check(record)
            quality_ms.append((time.perf_counter() - t0) * 1000.0)

            total += 1
            if extraction.success and qr.is_valid:
                success += 1
            if qr.duplicate_type == "exact":
                exact += 1
            elif qr.duplicate_type == "near":
                near += 1

            # Stage: ingest (only valid, non-duplicate records) via the mock sink.
            if qr.is_valid and not qr.is_duplicate:
                t0 = time.perf_counter()
                client.ingest(record)
                ingest_ms.append((time.perf_counter() - t0) * 1000.0)
                ingested += 1

        elapsed = time.perf_counter() - t_start

    throughput = total / elapsed if elapsed > 0 else 0.0
    success_rate = success / total if total else 0.0
    dedup_rate = (exact + near) / total if total else 0.0

    return {
        "n": n,
        "total": total,
        "throughput": round(throughput, 2),
        "avg_extract_ms": round(fmean(extract_ms), 4) if extract_ms else 0.0,
        "avg_normalize_ms": round(fmean(normalize_ms), 4) if normalize_ms else 0.0,
        "avg_quality_ms": round(fmean(quality_ms), 4) if quality_ms else 0.0,
        "avg_ingest_ms": round(fmean(ingest_ms), 4) if ingest_ms else 0.0,
        "success_rate": round(success_rate, 4),
        "dedup_rate": round(dedup_rate, 4),
        "exact_duplicates": exact,
        "near_duplicates": near,
        "ingested": ingested,
        "elapsed_seconds": round(elapsed, 4),
    }


def _format_report(m: dict) -> str:
    lines = [
        "=== M2 In-Process Baseline Benchmark (FEAT-004) ===",
        f"items: {m['total']}",
        f"throughput: {m['throughput']} records/s",
        f"avg_extract_ms: {m['avg_extract_ms']}",
        f"avg_normalize_ms: {m['avg_normalize_ms']}",
        f"avg_quality_ms: {m['avg_quality_ms']}",
        f"avg_ingest_ms: {m['avg_ingest_ms']}",
        f"success_rate: {m['success_rate']}",
        f"dedup_rate: {m['dedup_rate']}",
        f"exact_duplicates: {m['exact_duplicates']}",
        f"near_duplicates: {m['near_duplicates']}",
        f"ingested: {m['ingested']}",
        f"elapsed_seconds: {m['elapsed_seconds']}",
    ]
    return "\n".join(lines)


def _quiet_logging() -> None:
    """Suppress info-level structlog output so the benchmark prints only its
    structured report to stdout (warnings/errors still surface). Called only
    when run as a script, not from the smoke test, so it never mutates global
    logging config during the test session."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )


if __name__ == "__main__":
    _quiet_logging()
    print(_format_report(run_benchmark(n=300)))
