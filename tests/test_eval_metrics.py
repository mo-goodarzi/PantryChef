import json

import pytest

from pantry_chef.evaluation.cases import SearchCase, load_cases
from pantry_chef.evaluation.metrics import (
    hard_rule_failures,
    hit_at_k,
    percentile,
    reciprocal_rank,
)
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient


@pytest.mark.parametrize(
    ("relevant", "hit", "rr"),
    [
        ([True, False, False], 1.0, 1.0),
        ([False, False, True], 1.0, 1 / 3),
        ([False] * 5 + [True], 0.0, 0.0),  # beyond k=5
        ([], 0.0, 0.0),
    ],
)
def test_hit_and_reciprocal_rank(relevant, hit, rr):
    assert hit_at_k(relevant, 5) == hit
    assert reciprocal_rank(relevant, 5) == pytest.approx(rr)


def test_percentile():
    values = [0.1, 0.2, 0.3, 0.4, 1.0]
    assert percentile(values, 50) == 0.3
    assert percentile(values, 95) == 1.0
    assert percentile([2.0], 50) == 2.0


def candidate(minutes=10, *ingredients):
    return Candidate(
        recipe_id=1,
        name="x",
        minutes=minutes,
        ingredients=list(ingredients),
        have_key=0,
        total_key=0,
        coverage=0,
        ingredient_score=0,
        final_score=0,
    )


def ing(name, key=True, allergens=()):
    return RecipeIngredient(
        name=name, canonical_name=name, category=None, is_key=key, allergens=list(allergens)
    )


def test_one_missing_key_ingredient_is_allowed_two_are_not():
    query = RecipeQuery(ingredients=["egg"])
    assert hard_rule_failures(candidate(10, ing("egg"), ing("flour")), query) == []
    failures = hard_rule_failures(candidate(10, ing("egg"), ing("flour"), ing("milk")), query)
    assert failures == ["missing 2 key ingredients"]


def test_hard_rules_catch_allergens_and_time():
    query = RecipeQuery(
        ingredients=["nut"], required_allergen_free=[Allergen.TREE_NUTS], max_minutes=20
    )
    failures = hard_rule_failures(candidate(45, ing("nut", allergens=[Allergen.TREE_NUTS])), query)
    assert "allergen: nut" in failures
    assert "too_long: 45 min" in failures


def test_case_to_query_maps_allergy_words(tmp_path):
    case = SearchCase(
        id="x",
        group="g",
        pantry=["shrimp"],
        preferences="dinner",
        allergies=["shellfish"],
        diets=["vegetarian"],
    )
    query = case.to_query()
    assert {a.value for a in query.required_allergen_free} == {"crustaceans", "molluscs"}
    assert query.preferences_text == "dinner"


def test_load_cases_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "cases.json"
    item = {"id": "a", "group": "g", "pantry": [], "preferences": "p"}
    path.write_text(json.dumps([item, item]))
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)


def test_repository_eval_cases_are_valid():
    from pathlib import Path

    cases = load_cases(Path(__file__).parents[1] / "eval" / "cases" / "search.json")
    assert len(cases) == 52
    for case in cases:
        case.to_query()  # allergy words must all be known


def test_intra_list_similarity():
    from pantry_chef.evaluation.metrics import intra_list_similarity

    assert intra_list_similarity([[1.0, 0.0], [1.0, 0.0]]) == 1.0
    assert intra_list_similarity([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]) == pytest.approx(1 / 3)
    assert intra_list_similarity([[1.0, 0.0]]) is None
