"""Langfuse behind the tracing helpers, exported to memory (no network, no real keys)."""

import json
import uuid

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pantry_chef.observability import (
    configure_logging,
    configure_tracing,
    flush_tracing,
    generation,
    hash_user_id,
    score,
    span,
    trace,
    tracing,
)


@pytest.fixture
def spans(monkeypatch):
    configure_logging(level="WARNING")
    exporter = InMemorySpanExporter()
    # Langfuse keeps one client per public key: a fresh key per test gets a fresh exporter.
    key = f"pk-test-{uuid.uuid4().hex}"
    assert configure_tracing(key, "sk-test", "http://127.0.0.1:9", span_exporter=exporter)
    scores = []
    # Scores go over HTTP, not OpenTelemetry: record them instead.
    monkeypatch.setattr(
        tracing._langfuse, "score_current_trace", lambda **kw: scores.append(kw), raising=False
    )

    def finished():
        flush_tracing()
        return {s.name: s for s in exporter.get_finished_spans()}

    yield finished, scores
    configure_tracing(None, None, "")


def attrs(span_data):
    return dict(span_data.attributes)


def test_without_keys_tracing_stays_log_only():
    assert not configure_tracing(None, "sk", "http://x")
    assert tracing._langfuse is None


def test_one_trace_per_request_with_nested_spans_and_session(spans):
    finished, _ = spans
    with (
        trace("chat_turn", session_id="thread-1", user_id="alice") as trace_id,
        span("search.coverage", candidates=12),
    ):
        pass
    by_name = finished()
    root, child = by_name["chat_turn"], by_name["search.coverage"]
    assert child.parent.span_id == root.context.span_id
    assert format(root.context.trace_id, "032x") == trace_id  # logs and traces match
    assert attrs(child)["session.id"] == "thread-1"
    assert attrs(child)["user.id"] == hash_user_id("alice")  # never the raw name
    assert "alice" not in json.dumps(attrs(root))


def test_span_metadata_is_sent(spans):
    finished, _ = spans
    with trace("t"), span("verify", approved=3):
        pass
    assert "approved" in json.dumps(attrs(finished()["verify"]))


def test_generation_records_model_prompt_version_and_tokens(spans):
    finished, _ = spans
    with (
        trace("t"),
        generation(
            "llm.rerank", model="m1", prompt_name="rerank", prompt_version="1", input="pick 5"
        ) as record,
    ):
        record.output = {"picks": [1]}
        record.input_tokens, record.output_tokens = 30, 5
    gen = attrs(finished()["llm.rerank"])
    assert gen["langfuse.observation.type"] == "generation"
    assert gen["langfuse.observation.model.name"] == "m1"
    assert json.loads(gen["langfuse.observation.usage_details"]) == {"input": 30, "output": 5}
    assert "pick 5" in gen["langfuse.observation.input"]


def test_sensitive_generation_masks_health_text(spans):
    finished, _ = spans
    with (
        trace("t"),
        generation(
            "llm.safety_intake",
            model="m1",
            prompt_name="safety_intake",
            prompt_version="1",
            input="I have type 2 diabetes",
            sensitive=True,
        ) as record,
    ):
        record.output = {"conditions": ["diabetes"]}
    sent = json.dumps(attrs(finished()["llm.safety_intake"]))
    assert "diabetes" not in sent


def test_scores_go_to_the_current_trace(spans):
    _, scores = spans
    with trace("t"):
        score("allergen_violation", 0)
    assert scores == [{"name": "allergen_violation", "value": 0, "comment": None}]


def test_errors_are_marked_on_the_span(spans):
    finished, _ = spans
    with pytest.raises(ValueError), trace("t"), span("verify"):
        raise ValueError("boom")
    assert attrs(finished()["verify"])["langfuse.observation.level"] == "ERROR"
