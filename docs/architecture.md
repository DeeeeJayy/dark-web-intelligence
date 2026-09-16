# System Architecture

## Overview

The Dark Web Intelligence and Early Warning System uses a modular pipeline
for collecting and processing information from authorized and
research-permitted sources.

## High-Level Pipeline

Authorized Sources
        |
        v
Source Discovery & Management (M2-P1)
        |
        | SourceConfig
        v
Crawling Engine (M2-P2)
        |
        | CrawlResult
        v
Content Extraction & Normalization (M2-P4)
        |
        | NormalizedRecord
        v
Data Quality, Deduplication & Monitoring (M2-P3)
        |
        | ValidatedRecord
        v
Backend Ingestion (M1)
        |
        v
Database / Storage
        |
        v
NLP Analysis
        |
        v
Event Detection / Risk Scoring
        |
        v
Alert Engine
        |
        v
Backend APIs
        |
        v
Frontend Dashboard


## M2 - Crawling Architecture

### P1 - Source Discovery & Management

P1 manages the sources available to the crawling system.

Responsibilities:

- Source registration
- Source configuration
- Source metadata
- Source availability/health information
- Crawl policy configuration

Produces:

`SourceConfig`

Consumed by:

M2-P2


### P2 - Crawling Engine

P2 performs the actual collection of content.

Responsibilities:

- Crawl engine
- Request handling
- Crawl queues
- Scheduling
- Rate limiting
- Timeout handling
- Retry mechanisms
- Failure recovery
- Incremental crawling
- Crawl metadata
- Logging
- Performance optimization

Receives:

`SourceConfig`

Produces:

`CrawlResult`

Consumed by:

M2-P4


### P4 - Content Extraction & Normalization

P4 converts crawler responses into structured information.

Responsibilities:

- Content extraction
- HTML parsing
- Text extraction
- Metadata extraction
- Link extraction
- Content normalization
- Common record formatting

Receives:

`CrawlResult`

Produces:

`NormalizedRecord`

Consumed by:

M2-P3


### P3 - Data Quality, Deduplication & Monitoring

P3 verifies the quality and uniqueness of normalized records.

Responsibilities:

- Schema validation
- Content validation
- Duplicate detection
- Content fingerprinting
- Quality assurance
- Quality metrics
- Source/crawler monitoring
- Failure statistics
- Data completeness checks

Receives:

`NormalizedRecord`

Produces:

`ValidatedRecord`

Consumed by:

M1 Backend


## Integration Principle

Each POD owns its internal implementation but must follow the shared
input/output contracts.

A POD may change its internal implementation without affecting another
POD as long as the agreed data contract remains compatible.

Shared data structures should be maintained under:

shared/models/

Detailed interface definitions are maintained in:

docs/data-contracts.md


## Development Principle

Development should proceed incrementally.

The initial objective is to establish a minimal working pipeline:

P1 -> P2 -> P4 -> P3 -> Backend

Once the basic pipeline works, individual PODs can progressively add
reliability, monitoring, optimization, testing, and advanced functionality.