"""Tests for the NormalizedRecord schema (the versioned data contract)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.shared.models import (
    SCHEMA_VERSION,
    NormalizedRecord,
    RecordProvenance,
    SourceType,
)


def _make_provenance(**kwargs):
    defaults = dict(
        source_id="src_test01",
        source_url="http://localhost:9999/forum_a",
        fetched_url="http://localhost:9999/forum_a",
        fetch_time=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        crawl_run_id="run_abc123",
        http_status=200,
        content_hash_sha256="a" * 64,
    )
    defaults.update(kwargs)
    return RecordProvenance(**defaults)


def _make_record(**kwargs):
    defaults = dict(
        source="src_test01",
        url="http://localhost:9999/forum_a/thread/1",
        content="This is valid test content for the pipeline.",
        provenance=_make_provenance(),
    )
    defaults.update(kwargs)
    return NormalizedRecord(**defaults)


class TestSchemaVersion:
    def test_schema_version_present(self):
        r = _make_record()
        assert r.schema_version == SCHEMA_VERSION

    def test_schema_version_is_string(self):
        assert isinstance(SCHEMA_VERSION, str)


class TestRequiredFields:
    def test_valid_record_created(self):
        r = _make_record()
        assert r.source == "src_test01"
        assert r.url == "http://localhost:9999/forum_a/thread/1"
        assert r.content == "This is valid test content for the pipeline."

    def test_record_id_auto_generated(self):
        r = _make_record()
        assert r.record_id
        assert len(r.record_id) > 10

    def test_two_records_have_different_ids(self):
        r1 = _make_record()
        r2 = _make_record()
        assert r1.record_id != r2.record_id

    def test_missing_source_raises(self):
        with pytest.raises(Exception):
            NormalizedRecord(
                source="",
                url="http://localhost:9999/x",
                content="content",
                provenance=_make_provenance(),
            )

    def test_missing_url_raises(self):
        with pytest.raises(Exception):
            NormalizedRecord(
                source="src_test01",
                url="",
                content="content",
                provenance=_make_provenance(),
            )


class TestTimestamps:
    def test_collected_at_defaults_to_utc(self):
        r = _make_record()
        assert r.collected_at.tzinfo is not None

    def test_naive_fetch_time_gets_utc(self):
        prov = _make_provenance(fetch_time=datetime(2026, 9, 1, 12, 0, 0))
        r = _make_record(provenance=prov)
        assert r.provenance.fetch_time.tzinfo is not None

    def test_naive_published_at_gets_utc(self):
        r = _make_record(published_at=datetime(2026, 9, 1, 12, 0, 0))
        assert r.published_at.tzinfo is not None


class TestContentValidation:
    def test_whitespace_only_content_marks_invalid(self):
        r = _make_record(content="   \n\t  ")
        assert not r.is_valid
        assert any("whitespace" in e for e in r.validation_errors)


class TestSourceType:
    def test_default_source_type(self):
        r = _make_record()
        assert r.source_type == SourceType.OTHER

    def test_forum_source_type(self):
        r = _make_record(source_type=SourceType.FORUM)
        assert r.source_type == SourceType.FORUM


class TestToIngestDict:
    def test_ingest_dict_excludes_internal_fields(self):
        r = _make_record()
        d = r.to_ingest_dict()
        assert "is_duplicate" not in d
        assert "duplicate_of" not in d
        assert "validation_errors" not in d
        assert "is_valid" not in d

    def test_ingest_dict_has_required_fields(self):
        r = _make_record()
        d = r.to_ingest_dict()
        assert "source" in d
        assert "url" in d
        assert "content" in d
        assert "schema_version" in d
        assert "provenance" in d
