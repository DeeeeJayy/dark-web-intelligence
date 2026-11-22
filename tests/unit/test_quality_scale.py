"""Quality at-scale test (FEAT-004).

Generates ~200 synthetic NormalizedRecords with a KNOWN distribution of
exact duplicates, near duplicates (SimHash Hamming <= 3), unique docs, and
invalid (too-short) docs, runs the QualityChecker over all of them, and
asserts the dedup counts match the injected distribution. No live network,
no fixtures beyond the in-process models.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.quality.validator import (
    QualityChecker,
    hamming_distance_hex,
    simhash_hex,
)
from src.shared.models import NormalizedRecord, RecordProvenance


def _prov(sha256: str, run_id: str = "scale_run") -> RecordProvenance:
    return RecordProvenance(
        source_id="src_scale",
        source_url="http://localhost:9999/scale",
        fetched_url="http://localhost:9999/scale",
        fetch_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        crawl_run_id=run_id,
        http_status=200,
        content_hash_sha256=sha256,
    )


def _record(content: str, sha256: str, url: str) -> NormalizedRecord:
    return NormalizedRecord(
        source="src_scale",
        url=url,
        content=content,
        provenance=_prov(sha256=sha256),
    )


def _sha(n: int) -> str:
    """A distinct 64-char hex sha256 placeholder for synthetic records."""
    return f"{n:064x}"


def _build_scale_dataset() -> list[NormalizedRecord]:
    """Build 200 records with a KNOWN dedup distribution.

    Layout (every original precedes its duplicate):
      1. 100 unique valid docs        -> not duplicate
      2.  20 near-duplicate bases      -> not duplicate (originals)
      3.  20 exact-duplicate bases     -> not duplicate (originals)
      4.  20 invalid docs (too short)  -> invalid, not duplicate
      5.  20 near-duplicate variants   -> near duplicate (Hamming <= 3)
      6.  20 exact-duplicate copies    -> exact duplicate (same sha256)

    Expected: exact=20, near=20, invalid=20, valid-non-dup=140, total=200,
    duplicate_rate = 40/200 = 0.2.

    Each doc is dominated by a single unique repeated token so its SimHash
    equals md5(token) and originals are pairwise far apart (Hamming >> 3);
    near pairs share the dominant token (one extra word) so their SimHash
    Hamming distance is 0 (<= threshold).
    """
    records: list[NormalizedRecord] = []

    # 1. 100 unique valid docs.
    for i in range(100):
        token = f"uniquetoken{i}"
        content = (token + " ") * 30 + f"document number {i} for scale testing."
        records.append(_record(content, _sha(i), f"http://localhost:9999/scale/unique/{i}"))

    # 2. 20 near-duplicate bases.
    near_tokens: list[str] = []
    for i in range(20):
        token = f"neartopic{i}"
        near_tokens.append(token)
        content = (token + " ") * 30
        records.append(_record(content, _sha(1000 + i), f"http://localhost:9999/scale/near_base/{i}"))

    # 3. 20 exact-duplicate bases.
    exact_contents: list[str] = []
    for i in range(20):
        token = f"exactbase{i}"
        content = (token + " ") * 30 + f"marketplace listing {i}."
        exact_contents.append(content)
        records.append(_record(content, _sha(2000 + i), f"http://localhost:9999/scale/exact_base/{i}"))

    # 4. 20 invalid docs (too short). Distinct single token -> distinct SimHash,
    #    so they are invalid but NOT duplicates.
    for i in range(20):
        content = f"short{i}"  # length < 10 -> content_too_short
        records.append(_record(content, _sha(3000 + i), f"http://localhost:9999/scale/invalid/{i}"))

    # 5. 20 near-duplicate variants: same dominant token + one extra word.
    for i, token in enumerate(near_tokens):
        base_content = (token + " ") * 30
        variant_content = (token + " ") * 29 + f"{token} update"
        # Sanity: the variant must actually be a near duplicate (Hamming <= 3).
        assert hamming_distance_hex(simhash_hex(base_content), simhash_hex(variant_content)) <= 3
        records.append(_record(variant_content, _sha(1100 + i), f"http://localhost:9999/scale/near_variant/{i}"))

    # 6. 20 exact-duplicate copies: same sha256 as their base.
    for i, content in enumerate(exact_contents):
        records.append(_record(content, _sha(2000 + i), f"http://localhost:9999/scale/exact_copy/{i}"))

    return records


class TestQualityAtScale:
    def test_dedup_counts_match_injected_distribution(self):
        records = _build_scale_dataset()
        assert len(records) == 200

        checker = QualityChecker()
        exact = 0
        near = 0
        invalid = 0
        results = []

        for r in records:
            # Must not raise on any record, including the invalid ones.
            qr = checker.check(r)
            results.append(qr)
            if qr.duplicate_type == "exact":
                exact += 1
            elif qr.duplicate_type == "near":
                near += 1
            if not qr.is_valid:
                invalid += 1

        total = len(records)
        duplicate_rate = (exact + near) / total

        assert exact == 20, f"expected 20 exact duplicates, got {exact}"
        assert near == 20, f"expected 20 near duplicates, got {near}"
        assert invalid == 20, f"expected 20 invalid records, got {invalid}"
        assert abs(duplicate_rate - 0.2) < 0.001, f"expected dup rate 0.2, got {duplicate_rate}"

        # Cross-check: 140 originals are valid and non-duplicate.
        valid_non_dup = sum(1 for qr in results if qr.is_valid and not qr.is_duplicate)
        assert valid_non_dup == 140, f"expected 140 valid non-dup, got {valid_non_dup}"

        # The dedup store holds one exact entry per record that is NOT an exact
        # duplicate (near variants have their own unique sha256 and ARE added,
        # even though they are near duplicates). = 200 total - 20 exact dups.
        stats = checker.store_stats()
        assert stats["exact_seen"] == 180
