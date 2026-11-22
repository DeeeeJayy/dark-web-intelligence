"""
Quality stage: validation + exact/near-duplicate detection.

Exact dedup  : SHA-256 of raw bytes (from provenance)
Near-dedup   : SimHash of normalized content text
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol

from src.shared.logging import get_logger
from src.shared.models import NormalizedRecord

log = get_logger(__name__)

# SimHash bit width
SIMHASH_BITS = 64
# Hamming distance threshold for near-duplicate
NEAR_DUP_THRESHOLD = 3


# -----------------------------------------------------------------------
# SimHash implementation (no C extension required)
# -----------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Split on whitespace into tokens, lowercase."""
    return re.findall(r"\w+", text.lower())


def _simhash(text: str, bits: int = SIMHASH_BITS) -> int:
    """Compute SimHash fingerprint of text."""
    tokens = _tokenize(text)
    if not tokens:
        return 0

    v = [0] * bits
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            bit = (h >> i) & 1
            v[i] += 1 if bit else -1

    result = 0
    for i in range(bits):
        if v[i] > 0:
            result |= 1 << i
    return result


def simhash_hex(text: str) -> str:
    return format(_simhash(text), "016x")


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def hamming_distance_hex(h1: str, h2: str) -> int:
    return hamming_distance(int(h1, 16), int(h2, 16))


# -----------------------------------------------------------------------
# Dedup store (in-process; backed by sets for the pipeline session)
# -----------------------------------------------------------------------

class DedupStore(Protocol):
    def has_exact(self, sha256: str) -> bool: ...
    def add_exact(self, sha256: str, record_id: str) -> None: ...
    def find_near(self, simhash_val: str) -> str | None: ...
    def add_near(self, simhash_val: str, record_id: str) -> None: ...


@dataclass
class InMemoryDedupStore:
    """
    In-process dedup store. For production use a Redis-backed store.
    Keys are shared per pipeline run.
    """
    _exact: dict[str, str] = field(default_factory=dict)   # sha256 -> record_id
    _near: list[tuple[int, str]] = field(default_factory=list)  # (simhash_int, record_id)

    def has_exact(self, sha256: str) -> bool:
        return sha256 in self._exact

    def get_exact_id(self, sha256: str) -> str | None:
        return self._exact.get(sha256)

    def add_exact(self, sha256: str, record_id: str) -> None:
        self._exact[sha256] = record_id

    def find_near(self, simhash_val: str) -> str | None:
        """Return record_id of nearest duplicate if within threshold, else None."""
        sh = int(simhash_val, 16)
        for existing_sh, rid in self._near:
            if hamming_distance(sh, existing_sh) <= NEAR_DUP_THRESHOLD:
                return rid
        return None

    def add_near(self, simhash_val: str, record_id: str) -> None:
        self._near.append((int(simhash_val, 16), record_id))

    def stats(self) -> dict[str, int]:
        return {"exact_seen": len(self._exact), "near_seen": len(self._near)}


# -----------------------------------------------------------------------
# Validation rules
# -----------------------------------------------------------------------

REQUIRED_FIELDS = ["source", "url", "content"]
MIN_CONTENT_LENGTH = 10
MAX_URL_LENGTH = 2048


def validate_record(record: NormalizedRecord) -> list[str]:
    """Return list of validation error strings (empty = valid)."""
    errors: list[str] = []

    for f in REQUIRED_FIELDS:
        val = getattr(record, f, None)
        if not val:
            errors.append(f"missing_required_field:{f}")

    if record.url and len(record.url) > MAX_URL_LENGTH:
        errors.append("url_too_long")

    if record.content and len(record.content.strip()) < MIN_CONTENT_LENGTH:
        errors.append(f"content_too_short:{len(record.content.strip())}")

    if not record.provenance.fetch_time:
        errors.append("missing_fetch_time")

    if not record.provenance.crawl_run_id:
        errors.append("missing_crawl_run_id")

    return errors


# -----------------------------------------------------------------------
# Quality checker
# -----------------------------------------------------------------------

@dataclass
class QualityResult:
    record: NormalizedRecord
    validation_errors: list[str]
    is_valid: bool
    is_duplicate: bool
    duplicate_of: str | None
    duplicate_type: str | None  # "exact" | "near" | None


class QualityChecker:
    def __init__(self, dedup_store: InMemoryDedupStore | None = None) -> None:
        self._store = dedup_store or InMemoryDedupStore()

    def check(self, record: NormalizedRecord) -> QualityResult:
        """Validate, fingerprint, and check for duplicates."""
        # 1. Validate
        errors = validate_record(record)
        errors.extend(record.validation_errors)  # carry forward normalization errors
        is_valid = len(errors) == 0

        # 2. Exact dedup via SHA-256 of raw bytes
        sha256 = record.provenance.content_hash_sha256
        is_exact_dup = False
        dup_of: str | None = None
        dup_type: str | None = None

        if sha256:
            if self._store.has_exact(sha256):
                is_exact_dup = True
                dup_of = self._store.get_exact_id(sha256)
                dup_type = "exact"
                log.info("dedup_exact", record_id=record.record_id, duplicate_of=dup_of)
            else:
                self._store.add_exact(sha256, record.record_id)

        # 3. Near-dedup via SimHash of content
        sh_hex = simhash_hex(record.content)
        record.content_simhash = sh_hex

        if not is_exact_dup:
            near_dup_id = self._store.find_near(sh_hex)
            if near_dup_id:
                dup_of = near_dup_id
                dup_type = "near"
                log.info("dedup_near", record_id=record.record_id, duplicate_of=dup_of)
            else:
                self._store.add_near(sh_hex, record.record_id)

        is_duplicate = is_exact_dup or (dup_of is not None)

        # 4. Write back to record
        record.validation_errors = errors
        record.is_valid = is_valid
        record.is_duplicate = is_duplicate
        record.duplicate_of = dup_of

        return QualityResult(
            record=record,
            validation_errors=errors,
            is_valid=is_valid,
            is_duplicate=is_duplicate,
            duplicate_of=dup_of,
            duplicate_type=dup_type,
        )

    def store_stats(self) -> dict[str, int]:
        return self._store.stats()
