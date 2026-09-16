# M2 Data Contracts

## Purpose

This document defines the standard data exchanged between the four M2
Crawling PODs.

All PODs must follow these contracts when exchanging data.

Pipeline:

P1 -> SourceConfig
   -> P2 -> CrawlResult
   -> P4 -> NormalizedRecord
   -> P3 -> ValidatedRecord
   -> M1 Backend


---

# 1. SourceConfig

**Producer:** M2-P1 - Source Discovery & Management  
**Consumer:** M2-P2 - Crawling Engine

Represents a source that P2 is permitted and configured to crawl.

## Example

```json
{
  "source_id": "SRC001",
  "name": "research_source_1",
  "url": "http://example.onion",
  "enabled": true,
  "crawl_interval": 3600,
  "max_depth": 2,
  "tags": ["forum"]
}
```

## Required Fields

| Field | Type | Description |
|---|---|---|
| source_id | string | Unique source identifier |
| name | string | Human-readable source name |
| url | string | Starting URL |
| enabled | boolean | Whether crawling is enabled |
| crawl_interval | integer | Crawl interval in seconds |
| max_depth | integer | Maximum crawl depth |
| tags | array | Source classification tags |


---

# 2. CrawlResult

**Producer:** M2-P2 - Crawling Engine  
**Consumer:** M2-P4 - Content Extraction & Normalization

Represents the result of a crawl request.

## Example

```json
{
  "crawl_id": "CRAWL001",
  "source_id": "SRC001",
  "url": "http://example.onion/page",
  "crawled_at": "2026-09-16T13:30:00Z",
  "status_code": 200,
  "content_type": "text/html",
  "raw_content": "<html>...</html>",
  "response_time_ms": 1240,
  "error": null
}
```

## Required Fields

| Field | Type | Description |
|---|---|---|
| crawl_id | string | Unique crawl identifier |
| source_id | string | Source that produced the URL |
| url | string | Crawled URL |
| crawled_at | datetime | UTC crawl timestamp |
| status_code | integer/null | HTTP response status |
| content_type | string/null | Response content type |
| raw_content | string/null | Retrieved raw content |
| response_time_ms | integer/null | Request duration |
| error | string/null | Error information if crawling failed |


---

# 3. NormalizedRecord

**Producer:** M2-P4 - Content Extraction & Normalization  
**Consumer:** M2-P3 - Data Quality, Deduplication & Monitoring

Represents useful information extracted from a CrawlResult.

## Example

```json
{
  "crawl_id": "CRAWL001",
  "source_id": "SRC001",
  "url": "http://example.onion/page",
  "title": "Example",
  "text": "Extracted and normalized text...",
  "links": [],
  "metadata": {},
  "crawled_at": "2026-09-16T13:30:00Z"
}
```

## Required Fields

| Field | Type | Description |
|---|---|---|
| crawl_id | string | Original crawl identifier |
| source_id | string | Original source identifier |
| url | string | Original page URL |
| title | string/null | Extracted page title |
| text | string | Normalized textual content |
| links | array | Extracted links |
| metadata | object | Additional extracted metadata |
| crawled_at | datetime | Original crawl timestamp |


---

# 4. ValidatedRecord

**Producer:** M2-P3 - Data Quality, Deduplication & Monitoring  
**Consumer:** M1 Backend

Represents a normalized record after quality and duplicate checks.

## Example

```json
{
  "crawl_id": "CRAWL001",
  "source_id": "SRC001",
  "url": "http://example.onion/page",
  "title": "Example",
  "text": "Extracted and normalized text...",
  "content_hash": "sha256_hash_here",
  "is_valid": true,
  "is_duplicate": false,
  "validation_errors": [],
  "metadata": {},
  "crawled_at": "2026-09-16T13:30:00Z"
}
```

## Required Fields

| Field | Type | Description |
|---|---|---|
| crawl_id | string | Original crawl identifier |
| source_id | string | Original source identifier |
| url | string | Original URL |
| title | string/null | Extracted title |
| text | string | Validated normalized text |
| content_hash | string | Content fingerprint |
| is_valid | boolean | Whether the record passed validation |
| is_duplicate | boolean | Whether duplicate content was detected |
| validation_errors | array | Validation problems |
| metadata | object | Record metadata |
| crawled_at | datetime | Original crawl timestamp |


---

# Contract Rules

1. PODs must not silently rename or remove shared fields.
2. Contract changes must be discussed before implementation.
3. New optional fields may be proposed as the project evolves.
4. Timestamps should use ISO 8601 UTC format.
5. Secrets, authentication credentials and sensitive configuration must
   never be included in these records.
6. Each POD must validate the input it receives.
7. Failed operations should return structured error information rather
   than causing the complete pipeline to fail.
8. Shared Python models implementing these contracts should be maintained
   under `shared/models/`.


# Initial Integration Goal

The first integration milestone is:

SourceConfig
    |
    v
P2 Crawler
    |
    v
CrawlResult
    |
    v
P4 Extractor
    |
    v
NormalizedRecord
    |
    v
P3 Quality
    |
    v
ValidatedRecord
    |
    v
M1 Backend

The initial goal is not advanced crawling performance.

The goal is to prove that one authorized test source can pass through the
complete M2 pipeline using consistent data contracts.