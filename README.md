# M2 Crawling Pipeline

Dark Web Intelligence and Early Warning System — M2 Crawling Team.

Collects, extracts, normalizes, deduplicates, and ingests content from
registered, authorized research sources. All crawling is restricted to sources
with explicit authorization records; a source with no authorization record
cannot be queued.

---

## Pipeline

```
Source Registry
     │  (auth gate: no-auth source blocked here)
     ▼
Crawler (CrawlEngine)
     │  ETag / Last-Modified / content-digest incremental skip
     ▼
Extractor (format-aware: HTML forum/paste/blog, RSS, Atom, JSON)
     │
     ▼
Normalizer → NormalizedRecord v1.0
     │
     ▼
Quality (validation + exact/near-duplicate detection, SimHash-64)
     │
     ▼
Backend Ingestion (idempotent, X-Idempotency-Key per record)
     │
     ▼
Metrics / Pipeline-health report
```

---

## Setup

**Requires Python 3.12.**

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and edit as needed (see **Configuration** below).
For development and testing no live services are required — SQLite and the mock
backend are used by default.

---

## Running

### One-command demo (no live network, no real backend)

Runs the full pipeline on frozen local fixtures and prints a summary:

```powershell
venv\Scripts\python.exe scripts\demo.py
```

Example output:

```
==================================================
  M2 Crawling Pipeline — Demo Run
==================================================
  Sources processed : 6
  Records normalized: 6
  Exact duplicates  : 0
  Near duplicates   : 0
  Dedup rate        : 0%
  Ingested          : 6
  Success rate      : 100%
  Errors            : 0
  Elapsed           : 2.71s
==================================================
```

### Seed sources into the registry

```powershell
venv\Scripts\python.exe scripts\seed_sources.py        # hardcoded synthetic sources
venv\Scripts\python.exe scripts\load_sources.py        # load from config/sources.json
```

### Benchmark

```powershell
venv\Scripts\python.exe scripts\benchmark.py
```

Runs n=300 synthetic records through the full in-process pipeline and prints
real measured throughput, per-stage latency, success rate, and dedup rate.

---

## Tests

```powershell
venv\Scripts\python.exe -m pytest                      # full suite
venv\Scripts\python.exe -m pytest tests/unit/          # unit tests only
venv\Scripts\python.exe -m pytest tests/integration/   # integration tests only
```

All tests use frozen local fixtures — no live network, no real backend, no Tor.

**Current baseline:** 134 passed, 1 warning (Phase 2 complete).

---

## Configuration

| Env var | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://cyart:cyart@localhost:5432/darkweb` | Registry DB (SQLite in-memory for tests) |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `BACKEND_INGEST_URL` | `http://localhost:8001/api/v1/ingest` | Ingestion endpoint |
| `BACKEND_API_KEY` | `mock-api-key` | API key for ingestion |
| `TOR_SOCKS_PROXY` | *(empty)* | SOCKS5 proxy for Tor; empty = disabled |
| `LOG_LEVEL` | `INFO` | Structured log level |
| `CRAWL_CONCURRENCY` | `4` | Parallel fetch workers |
| `CRAWL_RATE_LIMIT` | `30` | Max pages/minute |
| `MAX_RETRIES` | `3` | Fetch retry limit (exponential backoff) |

Source configuration: `config/sources.json` — add new sources here without
code changes. Each entry requires a non-empty `auth_ref`; entries with no
`auth_ref` are skipped by the loader (authorization hard gate).

---

## Project structure

```
├── config/
│   └── sources.json          # registered sources (config-driven)
├── docker/                   # Dockerfile(s)
├── docker-compose.yml        # full stack (Postgres, Redis, ES)
├── docs/
│   ├── AUDIT.md
│   ├── DECISIONS.md          # D-001..D-016 architectural decisions
│   └── INTERFACE.md          # frozen NormalizedRecord v1.0 contract
├── requirements.txt
├── pyproject.toml
├── scripts/
│   ├── demo.py               # one-command end-to-end demo
│   ├── benchmark.py          # throughput / latency benchmark
│   ├── seed_sources.py       # seed synthetic sources
│   ├── load_sources.py       # load sources from config/sources.json
│   ├── init_db.py            # initialize registry DB schema
│   └── mock_backend.py       # local mock ingestion server
├── src/
│   ├── crawler/              # CrawlEngine, queue, checkpoint/resume
│   ├── extraction/           # HTML/RSS/Atom/JSON parsers, format detection
│   ├── ingestion/            # BackendIngestionClient
│   ├── monitoring/           # PipelineMetrics, health reports
│   ├── normalization/        # normalize() → NormalizedRecord
│   ├── pipeline/             # runner: end-to-end orchestration
│   ├── quality/              # validation, SimHash-64 exact/near-dedup
│   ├── registry/             # source registry, crawl policies, auth gate
│   └── shared/               # config, structured logging, models
└── tests/
    ├── fixtures/             # frozen HTML / RSS / Atom / JSON fixtures
    ├── integration/          # full-pipeline integration tests
    └── unit/                 # per-module unit tests
```

---

## Responsible-use constraints

- Crawling is restricted to sources registered in the source registry with a
  valid authorization record. A source with no `auth_ref` cannot be queued —
  the check is enforced at the pipeline entry point.
- No bypassing of access controls, no captcha solving, no use of unauthorized
  credentials.
- Incremental crawling skips already-collected, permitted content only; it
  never bypasses restrictions.
- No real dark-web / onion sources in development. Tor proxy is disabled by
  default (`TOR_SOCKS_PROXY=`).
- No automated purchasing, system compromise, or enforcement actions.

---

## Known limitations

- **Backend is a mock/sink.** `scripts/mock_backend.py` and the respx-mocked
  test client stand in for a real backend. `BACKEND_INGEST_URL` must point to a
  real endpoint in production.
- **Benchmark throughput is dominated by mock-ingest overhead.** The
  `BackendIngestionClient` builds a fresh `httpx.Client` per call; combined
  with respx overhead this costs ~226 ms per record. The pipeline stages
  themselves (extract / normalize / quality) average under 3 ms combined.
  Measured baseline: 5.5 records/s end-to-end (n=300).
- **File-SQLite persistence only.** Crawl checkpoints and the registry use
  SQLite for development and testing. Production deployment uses Postgres
  (registry) and Redis (Celery broker), configured via `docker-compose.yml`.
- **In-process dedup store.** The `QualityChecker` dedup store is in-memory
  per-process. A Redis-backed store is noted as the production replacement
  (D-003).
- **Phases 3–5 not yet complete.** Health-based crawl adjustment, bounded
  crash/resume with dead-letter queue, normalization edge cases, full
  benchmarking (CPU/memory), and the consolidated `docs/REPORT.md` are
  scheduled for Phases 3–5.
