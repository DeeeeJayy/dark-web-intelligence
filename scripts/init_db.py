"""Create all database tables."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.registry.db import Base, engine
from src.registry.models import Source, CrawlJob  # noqa: F401 — registers models

Base.metadata.create_all(bind=engine)
print("Database tables created.")
