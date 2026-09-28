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
from pantry_chef.observability import generation, get_logger

T = TypeVar("T", bound=BaseModel)

log = get_logger("llm")


class StructuredLLM(Protocol):
    def generate(self, prompt: Prompt, schema: type[T], **variables: str) -> T:
        """Render the prompt, call the model and return a validated `schema` object."""
        ...


class LangChainStructuredLLM:
    """StructuredLLM backed by any LangChain chat model.

    Network errors and rate limits are retried with exponential backoff by the provider
    client (max_retries); an answer that does not fit the schema is asked for once more.
    """

    def __init__(self, model: BaseChatModel, model_name: str, parse_attempts: int = 2):
        self.model = model
        self.model_name = model_name
        self.parse_attempts = parse_attempts

    def generate(self, prompt: Prompt, schema: type[T], **variables: str) -> T:
        text = prompt.render(**variables)
        for attempt in range(1, self.parse_attempts + 1):
            try:
                return self._generate_once(prompt, schema, text)
            except ValueError as error:
                if attempt == self.parse_attempts:
                    raise
                log.warning("llm.retry", prompt=prompt.name, attempt=attempt, error=str(error))
        raise AssertionError("unreachable")

    def _generate_once(self, prompt: Prompt, schema: type[T], text: str) -> T:
        runnable = self.model.with_structured_output(schema, include_raw=True)
        start = time.perf_counter()
        with generation(
            f"llm.{prompt.name}",
            model=self.model_name,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            input=text,
            sensitive=prompt.sensitive,
        ) as record:
            result = runnable.invoke(text)
            if not isinstance(result, dict):  # include_raw=True always returns a dict
                raise TypeError(f"unexpected structured output result: {type(result).__name__}")
            usage = getattr(result["raw"], "usage_metadata", None) or {}
            record.input_tokens = usage.get("input_tokens")
            record.output_tokens = usage.get("output_tokens")
            parsed = result.get("parsed")
            record.output = (
                parsed.model_dump(mode="json") if isinstance(parsed, BaseModel) else None
            )

        log.info(
            "llm.generation",
            model=self.model_name,
            prompt=prompt.name,
            prompt_version=prompt.version,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=round((time.perf_counter() - start) * 1000),
        )
        if result.get("parsing_error") is not None:
            raise ValueError(f"structured output failed: {result['parsing_error']}")
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
