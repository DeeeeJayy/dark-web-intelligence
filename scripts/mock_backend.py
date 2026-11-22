"""
Mock backend ingestion sink.
Accepts POST /api/v1/ingest, stores records in memory, returns 201.
Returns 409 if the same X-Idempotency-Key is seen again.
"""
from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request, Response
import structlog

app = FastAPI(title="CyArt Mock Backend Sink", version="1.0")
structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
log = structlog.get_logger()

_seen_ids: set[str] = set()
_records: list[dict] = []


@app.post("/api/v1/ingest", status_code=201)
async def ingest(
    request: Request,
    x_idempotency_key: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key")

    record_id = x_idempotency_key or "unknown"

    if record_id in _seen_ids:
        log.info("mock_ingest_duplicate", record_id=record_id)
        return Response(status_code=409, content="already ingested")

    body = await request.json()
    _seen_ids.add(record_id)
    _records.append(body)
    log.info("mock_ingest_ok", record_id=record_id, url=body.get("url", ""))
    return {"record_id": record_id, "status": "accepted"}


@app.get("/api/v1/records")
async def list_records() -> dict:
    return {"count": len(_records), "records": _records[-20:]}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ingested_count": len(_records)}
