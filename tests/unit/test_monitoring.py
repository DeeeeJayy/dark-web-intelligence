"""Tests for pipeline metrics."""
from src.monitoring.metrics import PipelineMetrics


class TestPipelineMetrics:
    def test_initial_state(self):
        m = PipelineMetrics(run_id="test_001")
        assert m.fetched == 0
        assert m.crawl_success_rate == 0.0

    def test_record_fetch_success(self):
        m = PipelineMetrics(run_id="test_002")
        m.record_fetch(success=True, skipped=False, response_ms=200.0)
        assert m.fetched == 1
        assert m.fetch_failed == 0
        assert m.crawl_success_rate == 1.0

    def test_record_fetch_failure(self):
        m = PipelineMetrics(run_id="test_003")
        m.record_fetch(success=False, skipped=False, response_ms=5000.0)
        assert m.fetch_failed == 1
        assert m.crawl_success_rate == 0.0

    def test_record_fetch_skipped(self):
        m = PipelineMetrics(run_id="test_004")
        m.record_fetch(success=True, skipped=True, response_ms=50.0)
        assert m.fetch_skipped == 1
        assert m.fetched == 0

    def test_duplicate_rate_calculation(self):
        m = PipelineMetrics(run_id="test_005")
        m.record_quality(valid=True, duplicate=True, dup_type="exact")
        m.record_quality(valid=True, duplicate=True, dup_type="near")
        m.record_ingestion(success=True, skipped=False)
        m.record_ingestion(success=True, skipped=False)
        assert m.duplicate_rate == 0.5

    def test_avg_fetch_ms(self):
        m = PipelineMetrics(run_id="test_006")
        m.record_fetch(success=True, skipped=False, response_ms=100.0)
        m.record_fetch(success=True, skipped=False, response_ms=300.0)
        assert m.avg_fetch_ms == 200.0

    def test_completeness(self):
        m = PipelineMetrics(run_id="test_007")
        m.record_normalization(success=True)
        m.record_normalization(success=True)
        m.record_quality(valid=True, duplicate=False, dup_type=None)
        m.record_quality(valid=False, duplicate=False, dup_type=None)
        assert m.completeness == 0.5

    def test_to_dict_has_all_keys(self):
        m = PipelineMetrics(run_id="test_008")
        d = m.to_dict()
        required = [
            "run_id", "fetched", "fetch_failed", "crawl_success_rate",
            "duplicate_rate", "ingested", "avg_fetch_ms", "completeness"
        ]
        for key in required:
            assert key in d, f"Missing key: {key}"

    def test_report_string(self):
        m = PipelineMetrics(run_id="test_009")
        report = m.report()
        assert "Pipeline Health Report" in report
        assert "run_id" in report
