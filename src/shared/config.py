"""Central configuration loaded from environment / .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg2://cyart:cyart@localhost:5432/darkweb"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # Backend ingestion
    backend_ingest_url: str = "http://localhost:8001/api/v1/ingest"
    backend_api_key: str = "mock-api-key"

    # Tor proxy (empty = disabled)
    tor_socks_proxy: str = ""

    # Logging
    log_level: str = "INFO"

    # Crawl behaviour
    crawl_concurrency: int = 4
    crawl_rate_limit: int = 30          # pages per minute ceiling
    max_retries: int = 3
    retry_backoff_base: float = 2.0     # seconds, exponential base
    request_timeout: int = 45           # seconds

    # Health
    health_check_interval: int = 300    # seconds
    health_slow_response_ms: int = 2000        # a single slow success degrades
    health_stale_success_age_s: int = 86400    # last success older than this -> stale
    health_low_success_ratio: float = 0.5      # success ratio below this degrades

    # Config-driven source registration
    sources_config_path: str = "config/sources.json"

    # Crash/resume checkpoint store (file-based SQLite, separate from registry)
    crawl_checkpoint_db: str = "data/crawl_checkpoints.db"


settings = Settings()
