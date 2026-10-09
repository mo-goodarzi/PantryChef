"""Tokens and cost of every LLM call in this process, per model.

LangChainStructuredLLM records each call here, so an eval can report what a run cost and
stop at a spending cap without passing counters through the graph.
"""

import threading
from dataclasses import dataclass

# USD per 1M tokens (input, output), checked 2026-10-06; update when prices change.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
}


@dataclass(frozen=True)
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


_lock = threading.Lock()  # the labeling script calls the LLM from worker threads
_usage: dict[str, ModelUsage] = {}


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    with _lock:
        old = _usage.get(model, ModelUsage())
        _usage[model] = ModelUsage(
            old.calls + 1, old.input_tokens + input_tokens, old.output_tokens + output_tokens
        )


def snapshot() -> dict[str, ModelUsage]:
    with _lock:
        return dict(_usage)


def since(before: dict[str, ModelUsage]) -> dict[str, ModelUsage]:
    """Usage after `before` (an earlier snapshot), per model."""
    now = snapshot()
    diff = {}
    for model, u in now.items():
        b = before.get(model, ModelUsage())
        if u.calls > b.calls:
            diff[model] = ModelUsage(
                u.calls - b.calls,
                u.input_tokens - b.input_tokens,
                u.output_tokens - b.output_tokens,
            )
    return diff


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def unpriced_models(usage: dict[str, ModelUsage]) -> list[str]:
    """Models in the usage that have no price in PRICES."""
    return sorted(model for model in usage if model not in PRICES)


def priced_cost(usage: dict[str, ModelUsage]) -> float:
    """USD for the models that have a price; the others count as 0, so a report must name
    them (unpriced_models). A spending cap uses total_cost instead."""
    return sum(cost_usd(m, u.input_tokens, u.output_tokens) or 0.0 for m, u in usage.items())


def total_cost(usage: dict[str, ModelUsage]) -> float:
    """USD for the usage; a model without a price is an error, so a cap is never skipped."""
    missing = unpriced_models(usage)
    if missing:
        raise ValueError(f"no price for {missing[0]}; add it to PRICES in llm/usage.py")
    return priced_cost(usage)
