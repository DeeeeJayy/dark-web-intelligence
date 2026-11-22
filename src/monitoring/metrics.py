"""Pipeline health metrics collector and reporter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PipelineMetrics:
    run_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Counts per stage
    fetched: int = 0
    fetch_skipped: int = 0        # incremental: not modified
    fetch_failed: int = 0
    extracted: int = 0
    extraction_failed: int = 0
    normalized: int = 0
    normalization_failed: int = 0
    validated: int = 0
    validation_failed: int = 0
    exact_duplicates: int = 0
    near_duplicates: int = 0
    ingested: int = 0
    ingest_failed: int = 0
    ingest_skipped: int = 0       # backend said already exists

    # Latency totals (ms) for averaging
    _total_fetch_ms: float = field(default=0.0, repr=False)
    _total_ingest_ms: float = field(default=0.0, repr=False)

    def record_fetch(self, *, success: bool, skipped: bool, response_ms: float) -> None:
        if skipped:
            self.fetch_skipped += 1
        elif success:
            self.fetched += 1
            self._total_fetch_ms += response_ms
        else:
            self.fetch_failed += 1

    def record_extraction(self, *, success: bool) -> None:
        if success:
            self.extracted += 1
        else:
            self.extraction_failed += 1

    def record_normalization(self, *, success: bool) -> None:
        if success:
            self.normalized += 1
        else:
            self.normalization_failed += 1

    def record_quality(self, *, valid: bool, duplicate: bool, dup_type: str | None) -> None:
        if valid:
            self.validated += 1
        else:
            self.validation_failed += 1
        if duplicate:
            if dup_type == "exact":
                self.exact_duplicates += 1
            else:
                self.near_duplicates += 1

    def record_ingestion(self, *, success: bool, skipped: bool) -> None:
        if skipped:
            self.ingest_skipped += 1
        elif success:
            self.ingested += 1
        else:
            self.ingest_failed += 1

    @property
    def avg_fetch_ms(self) -> float:
        return self._total_fetch_ms / self.fetched if self.fetched else 0.0

    @property
    def crawl_success_rate(self) -> float:
        total = self.fetched + self.fetch_failed
        return self.fetched / total if total else 0.0

    @property
    def duplicate_rate(self) -> float:
        total = self.exact_duplicates + self.near_duplicates + self.ingested + self.ingest_skipped
        dups = self.exact_duplicates + self.near_duplicates
        return dups / total if total else 0.0

    @property
    def completeness(self) -> float:
        """Fraction of fetched records that have all required fields."""
        total = self.normalized
        return self.validated / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return {
            "run_id": self.run_id,
            "elapsed_seconds": round(elapsed, 1),
            "fetched": self.fetched,
            "fetch_skipped_incremental": self.fetch_skipped,
            "fetch_failed": self.fetch_failed,
            "crawl_success_rate": round(self.crawl_success_rate, 4),
            "extracted": self.extracted,
            "extraction_failed": self.extraction_failed,
            "normalized": self.normalized,
            "normalization_failed": self.normalization_failed,
            "validated": self.validated,
            "validation_failed": self.validation_failed,
            "exact_duplicates": self.exact_duplicates,
            "near_duplicates": self.near_duplicates,
            "duplicate_rate": round(self.duplicate_rate, 4),
            "ingested": self.ingested,
            "ingest_failed": self.ingest_failed,
            "ingest_skipped": self.ingest_skipped,
            "avg_fetch_ms": round(self.avg_fetch_ms, 1),
            "completeness": round(self.completeness, 4),
        }

    def report(self) -> str:
        d = self.to_dict()
        lines = ["=== Pipeline Health Report ==="]
        for k, v in d.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)
