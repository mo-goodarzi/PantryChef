"""Label ingredients with an LLM in batches, caching every result to a JSON file.

There are two labeling tasks, each with its own prompt and cache file:
- INGREDIENT_TASK: category, allergens, diet fields
- QUANTITY_TASK: quantity_matters (kept separate so it can be redefined and relabeled
  cheaply without touching the other labels)

The cache makes runs resumable and reruns free: only names without a cached label are
sent. Labels are stored as returned by the LLM; combining them with the rule-based
allergens happens later (the LLM can only add allergens, never remove them).
"""

import json
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.ingredient import (
    IngredientLabel,
    IngredientLabelBatch,
    QuantityLabel,
    QuantityLabelBatch,
)
from pantry_chef.observability import get_logger, span

log = get_logger("ingredients.labeler")


@dataclass(frozen=True)
class LabelTask:
    prompt_name: str
    item_schema: type[BaseModel]  # one label; must have a `name` field
    batch_schema: type[BaseModel]  # LLM output; must have a `labels` list


INGREDIENT_TASK = LabelTask("ingredient_labeling", IngredientLabel, IngredientLabelBatch)
QUANTITY_TASK = LabelTask("quantity_matters", QuantityLabel, QuantityLabelBatch)


@dataclass
class LabelRunSummary:
    requested: int = 0
    already_cached: int = 0
    labeled: int = 0
    missing: int = 0  # names the LLM skipped; retried on the next run
    failed_batches: int = 0


def load_cache(path: Path, item_schema: type[BaseModel] = IngredientLabel) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {name: item_schema.model_validate(label) for name, label in data.items()}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    """Write atomically, so an interrupted run never corrupts the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    data = {name: label.model_dump(mode="json") for name, label in sorted(cache.items())}
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def batches(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def label_batch(llm: StructuredLLM, names: list[str], task: LabelTask) -> dict[str, Any]:
    """One LLM call. Returns labels only for names that were actually asked for."""
    prompt = load_prompt(task.prompt_name)
    result: Any = llm.generate(prompt, task.batch_schema, ingredients=json.dumps(names))
    wanted = set(names)
    return {label.name: label for label in result.labels if label.name in wanted}


def traced_label_batch(
    llm: StructuredLLM, batch: list[str], number: int, task: LabelTask
) -> dict[str, Any]:
    with span("labeler.batch", task=task.prompt_name, batch=number, size=len(batch)):
        return label_batch(llm, batch, task)


def label_ingredients(
    names: list[str],
    llm: StructuredLLM,
    cache_path: Path,
    task: LabelTask = INGREDIENT_TASK,
    batch_size: int = 50,
    limit: int | None = None,
    workers: int = 1,
) -> LabelRunSummary:
    """Label every name that is not cached yet, most frequent first.

    With workers > 1, batches run in parallel threads; results are merged and saved in
    the main thread as each batch finishes.
    """
    cache = load_cache(cache_path, task.item_schema)
    unique_names = list(dict.fromkeys(names))
    todo = [name for name in unique_names if name not in cache]
    summary = LabelRunSummary(
        requested=len(unique_names), already_cached=len(unique_names) - len(todo)
    )
    if limit is not None:
        todo = todo[:limit]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        # copy_context keeps trace_id/session_id on log lines written by worker threads
        futures = {
            executor.submit(copy_context().run, traced_label_batch, llm, batch, number, task): batch
            for number, batch in enumerate(batches(todo, batch_size), start=1)
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                labels = future.result()
            except Exception as error:  # one bad batch must not stop the run
                summary.failed_batches += 1
                log.warning("labeler.batch_failed", size=len(batch), error=str(error))
                continue
            cache.update(labels)
            save_cache(cache_path, cache)
            summary.labeled += len(labels)
            summary.missing += len(batch) - len(labels)

    return summary
