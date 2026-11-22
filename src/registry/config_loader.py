"""Config-driven source registration (JSON, stdlib only — no new dependency).

Loads sources from a JSON array file and registers them via the existing
``RegistryService.add_source``. The authorization hard gate is preserved:
entries with an empty/missing ``auth_ref`` raise ``AuthorizationError`` inside
``add_source`` and are skipped here (counted as skipped, NEVER registered or
queued). The loader is idempotent — entries whose URL already exists are
skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.registry.service import AuthorizationError, RegistryService
from src.shared.logging import get_logger

log = get_logger(__name__)

# Keyword-only arguments accepted by RegistryService.add_source.
_ADD_SOURCE_KEYS = (
    "name",
    "url",
    "source_type",
    "auth_ref",
    "auth_note",
    "crawl_policy",
    "crawl_frequency_seconds",
)


def load_sources_from_config(path: str, registry: RegistryService) -> dict[str, int]:
    """Load sources from a JSON array config file.

    Returns ``{"registered": n, "skipped": n, "errors": n}``.

    - entries whose URL already exists in the registry are skipped;
    - entries with empty/missing ``auth_ref`` raise ``AuthorizationError`` and
      are skipped (NOT registered) — the hard gate is respected;
    - any other unexpected error is counted as an error and the loader continues.
    """
    counts = {"registered": 0, "skipped": 0, "errors": 0}
    raw = Path(path).read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = json.loads(raw)

    for entry in entries:
        url = entry.get("url")
        if not url:
            log.warning("config_source_missing_url", name=entry.get("name"))
            counts["errors"] += 1
            continue

        if registry.get_source_by_url(url) is not None:
            log.info("config_source_already_registered", url=url)
            counts["skipped"] += 1
            continue

        kwargs = {k: entry[k] for k in _ADD_SOURCE_KEYS if k in entry}
        # Ensure auth_ref is always passed so a missing/empty value is rejected
        # by the hard gate (AuthorizationError) rather than raising a TypeError.
        kwargs.setdefault("auth_ref", "")

        try:
            registry.add_source(**kwargs)
            counts["registered"] += 1
        except AuthorizationError:
            log.warning("config_source_skipped_no_auth", url=url)
            counts["skipped"] += 1
        except Exception as exc:  # pragma: no cover - defensive, logged
            log.warning("config_source_error", url=url, error=str(exc))
            counts["errors"] += 1

    return counts
