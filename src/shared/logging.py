"""Structured logging setup with run/trace id propagation."""
from __future__ import annotations

import logging
import sys
import uuid

import structlog

from src.shared.config import settings


def configure_logging(run_id: str | None = None) -> str:
    """Configure structlog. Returns the run_id used."""
    run_id = run_id or str(uuid.uuid4())[:8]

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )

    structlog.contextvars.bind_contextvars(run_id=run_id)
    return run_id


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
