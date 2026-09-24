"""Tracing interface.

For now every helper only writes structured logs, so code can be instrumented from the
start. In Phase 5 the same helpers will also send data to Langfuse when keys are set;
without keys they stay no-ops, so tests and CI never need Langfuse.
"""

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog

from pantry_chef.observability.logging import get_logger

P = ParamSpec("P")
R = TypeVar("R")

log = get_logger("tracing")


@contextmanager
def trace(name: str, session_id: str | None = None) -> Iterator[str]:
    """Start one trace per user request. Yields the new trace_id."""
    trace_id = uuid.uuid4().hex
    ids = {k: v for k, v in {"trace_id": trace_id, "session_id": session_id}.items() if v}
    # bound_contextvars removes the ids again when the trace ends.
    with structlog.contextvars.bound_contextvars(**ids), span(name):
        yield trace_id


@contextmanager
def span(name: str, **metadata: Any) -> Iterator[None]:
    """Time a unit of work (graph node, search stage, verifier check) and log the result."""
    start = time.perf_counter()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info("span", span=name, status=status, duration_ms=duration_ms, **metadata)


def score(name: str, value: float, comment: str | None = None) -> None:
    """Record a quality score (e.g. allergen_violation, verifier_pass_rate)."""
    log.info("score", score=name, value=value, comment=comment)


def traced(name: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator: run the function inside a span named after it."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        span_name = name or func.__qualname__

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with span(span_name):
                return func(*args, **kwargs)

        return wrapper

    return decorator
