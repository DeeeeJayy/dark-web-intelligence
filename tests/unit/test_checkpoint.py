"""
CrawlCheckpointStore + resume_interrupted_crawls tests.

The checkpoint store is a file-based stdlib sqlite3 DB separate from the
registry (D-014). The resume case monkeypatches the runner's SessionLocal to a
temp-file sqlite registry so the re-run exercises the real pipeline in-process
with respx-mocked fetch + ingest (no live network).
"""
from __future__ import annotations

import httpx
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import src.pipeline.runner as runner_mod
from src.crawler.checkpoint import CrawlCheckpointStore
from src.registry.db import Base
from src.registry.models import CrawlJob, Source  # noqa: F401  (registers tables)
from src.registry.service import RegistryService

FIXTURES = __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "html"


class TestCheckpointStore:
    def test_save_and_get_latest_round_trip(self, tmp_path):
        store = CrawlCheckpointStore(str(tmp_path / "cp.db"))
        try:
            store.save_checkpoint(
                "src_a", "run_1", "http://localhost:9999/a", "running"
            )
            latest = store.get_latest("src_a")
            assert latest is not None
            assert latest["source_id"] == "src_a"
            assert latest["run_id"] == "run_1"
            assert latest["status"] == "running"
            assert latest["url"] == "http://localhost:9999/a"
            assert latest["attempts"] == 0
        finally:
            store.close()

    def test_upsert_updates_existing_row(self, tmp_path):
        store = CrawlCheckpointStore(str(tmp_path / "cp.db"))
        try:
            store.save_checkpoint("src_a", "run_1", "http://localhost:9999/a", "running")
            store.save_checkpoint("src_a", "run_1", "http://localhost:9999/a", "failed", attempts=1)
            latest = store.get_latest("src_a")
            assert latest["status"] == "failed"
            assert latest["attempts"] == 1
        finally:
            store.close()

    def test_list_interrupted_excludes_completed(self, tmp_path):
        store = CrawlCheckpointStore(str(tmp_path / "cp.db"))
        try:
            store.save_checkpoint("src_a", "run_1", "http://localhost:9999/a", "running")
            store.save_checkpoint("src_b", "run_2", "http://localhost:9999/b", "running")
            store.save_checkpoint("src_c", "run_3", "http://localhost:9999/c", "checkpointed")
            store.mark_complete("src_a", "run_1")  # completed -> no longer interrupted

            interrupted = store.list_interrupted()
            keys = {(r["source_id"], r["run_id"]) for r in interrupted}
            assert ("src_b", "run_2") in keys
            assert ("src_c", "run_3") in keys
            assert ("src_a", "run_1") not in keys
        finally:
            store.close()

    def test_list_interrupted_excludes_failed(self, tmp_path):
        store = CrawlCheckpointStore(str(tmp_path / "cp.db"))
        try:
            store.save_checkpoint("src_a", "run_1", "http://localhost:9999/a", "running")
            store.save_checkpoint("src_a", "run_2", "http://localhost:9999/a", "failed")
            interrupted = store.list_interrupted()
            keys = {(r["source_id"], r["run_id"]) for r in interrupted}
            assert ("src_a", "run_1") not in keys  # a later 'failed' row exists
            assert ("src_a", "run_2") not in keys
        finally:
            store.close()

    def test_get_latest_returns_none_for_unknown(self, tmp_path):
        store = CrawlCheckpointStore(str(tmp_path / "cp.db"))
        try:
            assert store.get_latest("nope") is None
        finally:
            store.close()

    def test_auto_creates_parent_dir(self, tmp_path):
        nested = tmp_path / "nested" / "dir" / "cp.db"
        store = CrawlCheckpointStore(str(nested))
        try:
            store.save_checkpoint("src_a", "run_1", "http://localhost:9999/a", "running")
            assert nested.exists()
        finally:
            store.close()


class TestResumeInterruptedCrawls:
    def test_resume_reruns_interrupted_and_marks_complete(self, tmp_path, monkeypatch):
        # --- temp-file registry with schema + an authorized source ---
        reg_path = tmp_path / "reg.db"
        reg_engine = create_engine(
            f"sqlite:///{reg_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=reg_engine)
        RegSession = sessionmaker(bind=reg_engine)

        s = RegSession()
        svc = RegistryService(s)
        src = svc.add_source(
            name="Inc",
            url="http://localhost:9999/inc",
            source_type="forum",
            auth_ref="AUTH-RESUME",
        )
        s.commit()
        # capture scalars while the session is bound (avoid detached-access after close)
        src_id = src.id
        src_url = src.url
        s.commit()
        s.close()

        # point the runner's DB at our temp-file registry
        monkeypatch.setattr(runner_mod, "SessionLocal", RegSession)

        # The runner calls configure_logging(), which reconfigures the global
        # structlog chain (PrintLoggerFactory -> stdout) and binds a run_id
        # contextvar. configure_logging itself no longer raises: the
        # add_logger_name / PrintLogger incompatibility was fixed in ab7d2cd
        # (logging.py is no longer "frozen"). This monkeypatch is therefore for
        # TEST ISOLATION only — it keeps this resume test from mutating global
        # logging state and printing JSON logs to stdout. The test asserts
        # checkpoint resume behaviour, not logging.
        monkeypatch.setattr(runner_mod, "configure_logging", lambda run_id=None: run_id or "run")

        # --- checkpoint store with an interrupted 'running' run ---
        cp_path = tmp_path / "cp.db"
        store = CrawlCheckpointStore(str(cp_path))
        store.save_checkpoint(src_id, "run_resume", src_url, "running")

        html = (FIXTURES / "forum_post.html").read_bytes()

        with respx.mock:
            respx.get("http://localhost:9999/inc").mock(
                return_value=httpx.Response(200, content=html, headers={"etag": '"e1"'})
            )
            respx.post("http://localhost:8001/api/v1/ingest").mock(
                return_value=httpx.Response(201, json={"record_id": "r"})
            )
            results = runner_mod.resume_interrupted_crawls(checkpoint_store=store)

        try:
            assert results["interrupted"] == 1
            assert len(results["rerun"]) == 1
            assert results["errors"] == []
            assert results["rerun"][0]["source_id"] == src_id
            assert results["rerun"][0]["run_id"] == "run_resume"

            # the interrupted checkpoint transitioned to 'done'
            latest = store.get_latest(src_id)
            assert latest["status"] == "done"
            assert store.list_interrupted() == []
        finally:
            store.close()
            reg_engine.dispose()
