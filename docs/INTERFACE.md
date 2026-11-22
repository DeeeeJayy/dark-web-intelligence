# M2 Pipeline Interface Contract

## Normalized Record Schema (v1.0)

This schema is frozen after Phase 1. No stage may add or remove fields without bumping `schema_version`.

```json
{
  "schema_version": "1.0",
  "record_id": "<uuid>",
  "source": "<source_id>",
  "url": "<string>",
  "content": "<string>",
  "published_at": "<ISO8601 | null>",
  "collected_at": "<ISO8601>",
  "title": "<string | null>",
  "author": "<string | null>",
  "language": "<ISO639-1 | null>",
  "source_type": "forum|paste|blog|marketplace|news|other",
  "tags": ["<string>"],
  "links": ["<string>"],
  "extra": {},
  "provenance": {
    "source_id": "<string>",
    "source_url": "<string>",
    "fetched_url": "<string>",
    "fetch_time": "<ISO8601>",
    "crawl_run_id": "<string>",
    "http_status": "<int | null>",
    "content_hash_sha256": "<64-char hex | null>"
  }
}
```

## Backend Ingestion API

**Endpoint:** `POST /api/v1/ingest`

**Headers:**
- `X-Idempotency-Key: <record_id>` — prevents double-ingestion on retry
- `X-API-Key: <key>` — authentication
- `Content-Type: application/json`

**Body:** NormalizedRecord serialized via `record.to_ingest_dict()`

**Responses:**
- `201 Created` — accepted
- `409 Conflict` — already ingested (idempotent success, skipped=True)
- `401 Unauthorized` — bad API key

## Pipeline Stage Boundaries

| Producer | Consumer | Artifact |
|---|---|---|
| Registry | Crawler | Source config + crawl policy |
| Crawler | Extractor | `CrawlResult` (raw bytes + provenance) |
| Extractor | Normalizer | `ExtractionResult` (text + metadata) |
| Normalizer | Quality | `NormalizedRecord` (v1.0) |
| Quality | Ingestion | Validated, deduped `NormalizedRecord` |
| Ingestion | Backend | JSON payload (to_ingest_dict) |
