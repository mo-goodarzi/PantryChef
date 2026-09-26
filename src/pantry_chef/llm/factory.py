"""LLM provider factory and structured-output helper.

Business logic depends only on the StructuredLLM protocol, so the provider can be
swapped (or replaced by a fake in tests) without touching callers.
"""

import time
from typing import Protocol, TypeVar

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from pantry_chef.config import Settings
from pantry_chef.llm.prompt_loader import Prompt
from pantry_chef.observability import get_logger, span

T = TypeVar("T", bound=BaseModel)

log = get_logger("llm")


class StructuredLLM(Protocol):
    def generate(self, prompt: Prompt, schema: type[T], **variables: str) -> T:
        """Render the prompt, call the model and return a validated `schema` object."""
        ...


class LangChainStructuredLLM:
    """StructuredLLM backed by any LangChain chat model."""

    def __init__(self, model: BaseChatModel, model_name: str):
        self.model = model
        self.model_name = model_name

    def generate(self, prompt: Prompt, schema: type[T], **variables: str) -> T:
        runnable = self.model.with_structured_output(schema, include_raw=True)
        start = time.perf_counter()
        with span("llm.generate", prompt=prompt.name, prompt_version=prompt.version):
            result = runnable.invoke(prompt.render(**variables))
        if not isinstance(result, dict):  # include_raw=True always returns a dict
            raise TypeError(f"unexpected structured output result: {type(result).__name__}")

        usage = getattr(result["raw"], "usage_metadata", None) or {}
        log.info(
            "llm.generation",
            model=self.model_name,
            prompt=prompt.name,
            prompt_version=prompt.version,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=round((time.perf_counter() - start) * 1000),
        )
        if result.get("parsing_error") is not None:
            raise ValueError(f"structured output failed: {result['parsing_error']}")
        parsed = result["parsed"]
        if not isinstance(parsed, schema):
            raise ValueError(f"expected {schema.__name__}, got {type(parsed).__name__}")
        return parsed


def create_llm(settings: Settings) -> StructuredLLM:
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        if settings.openai_api_key is None:
            raise ValueError("OPENAI_API_KEY is not set")
        model = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            reasoning_effort=settings.llm_reasoning_effort,
            timeout=settings.llm_timeout_seconds,
            max_retries=3,
        )
        return LangChainStructuredLLM(model, settings.llm_model)
    raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
