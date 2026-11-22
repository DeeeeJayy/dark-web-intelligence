"""Tests for the normalization stage."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.crawler.engine import CrawlResult
from src.extraction.parsers import parse_forum, parse_paste
from src.normalization.normalizer import normalize, _clean_text
from src.shared.models import SourceType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


def _make_crawl_result(url: str = "http://localhost:9999/forum_a", content: bytes = b"<html></html>") -> CrawlResult:
    return CrawlResult(
        source_id="src_test01",
        url=url,
        crawl_run_id="run_test001",
        http_status=200,
        raw_bytes=content,
        headers={"content-type": "text/html"},
        fetch_time=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        response_ms=120.0,
    )


class TestCleanText:
    def test_strips_control_chars(self):
        result = _clean_text("hello\x00world")
        assert "\x00" not in result
        assert "hello" in result

    def test_unicode_normalization(self):
        # ff (fullwidth) → ff
        result = _clean_text("\uff46\uff46")
        assert result == "ff"

    def test_collapses_whitespace(self):
        result = _clean_text("hello   world")
        assert result == "hello world"

    def test_empty_string(self):
        assert _clean_text("") == ""

    def test_preserves_newlines(self):
        result = _clean_text("line1\nline2")
        assert "line1" in result
        assert "line2" in result


class TestNormalize:
    def test_valid_forum_post(self):
        raw = (FIXTURES / "forum_post.html").read_bytes()
        crawl = _make_crawl_result(content=raw)
        extraction = parse_forum(raw, crawl.url)
        record = normalize(crawl, extraction, source_type="forum")

        assert record.source == "src_test01"
        assert record.url == crawl.url
        assert len(record.content) > 10
        assert record.source_type == SourceType.FORUM

    def test_provenance_attached(self):
        raw = (FIXTURES / "forum_post.html").read_bytes()
        crawl = _make_crawl_result(content=raw)
        extraction = parse_forum(raw, crawl.url)
        record = normalize(crawl, extraction, source_type="forum")

        assert record.provenance.source_id == "src_test01"
        assert record.provenance.crawl_run_id == "run_test001"
        assert record.provenance.http_status == 200
        assert record.provenance.fetch_time.tzinfo is not None

    def test_content_hash_in_provenance(self):
        raw = (FIXTURES / "forum_post.html").read_bytes()
        crawl = _make_crawl_result(content=raw)
        extraction = parse_forum(raw, crawl.url)
        record = normalize(crawl, extraction)
        assert record.provenance.content_hash_sha256 is not None
        assert len(record.provenance.content_hash_sha256) == 64

    def test_extraction_error_marks_invalid(self):
        from src.extraction.parsers import ExtractionResult
        crawl = _make_crawl_result()
        bad_extraction = ExtractionResult(error="parse_failed", content="")
        record = normalize(crawl, bad_extraction)
        assert not record.is_valid

    def test_paste_normalization(self):
        raw = (FIXTURES / "paste_content.html").read_bytes()
        crawl = _make_crawl_result(url="http://localhost:9999/paste_b", content=raw)
        extraction = parse_paste(raw, crawl.url)
        record = normalize(crawl, extraction, source_type="paste")
        assert record.source_type == SourceType.PASTE
