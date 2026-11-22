"""
CrawlEngine.fetch tests + incremental-crawl persistence tests.

Uses respx against http://localhost:9999/... and the frozen
tests/fixtures/html/forum_post.html bytes only — no live sites, no Tor.
"""
from __future__ import annotations

import hashlib

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.crawler.engine import CrawlEngine
from src.registry.db import Base
from src.registry.models import CrawlJob, Source  # noqa: F401  (registers tables on Base.metadata)
from src.registry.service import RegistryService

FIXTURES = __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "html"
URL = "http://localhost:9999/forum_a/thread/1"


def _forum_html() -> bytes:
    return (FIXTURES / "forum_post.html").read_bytes()


# ---------------------------------------------------------------------------
# CrawlEngine.fetch behaviour (respx, frozen fixtures only)
# ---------------------------------------------------------------------------


class TestFetch:
    def test_200_success_sets_status_and_hash(self):
        html = _forum_html()
        engine = CrawlEngine()
        with respx.mock:
            respx.get(URL).mock(
                return_value=httpx.Response(
                    200, content=html, headers={"etag": '"abc"'}
                )
            )
            result = engine.fetch(source_id="src_t", url=URL, crawl_run_id="run_1")

        assert result.success is True
        assert result.http_status == 200
        assert result.content_hash_sha256 == hashlib.sha256(html).hexdigest()
        assert result.skipped is False
        assert result.error is None

    def test_304_after_if_none_match_is_skipped(self):
        engine = CrawlEngine()
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(304))
            result = engine.fetch(
                source_id="src_t", url=URL, crawl_run_id="run_1", etag='"abc"'
            )

        assert result.skipped is True
        assert result.skip_reason == "304_not_modified"
        assert result.http_status == 304
        # skipped means not "success" (do not process downstream)
        assert result.success is False

    def test_content_unchanged_when_previous_hash_matches(self):
        html = _forum_html()
        body_hash = hashlib.sha256(html).hexdigest()
        engine = CrawlEngine()
        with respx.mock:
            respx.get(URL).mock(return_value=httpx.Response(200, content=html))
            result = engine.fetch(
                source_id="src_t",
                url=URL,
                crawl_run_id="run_1",
                previous_hash=body_hash,
            )

        assert result.skipped is True
        assert result.skip_reason == "content_unchanged"
        assert result.http_status == 200
        assert result.success is False

    def test_network_error_retries_then_fails(self):
        engine = CrawlEngine()
        with respx.mock:
            respx.get(URL).mock(side_effect=httpx.ConnectError("boom"))
            result = engine.fetch(source_id="src_t", url=URL, crawl_run_id="run_1")

        # after tenacity retries (max 3) the run is a failure, not a skip
        assert result.success is False
        assert result.error is not None
        assert result.http_status is None
        assert result.skipped is False


# ---------------------------------------------------------------------------
# Incremental crawl state persistence (file-based SQLite, survives a fresh
# session opened from the same file)
# ---------------------------------------------------------------------------


@pytest.fixture
def file_engine(tmp_path):
    db_path = tmp_path / "crawl.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


class TestIncrementalPersistence:
    def test_etag_and_cursor_persist_across_fresh_session(self, file_engine):
        Session = sessionmaker(bind=file_engine)

        # --- first session: register an authorized source + run a 200 fetch ---
        session = Session()
        svc = RegistryService(session)
        src = svc.add_source(
            name="Inc",
            url="http://localhost:9999/inc",
            source_type="forum",
            auth_ref="AUTH-INC",
        )
        session.commit()
        # capture scalars while the session is still bound (avoid detached-access)
        src_id = src.id
        session.commit()

        html = _forum_html()
        etag = '"etag-inc-1"'
        last_mod = "Mon, 01 Sep 2026 00:00:00 GMT"
        inc_url = "http://localhost:9999/inc"

        engine = CrawlEngine()
        with respx.mock:
            respx.get(inc_url).mock(
                return_value=httpx.Response(
                    200,
                    content=html,
                    headers={"etag": etag, "last-modified": last_mod},
                )
            )
            result = engine.fetch(source_id=src_id, url=inc_url, crawl_run_id="run_inc")
        assert result.success
        assert result.http_status == 200

        # mirror the runner: persist ETag / Last-Modified + incremental cursor
        new_etag = result.headers.get("etag") or result.headers.get("ETag")
        new_lm = result.headers.get("last-modified") or result.headers.get("Last-Modified")
        svc.update_etag(src_id, new_etag, new_lm)
        svc.update_cursor(
            src_id,
            {"last_content_hash": result.content_hash_sha256, "last_run_id": "run_inc"},
        )
        session.commit()
        session.close()

        # --- brand-new session from the SAME file ---
        session2 = Session()
        svc2 = RegistryService(session2)
        src2 = svc2.get_source(src_id)
        assert src2 is not None
        assert src2.etag == etag
        assert src2.last_modified == last_mod
        cursor = src2.crawl_cursor or {}
        assert cursor.get("last_content_hash") == result.content_hash_sha256
        assert cursor.get("last_run_id") == "run_inc"

        # --- second fetch passing the persisted etag -> 304 -> skipped ---
        engine2 = CrawlEngine()
        with respx.mock:
            respx.get(inc_url).mock(return_value=httpx.Response(304))
            result2 = engine2.fetch(
                source_id=src_id,
                url=inc_url,
                crawl_run_id="run_inc2",
                etag=src2.etag,
            )
        assert result2.skipped is True
        assert result2.skip_reason == "304_not_modified"
        assert result2.success is False
        session2.close()
