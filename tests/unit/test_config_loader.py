"""Tests for config-driven source registration (JSON config loader).

Uses the in-memory SQLite db_session fixture pattern from tests/unit/test_registry.py.
No live network; no real backend. The authorization hard gate is exercised:
entries with empty/missing auth_ref are skipped, never registered.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.registry.config_loader import load_sources_from_config
from src.registry.db import Base
from src.registry.service import RegistryService


@pytest.fixture
def db_session():
    """In-memory SQLite session for tests."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def svc(db_session):
    return RegistryService(db_session)


REAL_CONFIG = Path(__file__).resolve().parents[2] / "config" / "sources.json"


def _write_config(tmp_path: Path, entries: list[dict]) -> Path:
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps(entries), encoding="utf-8")
    return cfg


class TestConfigLoader:
    def test_loads_authorized_sources(self, svc, tmp_path):
        cfg = _write_config(
            tmp_path,
            [
                {
                    "name": "Forum A",
                    "url": "http://localhost:9999/a",
                    "source_type": "forum",
                    "auth_ref": "AUTH-1",
                    "crawl_frequency_seconds": 3600,
                },
                {
                    "name": "Paste B",
                    "url": "http://localhost:9999/b",
                    "source_type": "paste",
                    "auth_ref": "AUTH-2",
                    "crawl_frequency_seconds": 900,
                },
            ],
        )
        summary = load_sources_from_config(str(cfg), svc)
        assert summary == {"registered": 2, "skipped": 0, "errors": 0}

        a = svc.get_source_by_url("http://localhost:9999/a")
        b = svc.get_source_by_url("http://localhost:9999/b")
        assert a is not None and a.is_queueable()
        assert b is not None and b.is_queueable()

    def test_empty_auth_ref_skipped_not_registered(self, svc, tmp_path):
        cfg = _write_config(
            tmp_path,
            [
                {
                    "name": "No Auth",
                    "url": "http://localhost:9999/noauth",
                    "source_type": "marketplace",
                    "auth_ref": "",
                    "crawl_frequency_seconds": 3600,
                },
                {
                    "name": "Forum A",
                    "url": "http://localhost:9999/a",
                    "source_type": "forum",
                    "auth_ref": "AUTH-1",
                    "crawl_frequency_seconds": 3600,
                },
            ],
        )
        summary = load_sources_from_config(str(cfg), svc)
        # the no-auth entry is skipped (hard gate), the authorized one registered
        assert summary["registered"] == 1
        assert summary["skipped"] == 1
        assert summary["errors"] == 0

        assert svc.get_source_by_url("http://localhost:9999/noauth") is None
        a = svc.get_source_by_url("http://localhost:9999/a")
        assert a is not None
        assert a.is_queueable()

    def test_missing_auth_ref_key_skipped_not_registered(self, svc, tmp_path):
        cfg = _write_config(
            tmp_path,
            [
                {
                    "name": "Missing Auth Key",
                    "url": "http://localhost:9999/missing_auth",
                    "source_type": "blog",
                    "crawl_frequency_seconds": 3600,
                },
            ],
        )
        summary = load_sources_from_config(str(cfg), svc)
        assert summary["registered"] == 0
        assert summary["skipped"] == 1
        assert svc.get_source_by_url("http://localhost:9999/missing_auth") is None

    def test_idempotent_no_duplicates(self, svc, tmp_path):
        cfg = _write_config(
            tmp_path,
            [
                {
                    "name": "Forum A",
                    "url": "http://localhost:9999/a",
                    "source_type": "forum",
                    "auth_ref": "AUTH-1",
                    "crawl_frequency_seconds": 3600,
                },
                {
                    "name": "Paste B",
                    "url": "http://localhost:9999/b",
                    "source_type": "paste",
                    "auth_ref": "AUTH-2",
                    "crawl_frequency_seconds": 900,
                },
            ],
        )
        first = load_sources_from_config(str(cfg), svc)
        assert first == {"registered": 2, "skipped": 0, "errors": 0}

        second = load_sources_from_config(str(cfg), svc)
        assert second["registered"] == 0
        assert second["skipped"] == 2
        assert second["errors"] == 0

        # still only two sources — no duplicates
        assert len(svc.list_all_sources()) == 2

    def test_loads_real_committed_config(self, svc):
        assert REAL_CONFIG.exists(), "config/sources.json must be committed"
        summary = load_sources_from_config(str(REAL_CONFIG), svc)
        assert summary["errors"] == 0
        assert summary["registered"] >= 1

        # the no-auth entry is skipped, never registered
        assert svc.get_source_by_url("http://localhost:9999/no_auth_leak") is None

        # every registered source passes the authorization hard gate
        sources = svc.list_all_sources()
        assert sources, "expected at least one registered source from the real config"
        for s in sources:
            assert s.is_queueable(), f"source {s.id} ({s.url}) is not queueable"
