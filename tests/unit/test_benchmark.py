"""Smoke test for the in-process baseline benchmark (FEAT-004).

Verifies run_benchmark returns the expected measured keys, success_rate is in
[0, 1], and it runs without live network (run_benchmark opens its own
respx.mock context for ingestion). Numbers are real, not asserted to fixed
values.
"""
from __future__ import annotations

from scripts.benchmark import run_benchmark


REQUIRED_KEYS = {
    "throughput",
    "avg_extract_ms",
    "avg_normalize_ms",
    "avg_quality_ms",
    "success_rate",
    "dedup_rate",
}


class TestBenchmark:
    def test_run_benchmark_returns_measured_metrics(self):
        result = run_benchmark(n=50)

        assert isinstance(result, dict)
        assert REQUIRED_KEYS.issubset(result.keys()), result.keys()
        assert result["total"] == 50
        assert 0.0 <= result["success_rate"] <= 1.0
        assert 0.0 <= result["dedup_rate"] <= 1.0
        assert result["throughput"] > 0
        # Latency averages are non-negative measured numbers.
        for k in ("avg_extract_ms", "avg_normalize_ms", "avg_quality_ms", "avg_ingest_ms"):
            assert result[k] >= 0.0, k

    def test_run_benchmark_runs_without_network(self):
        # run_benchmark mocks ingestion via respx, so valid non-dup records are
        # "ingested" against the mock sink with no live network.
        result = run_benchmark(n=50)
        assert result["ingested"] > 0
        assert result["exact_duplicates"] > 0
        assert result["near_duplicates"] > 0
