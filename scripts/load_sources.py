"""Load sources into the registry from a JSON config file.

Respects the authorization hard gate: entries without an auth_ref are skipped
(never registered or queued). Run with the project venv:

    venv\\Scripts\\python.exe scripts\\load_sources.py [path/to/sources.json]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.registry.config_loader import load_sources_from_config
from src.registry.db import SessionLocal
from src.registry.service import RegistryService
from src.shared.config import settings


def main(path: str | None = None) -> None:
    db = SessionLocal()
    try:
        svc = RegistryService(db)
        summary = load_sources_from_config(path or settings.sources_config_path, svc)
        print(
            f"registered={summary['registered']} "
            f"skipped={summary['skipped']} "
            f"errors={summary['errors']}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    main(config_path)
