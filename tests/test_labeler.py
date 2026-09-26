import json

import pytest

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.labeler import QUANTITY_TASK, label_ingredients, load_cache
from pantry_chef.models.ingredient import (
    Category,
    IngredientLabel,
    IngredientLabelBatch,
    QuantityLabel,
    QuantityLabelBatch,
)


def make_label(name: str) -> IngredientLabel:
    return IngredientLabel(
        name=name,
        category=Category.DAIRY if name == "butter" else Category.OTHER,
        allergens=[Allergen.MILK] if name == "butter" else [],
        contains_meat=False,
        contains_fish=False,
        animal_product=name == "butter",
    )


class FakeLLM:
    """Labels every requested name, except the ones listed in `skip`."""

    def __init__(self, skip=(), extra=(), fail_on_call=None):
        self.calls: list[list[str]] = []
        self.skip = set(skip)
        self.extra = list(extra)
        self.fail_on_call = fail_on_call

    def generate(self, prompt, schema, **variables):
        names = json.loads(variables["ingredients"])
        self.calls.append(names)
        assert prompt.name == "ingredient_labeling"
        assert schema is IngredientLabelBatch
        if self.fail_on_call == len(self.calls):
            raise TimeoutError("simulated API timeout")
        labels = [make_label(n) for n in names + self.extra if n not in self.skip]
        return IngredientLabelBatch(labels=labels)


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "labels.json"


def test_labels_are_cached(cache_path):
    summary = label_ingredients(["butter", "salt"], FakeLLM(), cache_path)

    assert summary.labeled == 2
    cache = load_cache(cache_path)
    assert cache["butter"].allergens == [Allergen.MILK]
    assert cache["salt"].category == Category.OTHER


def test_rerun_only_sends_uncached_names(cache_path):
    label_ingredients(["butter", "salt"], FakeLLM(), cache_path)
    llm = FakeLLM()

    summary = label_ingredients(["butter", "salt", "eggs"], llm, cache_path)

    assert llm.calls == [["eggs"]]
    assert summary.already_cached == 2


def test_names_are_sent_in_batches_in_order(cache_path):
    llm = FakeLLM()
    label_ingredients(["a", "b", "c", "d", "e"], llm, cache_path, batch_size=2)
    assert llm.calls == [["a", "b"], ["c", "d"], ["e"]]


def test_limit_caps_the_number_of_new_names(cache_path):
    llm = FakeLLM()
    label_ingredients(["a", "b", "c"], llm, cache_path, limit=2)
    assert llm.calls == [["a", "b"]]


def test_skipped_names_are_not_cached_and_retried_next_run(cache_path):
    summary = label_ingredients(["butter", "salt"], FakeLLM(skip={"salt"}), cache_path)
    assert summary.missing == 1
    assert "salt" not in load_cache(cache_path)

    llm = FakeLLM()
    label_ingredients(["butter", "salt"], llm, cache_path)
    assert llm.calls == [["salt"]]


def test_labels_for_names_not_asked_for_are_ignored(cache_path):
    label_ingredients(["butter"], FakeLLM(extra=["invented"]), cache_path)
    assert set(load_cache(cache_path)) == {"butter"}


def test_parallel_workers_label_every_batch(cache_path):
    names = [f"item {i}" for i in range(23)]
    llm = FakeLLM()

    summary = label_ingredients(names, llm, cache_path, batch_size=5, workers=4)

    assert summary.labeled == 23
    assert len(llm.calls) == 5
    assert set(load_cache(cache_path)) == set(names)


def test_summary_counts_cached_names_before_limit(cache_path):
    label_ingredients(["a"], FakeLLM(), cache_path)
    summary = label_ingredients(["a", "b", "c", "d"], FakeLLM(), cache_path, limit=1)
    assert (summary.requested, summary.already_cached, summary.labeled) == (4, 1, 1)


def test_failed_batch_does_not_stop_the_run(cache_path):
    llm = FakeLLM(fail_on_call=1)
    summary = label_ingredients(["a", "b", "c"], llm, cache_path, batch_size=1)

    assert summary.failed_batches == 1
    assert set(load_cache(cache_path)) == {"b", "c"}


class FakeQuantityLLM:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, schema, **variables):
        self.prompts.append(prompt.name)
        assert schema is QuantityLabelBatch
        names = json.loads(variables["ingredients"])
        return QuantityLabelBatch(
            labels=[QuantityLabel(name=n, quantity_matters=n == "eggs") for n in names]
        )


def test_quantity_task_uses_its_own_prompt_schema_and_cache(tmp_path):
    cache_path = tmp_path / "quantity.json"
    llm = FakeQuantityLLM()

    label_ingredients(["eggs", "flour"], llm, cache_path, task=QUANTITY_TASK)

    assert llm.prompts == ["quantity_matters"]
    cache = load_cache(cache_path, QUANTITY_TASK.item_schema)
    assert cache["eggs"].quantity_matters and not cache["flour"].quantity_matters
