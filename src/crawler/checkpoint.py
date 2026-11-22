"""
File-based SQLite checkpoint store for crash/resume recovery.

Separate from the registry DB (D-014): tracks the in-progress state of each
crawl run so an interrupted worker can resume where it left off. Uses stdlib
``sqlite3`` only — no SQLAlchemy, no network.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from src.shared.logging import get_logger

log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CrawlCheckpointStore:
    """Persist crawl-run progress to a file-based sqlite3 database.

    A checkpoint row records that a crawl run for a given source is in flight
    (``running`` / ``checkpointed``) or has finished (``done`` / ``failed``).
    ``list_interrupted`` returns rows that never reached a terminal state, so
    a worker restarting can find and re-run them.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # Auto-create the parent directory (file-based sqlite).
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        log.info("checkpoint_store_open", db_path=db_path)

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_checkpoints (
                source_id    TEXT    NOT NULL,
                run_id       TEXT    NOT NULL,
                url          TEXT,
                status       TEXT    NOT NULL,
                cursor_json  TEXT,
                attempts     INTEGER NOT NULL DEFAULT 0,
                updated_at   TEXT    NOT NULL,
                created_at   TEXT    NOT NULL,
                PRIMARY KEY (source_id, run_id)
            )
            """
        )
        self._conn.commit()

    def save_checkpoint(
        self,
        source_id: str,
        run_id: str,
        url: str | None,
        status: str,
        cursor: dict[str, Any] | None = None,
        attempts: int = 0,
    ) -> None:
        """Upsert a checkpoint row for (source_id, run_id)."""
        now = _now_iso()
        cursor_json = json.dumps(cursor) if cursor else None
        self._conn.execute(
            """
            INSERT INTO crawl_checkpoints
                (source_id, run_id, url, status, cursor_json, attempts,
                 updated_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, run_id) DO UPDATE SET
                url          = excluded.url,
                status       = excluded.status,
                cursor_json  = excluded.cursor_json,
                attempts     = excluded.attempts,
                updated_at   = excluded.updated_at
            """,
            (source_id, run_id, url, status, cursor_json, attempts, now, now),
        )
        self._conn.commit()

    def get_latest(self, source_id: str) -> dict[str, Any] | None:
        """Return the most recent checkpoint for a source, or None."""
        row = self._conn.execute(
            """
            SELECT * FROM crawl_checkpoints
            WHERE source_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_interrupted(self) -> list[dict[str, Any]]:
        """Return checkpoints that never reached a terminal state.

        A row is interrupted if its status is ``running`` or ``checkpointed``
        and no later ``done``/``failed`` row exists for the same source.
        """
        rows = self._conn.execute(
            """
            SELECT c.* FROM crawl_checkpoints c
            WHERE c.status IN ('running', 'checkpointed')
              AND NOT EXISTS (
                  SELECT 1 FROM crawl_checkpoints c2
                  WHERE c2.source_id = c.source_id
                    AND c2.status IN ('done', 'failed')
                    AND c2.updated_at >= c.updated_at
              )
            ORDER BY c.updated_at ASC
            """
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def mark_complete(self, source_id: str, run_id: str) -> None:
        """Record that a crawl run finished successfully."""
        self.save_checkpoint(
            source_id=source_id,
            run_id=run_id,
            url=None,
            status="done",
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        if data.get("cursor_json"):
            try:
                data["cursor"] = json.loads(data["cursor_json"])
            except (ValueError, TypeError):
                data["cursor"] = None
        else:
            data["cursor"] = None
        return data

    def close(self) -> None:
        self._conn.close()


def new_run_id() -> str:
    """Generate a short run id for a crawl run."""
    return str(uuid.uuid4())[:8]
