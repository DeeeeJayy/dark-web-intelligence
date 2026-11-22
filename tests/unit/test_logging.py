"""Tests for shared logging configuration.

Regression guard: configure_logging() must not raise on the first log call.
Previously add_logger_name was chained with PrintLoggerFactory, whose
PrintLogger has no .name, raising AttributeError on the first log.
"""
from src.shared.logging import configure_logging, get_logger


class TestConfigureLogging:
    def test_configure_logging_then_log_does_not_raise(self):
        # configure_logging + an actual log call must complete without raising.
        run_id = configure_logging(run_id="test_log")
        log = get_logger("test_logging")
        log.info("pipeline_event", stage="crawl", source_id="src-1")
        assert run_id == "test_log"

    def test_configure_logging_returns_generated_run_id(self):
        # When no run_id is supplied, one is generated (run/trace-id stays intact).
        run_id = configure_logging()
        log = get_logger("test_logging")
        log.warning("warn_event", stage="quality")
        assert isinstance(run_id, str)
        assert len(run_id) > 0

    def test_error_level_log_does_not_raise(self):
        configure_logging(run_id="test_err")
        log = get_logger("test_logging")
        log.error("error_event", stage="ingestion", reason="boom")
