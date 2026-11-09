"""Structured JSON logging."""

import logging
import sys
from typing import Any, TextIO

import structlog
from structlog.typing import FilteringBoundLogger


def configure_logging(level: str = "INFO", *, stream: TextIO | None = None) -> None:
    """Emit one JSON object per line, with level and ISO UTC timestamp, to ``stream`` or stderr."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(**bind: Any) -> FilteringBoundLogger:
    return structlog.get_logger().bind(**bind)
