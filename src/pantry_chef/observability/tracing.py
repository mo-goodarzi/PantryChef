"""Tracing interface: structured logs always, Langfuse when keys are configured.

Business code only uses trace() / span() / score() / generation() / @traced. Without
Langfuse keys every helper just writes structlog JSON, so tests and CI need no keys.
With keys (configure_tracing), the same calls create one Langfuse trace per user request
with nested spans, generations (model, prompt version, tokens) and scores.

Privacy: only what the caller passes is sent. Spans carry metadata (counts, codes), never
the conversation state; generations from prompts marked `sensitive` (health answers) are
sent with their input and output masked.
"""

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import structlog

from pantry_chef.observability.logging import MASK, get_logger, hash_user_id

P = ParamSpec("P")
R = TypeVar("R")

log = get_logger("tracing")

_langfuse: Any = None  # the Langfuse client when configured, else None (log-only)


def configure_tracing(
    public_key: str | None, secret_key: str | None, host: str, **client_options: Any
) -> bool:
    """Connect Langfuse when both keys are given; otherwise stay log-only.

    Returns whether Langfuse is on. `client_options` go to the Langfuse client (tests
    pass an in-memory span exporter).
    """
    global _langfuse
    if not (public_key and secret_key):
        _langfuse = None
        return False
    from langfuse import Langfuse

    _langfuse = Langfuse(public_key=public_key, secret_key=secret_key, host=host, **client_options)
    return True


def tracing_from_settings(settings: Any) -> bool:
    """configure_tracing() with the keys from Settings (SecretStr values)."""
    public = settings.langfuse_public_key
    secret = settings.langfuse_secret_key
    return configure_tracing(
        public.get_secret_value() if public else None,
        secret.get_secret_value() if secret else None,
        settings.langfuse_host,
    )


def flush_tracing() -> None:
    """Send buffered traces (call before a short-lived program exits)."""
    if _langfuse is not None:
        _langfuse.flush()


@contextmanager
def _observation(name: str, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """A Langfuse observation nested under the current one, or None when log-only."""
    if _langfuse is None:
        yield None
        return
    with _langfuse.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
        yield obs


@contextmanager
def _timed_log(name: str, observation: Any = None, **metadata: Any) -> Iterator[None]:
    start = time.perf_counter()
    status = "ok"
    try:
        yield
    except Exception as error:
        status = "error"
        if observation is not None:
            observation.update(level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info("span", span=name, status=status, duration_ms=duration_ms, **metadata)


@contextmanager
def trace(name: str, session_id: str | None = None, user_id: str | None = None) -> Iterator[str]:
    """Start one trace per user request. Yields the trace_id (Langfuse's when on).

    `user_id` is hashed before it reaches logs or Langfuse.
    """
    hashed_user = hash_user_id(user_id) if user_id else None
    with ExitStack() as stack:
        root = stack.enter_context(_observation(name))
        trace_id = uuid.uuid4().hex
        if root is not None:
            from langfuse import propagate_attributes

            stack.enter_context(
                propagate_attributes(session_id=session_id, user_id=hashed_user, trace_name=name)
            )
            trace_id = _langfuse.get_current_trace_id() or trace_id
        ids = {"trace_id": trace_id, "session_id": session_id, "user": hashed_user}
        # bound_contextvars removes the ids again when the trace ends.
        stack.enter_context(
            structlog.contextvars.bound_contextvars(**{k: v for k, v in ids.items() if v})
        )
        stack.enter_context(_timed_log(name, root))
        yield trace_id


@contextmanager
def span(name: str, **metadata: Any) -> Iterator[None]:
    """Time a unit of work (graph node, search stage, verifier check) and log the result."""
    with _observation(name, metadata=metadata or None) as obs, _timed_log(name, obs, **metadata):
        yield


def score(name: str, value: float, comment: str | None = None) -> None:
    """Record a quality score (e.g. allergen_violation, verifier_pass_rate) on the trace."""
    log.info("score", score=name, value=value, comment=comment)
    if _langfuse is not None:
        _langfuse.score_current_trace(name=name, value=value, comment=comment)


@dataclass
class GenerationRecord:
    """Filled in by the caller once the model has answered."""

    output: Any = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@contextmanager
def generation(
    name: str,
    *,
    model: str,
    prompt_name: str,
    prompt_version: str,
    input: Any = None,
    sensitive: bool = False,
) -> Iterator[GenerationRecord]:
    """One LLM call. With `sensitive`, input and output are masked in the trace."""
    record = GenerationRecord()
    fields = {
        "model": model,
        "version": prompt_version,
        "metadata": {"prompt": prompt_name, "prompt_version": prompt_version},
        "input": MASK if sensitive and input is not None else input,
    }
    with _observation(name, "generation", **fields) as obs, _timed_log(name, obs):
        yield record
        if obs is not None:
            usage = {"input": record.input_tokens, "output": record.output_tokens}
            obs.update(
                output=MASK if sensitive else record.output,
                usage_details={k: v for k, v in usage.items() if v is not None},
            )


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
