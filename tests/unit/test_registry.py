"""Tests for the source registry — uses SQLite in-memory for isolation."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.registry.db import Base
from src.registry.models import Source, CrawlJob
from src.registry.service import AuthorizationError, RegistryService


@pytest.fixture
def db_session():
    """In-memory SQLite session for tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def svc(db_session):
    return RegistryService(db_session)


class TestAddSource:
    def test_add_authorized_source(self, svc):
        s = svc.add_source(
            name="Test Forum",
            url="http://localhost:9999/forum_a",
            source_type="forum",
            auth_ref="AUTH-2026-001",
        )
        assert s.id.startswith("src_")
        assert s.is_authorized
        assert s.is_queueable()

    def test_add_source_without_auth_ref_raises(self, svc):
        with pytest.raises(AuthorizationError):
            svc.add_source(
                name="Bad Source",
                url="http://localhost:9999/no_auth",
                auth_ref="",  # empty — must reject
            )

    def test_duplicate_url_raises(self, svc):
        svc.add_source(name="S1", url="http://localhost:9999/x", auth_ref="AUTH-001")
        with pytest.raises(Exception):
            svc.add_source(name="S2", url="http://localhost:9999/x", auth_ref="AUTH-002")


class TestAuthorizationGate:
    def test_queueable_requires_auth_ref(self, svc):
        s = svc.add_source(name="S", url="http://localhost:9999/y", auth_ref="AUTH-X")
        assert s.is_queueable()

    def test_not_queueable_when_not_authorized(self, db_session):
        # Create source directly with is_authorized=False
        s = Source(
            id="src_manual",
            name="Manual",
            url="http://localhost:9999/z",
            auth_ref=None,
            is_authorized=False,
        )
        db_session.add(s)
        db_session.commit()
        assert not s.is_queueable()

    def test_create_job_blocked_without_auth(self, db_session):
        s = Source(
            id="src_noauth",
            name="No Auth",
            url="http://localhost:9999/noauth",
            auth_ref=None,
            is_authorized=False,
        )
        db_session.add(s)
        db_session.commit()
        svc = RegistryService(db_session)
        with pytest.raises(AuthorizationError):
            svc.create_job("src_noauth", "run_001")


class TestHealthUpdates:
    def test_success_sets_healthy(self, svc):
        s = svc.add_source(name="H", url="http://localhost:9999/h", auth_ref="AUTH-H")
        s = svc.update_health(s.id, success=True, response_ms=100.0)
        assert s.health_status == "healthy"
        assert s.consecutive_failures == 0

    def test_one_failure_sets_degraded(self, svc):
        s = svc.add_source(name="D", url="http://localhost:9999/d", auth_ref="AUTH-D")
        s = svc.update_health(s.id, success=False)
        assert s.health_status == "degraded"
        assert s.consecutive_failures == 1

    def test_three_failures_sets_unavailable(self, svc):
        s = svc.add_source(name="U", url="http://localhost:9999/u", auth_ref="AUTH-U")
        for _ in range(3):
            s = svc.update_health(s.id, success=False)
        assert s.health_status == "unavailable"

    def test_success_after_failure_resets(self, svc):
        s = svc.add_source(name="R", url="http://localhost:9999/r", auth_ref="AUTH-R")
        svc.update_health(s.id, success=False)
        s = svc.update_health(s.id, success=True)
        assert s.health_status == "healthy"
        assert s.consecutive_failures == 0


class TestHealthClassifier:
    """Extended health states: recovered, slow-response, stale age, low ratio."""

    def test_recovered_after_unavailable(self, svc):
        s = svc.add_source(name="R", url="http://localhost:9999/rec", auth_ref="AUTH-REC")
        for _ in range(3):
            s = svc.update_health(s.id, success=False)
        assert s.health_status == "unavailable"
        # a success after being unavailable -> recovered
        s = svc.update_health(s.id, success=True, response_ms=100.0)
        assert s.health_status == "recovered"
        assert s.consecutive_failures == 0

    def test_slow_response_degraded(self, svc):
        s = svc.add_source(name="S", url="http://localhost:9999/slow", auth_ref="AUTH-SLOW")
        s = svc.update_health(s.id, success=True, response_ms=3000.0)
        assert s.health_status == "degraded"

    def test_stale_last_success(self, svc, db_session):
        from datetime import datetime, timedelta, timezone

        s = svc.add_source(name="ST", url="http://localhost:9999/stale", auth_ref="AUTH-ST")
        svc.update_health(s.id, success=True, response_ms=100.0)  # fresh success -> healthy
        assert svc.get_source(s.id).health_status == "healthy"

        # force the last success into the distant past -> stale for the probe
        s = svc.get_source(s.id)
        s.last_success_at = datetime.now(timezone.utc) - timedelta(days=2)
        db_session.commit()

        result = svc.evaluate_health(s.id)
        assert result in ("degraded", "unavailable")

    def test_low_success_ratio(self, svc):
        s = svc.add_source(name="L", url="http://localhost:9999/low", auth_ref="AUTH-LOW")
        svc.update_health(s.id, success=False)
        svc.update_health(s.id, success=False)
        # 1 success vs 2 failures -> ratio 0.33 < 0.5 -> degraded
        s = svc.update_health(s.id, success=True, response_ms=50.0)
        assert s.health_status == "degraded"

    def test_evaluate_health_unknown_source(self, svc):
        assert svc.evaluate_health("src_does_not_exist") is None

    def test_evaluate_health_fresh_stays_stored(self, svc):
        s = svc.add_source(name="F", url="http://localhost:9999/fresh", auth_ref="AUTH-F")
        svc.update_health(s.id, success=True, response_ms=100.0)
        assert svc.evaluate_health(s.id) == "healthy"


class TestCursorAndEtag:
    def test_update_cursor(self, svc):
        s = svc.add_source(name="C", url="http://localhost:9999/c", auth_ref="AUTH-C")
        svc.update_cursor(s.id, {"last_id": "post_42"})
        s2 = svc.get_source(s.id)
        assert s2.crawl_cursor == {"last_id": "post_42"}

    def test_update_etag(self, svc):
        s = svc.add_source(name="E", url="http://localhost:9999/e", auth_ref="AUTH-E")
        svc.update_etag(s.id, '"abc123"', "Mon, 01 Sep 2026 00:00:00 GMT")
        s2 = svc.get_source(s.id)
        assert s2.etag == '"abc123"'


class TestListQueueable:
    def test_only_authorized_returned(self, db_session):
        s1 = Source(id="src_a1", name="A1", url="http://localhost:9999/a1",
                    auth_ref="AUTH-1", is_authorized=True, is_active=True)
        s2 = Source(id="src_a2", name="A2", url="http://localhost:9999/a2",
                    auth_ref=None, is_authorized=False, is_active=True)
        db_session.add_all([s1, s2])
        db_session.commit()

        svc = RegistryService(db_session)
        queueable = svc.list_queueable_sources()
        ids = [s.id for s in queueable]
        assert "src_a1" in ids
        assert "src_a2" not in ids
