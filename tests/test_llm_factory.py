"""StructuredLLM over a LangChain model: validation, retry, tracing (no real model)."""

import pytest
from pydantic import BaseModel

from pantry_chef.llm.factory import LangChainStructuredLLM
from pantry_chef.llm.prompt_loader import parse_prompt_file
from pantry_chef.observability import GenerationRecord


class Answer(BaseModel):
    value: int


class Raw:
    usage_metadata = {"input_tokens": 12, "output_tokens": 3}


class FakeChatModel:
    """Mimics with_structured_output(include_raw=True): returns scripted results."""

    def __init__(self, results):
        self.results = list(results)
        self.prompts = []

    def with_structured_output(self, schema, include_raw):
        assert include_raw
        return self

    def invoke(self, text):
        self.prompts.append(text)
        return self.results.pop(0)


PROMPT = parse_prompt_file("---\nname: demo\nversion: 3\n---\nSay $what")
OK = {"raw": Raw(), "parsed": Answer(value=7), "parsing_error": None}
BROKEN = {"raw": Raw(), "parsed": None, "parsing_error": "invalid json"}


def test_returns_the_validated_object():
    model = FakeChatModel([OK])
    assert LangChainStructuredLLM(model, "m").generate(PROMPT, Answer, what="7") == Answer(value=7)
    assert model.prompts == ["Say 7"]


def test_retries_once_after_a_parsing_error():
    model = FakeChatModel([BROKEN, OK])
    assert LangChainStructuredLLM(model, "m").generate(PROMPT, Answer, what="7").value == 7
    assert len(model.prompts) == 2


def test_gives_up_after_the_retry():
    model = FakeChatModel([BROKEN, BROKEN])
    with pytest.raises(ValueError, match="structured output failed"):
        LangChainStructuredLLM(model, "m").generate(PROMPT, Answer, what="7")


def test_each_call_is_traced_as_a_generation(monkeypatch):
    from pantry_chef.llm import factory

    calls = []

    class Recorder:
        def __init__(self, name, **fields):
            calls.append((name, fields))
            self.record = GenerationRecord()

        def __enter__(self):
            return self.record

        def __exit__(self, *exc):
            calls.append(("usage", self.record.input_tokens, self.record.output_tokens))

    monkeypatch.setattr(factory, "generation", Recorder)
    sensitive = parse_prompt_file("---\nname: safety\nversion: 1\nsensitive: true\n---\n$x")
    LangChainStructuredLLM(FakeChatModel([OK]), "m1").generate(sensitive, Answer, x="diabetes")
    name, fields = calls[0]
    assert name == "llm.safety"
    assert fields["model"] == "m1" and fields["prompt_version"] == "1"
    assert fields["sensitive"] is True
    assert calls[1] == ("usage", 12, 3)
