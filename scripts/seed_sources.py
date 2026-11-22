"""
Seed the registry with synthetic research-permitted test sources.
All sources are simulated/fixture-based; no real dark-web sources.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.registry.db import SessionLocal
from src.registry.service import RegistryService

SOURCES = [
    {
        "name": "Research Forum Fixture A",
        "url": "http://localhost:9999/forum_a",
        "source_type": "forum",
        "auth_ref": "AUTH-2026-001",
        "auth_note": "Synthetic research fixture for testing. No real content.",
        "crawl_frequency_seconds": 3600,
        "crawl_policy": {"depth": 2, "rate_ppm": 10, "timeout_s": 30, "respect_robots": True, "max_pages": 50},
    },
    {
        "name": "Research Paste Fixture B",
        "url": "http://localhost:9999/paste_b",
        "source_type": "paste",
        "auth_ref": "AUTH-2026-002",
        "auth_note": "Synthetic research fixture for testing.",
        "crawl_frequency_seconds": 900,
        "crawl_policy": {"depth": 1, "rate_ppm": 20, "timeout_s": 20, "respect_robots": True, "max_pages": 20},
    },
    {
        "name": "Research Blog Fixture C",
        "url": "http://localhost:9999/blog_c",
        "source_type": "blog",
        "auth_ref": "AUTH-2026-003",
        "auth_note": "Synthetic research fixture for testing.",
        "crawl_frequency_seconds": 7200,
    },
]


def main():
    db = SessionLocal()
    svc = RegistryService(db)
    for s in SOURCES:
        existing = svc.get_source_by_url(s["url"])
        if existing:
            print(f"Already exists: {s['url']}")
            continue
        policy = s.pop("crawl_policy", None)
        source = svc.add_source(**s, crawl_policy=policy)
        print(f"Registered: {source.id} → {source.url}")
    db.close()


if __name__ == "__main__":
    main()
