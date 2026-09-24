"""Structured JSON logging with structlog.

Every log line carries the current trace_id and session_id (bound with bind_context),
so logs can be matched with traces.
"""

import logging
import sys

import structlog

MASK = "***"


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure structlog once at program start."""
    renderer = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a logger; `name` is added to every line as `component`.

    The logger is lazy, so it is safe to create at import time, before configure_logging().
    """
    return structlog.get_logger(component=name) if name else structlog.get_logger()


def bind_context(**values: str | None) -> None:
    """Bind values (e.g. trace_id, session_id, node) to all later log lines in this context."""
    structlog.contextvars.bind_contextvars(**{k: v for k, v in values.items() if v is not None})


def mask(value: object) -> str:
    """Hide free-text health information before it reaches logs or traces.

    Keeps only whether a value was given, never its content.
    """
    if value is None or value == "":
        return ""
    return MASK
