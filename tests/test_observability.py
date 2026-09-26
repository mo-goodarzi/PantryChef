import json

import pytest

from pantry_chef.observability import configure_logging, mask, score, span, trace, traced


@pytest.fixture(autouse=True)
def json_logging():
    configure_logging(level="INFO", json_output=True)


def read_lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_mask_hides_health_text():
    assert mask("I have type 2 diabetes") == "***"


def test_mask_keeps_empty_values_empty():
    assert mask(None) == ""
    assert mask("") == ""


def test_span_logs_name_status_and_duration(capsys):
    with span("search.filters", candidates=12):
        pass

    [line] = read_lines(capsys)
    assert line["span"] == "search.filters"
    assert line["status"] == "ok"
    assert line["candidates"] == 12
    assert line["duration_ms"] >= 0


def test_span_marks_errors_and_reraises(capsys):
    with pytest.raises(ValueError), span("verify"):
        raise ValueError("boom")

    [line] = read_lines(capsys)
    assert line["status"] == "error"


def test_trace_adds_ids_to_every_line_inside_it(capsys):
    with trace("request", session_id="s1") as trace_id:
        score("allergen_violation", 0)

    lines = read_lines(capsys)
    assert len(lines) == 2  # the score and the closing request span
    assert all(line["trace_id"] == trace_id for line in lines)
    assert all(line["session_id"] == "s1" for line in lines)


def test_trace_ids_do_not_leak_after_trace_ends(capsys):
    with trace("request", session_id="s1"):
        pass
    capsys.readouterr()

    score("retries", 1)

    [line] = read_lines(capsys)
    assert "trace_id" not in line
    assert "session_id" not in line


def test_traced_decorator_wraps_function_in_span(capsys):
    @traced("parse_request")
    def parse(text: str) -> str:
        return text.upper()

    assert parse("eggs") == "EGGS"
    [line] = read_lines(capsys)
    assert line["span"] == "parse_request"
