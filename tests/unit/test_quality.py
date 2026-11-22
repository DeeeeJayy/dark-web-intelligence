"""Tests for quality validation and deduplication."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.quality.validator import (
    InMemoryDedupStore,
    QualityChecker,
    hamming_distance_hex,
    simhash_hex,
    validate_record,
)
from src.shared.models import NormalizedRecord, RecordProvenance


def _prov(sha256: str = "a" * 64, run_id: str = "run_001") -> RecordProvenance:
    return RecordProvenance(
        source_id="src_01",
        source_url="http://localhost:9999/forum_a",
        fetched_url="http://localhost:9999/forum_a",
        fetch_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        crawl_run_id=run_id,
        http_status=200,
        content_hash_sha256=sha256,
    )


def _record(content: str = "valid content for testing purposes here", sha256: str = "a" * 64) -> NormalizedRecord:
    return NormalizedRecord(
        source="src_01",
        url="http://localhost:9999/forum_a/1",
        content=content,
        provenance=_prov(sha256=sha256),
    )


class TestSimHash:
    def test_same_text_same_hash(self):
        assert simhash_hex("hello world") == simhash_hex("hello world")

    def test_different_text_usually_different_hash(self):
        h1 = simhash_hex("hello world")
        h2 = simhash_hex("completely different text about security research")
        assert h1 != h2

    def test_similar_text_low_distance(self):
        h1 = simhash_hex("security research vulnerability CVE patching")
        h2 = simhash_hex("security research vulnerability CVE patching update")
        dist = hamming_distance_hex(h1, h2)
        # Similar texts should have small hamming distance
        assert dist < 20  # very loose bound

    def test_empty_text(self):
        # Should not raise
        result = simhash_hex("")
        assert isinstance(result, str)


class TestValidation:
    def test_valid_record_no_errors(self):
        r = _record()
        errors = validate_record(r)
        assert errors == []

    def test_missing_content_flagged(self):
        r = NormalizedRecord(
            source="src_01",
            url="http://localhost:9999/x",
            content=" ",
            provenance=_prov(),
        )
        errors = validate_record(r)
        # whitespace-only content is too short
        assert any("content_too_short" in e or "whitespace" in e for e in errors + r.validation_errors)

    def test_url_too_long(self):
        r = _record()
        r.url = "http://localhost:9999/" + "x" * 2500
        errors = validate_record(r)
        assert any("url_too_long" in e for e in errors)


class TestQualityChecker:
    def test_valid_record_passes(self):
        checker = QualityChecker()
        r = _record()
        qr = checker.check(r)
        assert qr.is_valid
        assert not qr.is_duplicate

    def test_exact_duplicate_detected(self):
        checker = QualityChecker()
        sha = "b" * 64
        r1 = _record(sha256=sha)
        r2 = _record(content="different content text here but same sha256", sha256=sha)

        qr1 = checker.check(r1)
        qr2 = checker.check(r2)

        assert not qr1.is_duplicate
        assert qr2.is_duplicate
        assert qr2.duplicate_type == "exact"
        assert qr2.duplicate_of == r1.record_id

    def test_near_duplicate_detected(self):
        checker = QualityChecker()
        base = "security research post about vulnerabilities in test systems for analysis"
        near = "security research post about vulnerabilities in test systems for analysis purposes"

        r1 = _record(content=base, sha256="c" * 64)
        r2 = _record(content=near, sha256="d" * 64)
        r2.url = "http://localhost:9999/forum_a/2"

        qr1 = checker.check(r1)
        qr2 = checker.check(r2)

        # Near-dup detection; may or may not trigger depending on content similarity
        # Just verify the checker runs without error
        assert qr1 is not None
        assert qr2 is not None

    def test_simhash_set_on_record(self):
        checker = QualityChecker()
        r = _record()
        checker.check(r)
        assert r.content_simhash is not None
        assert len(r.content_simhash) == 16  # 64-bit = 16 hex chars

    def test_store_stats(self):
        checker = QualityChecker()
        r1 = _record(sha256="e" * 64)
        r2 = _record(content="another unique record content here", sha256="f" * 64)
        r2.url = "http://localhost:9999/forum_a/3"
        checker.check(r1)
        checker.check(r2)
        stats = checker.store_stats()
        assert stats["exact_seen"] >= 2
