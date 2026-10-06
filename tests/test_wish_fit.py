"""Wish-fit check: rules in code, LLM faked; in the search pipeline on the fixture DB."""

import json

import pytest

from pantry_chef.agents.wish_fit import (
    Fit,
    WishFitBatch,
    WishFitChecker,
    WishFitReview,
    least_fitting,
    never_empty,
    outcome_for,
)
from pantry_chef.models.query import NutritionGoal, RecipeQuery
from pantry_chef.models.verification import FailureCode, VerificationStatus
from pantry_chef.search.engine import SearchOptions, find_verified

PANCAKES, WAFFLES, POACHED_EGGS = 5170, 31750, 118761
BAKING = RecipeQuery(
    ingredients=["flour", "butter", "eggs", "milk"],
    preferences_text="dinner",
    nutrition_goals=[NutritionGoal.HIGH_PROTEIN],
)


def fit(recipe_id, verdict, reason="mostly bread and cheese"):
    return WishFitReview(recipe_id=recipe_id, fit=verdict, reason=reason)


class FakeFitLLM:
    def __init__(self, verdicts, skip=(), model_name="mini"):
        self.verdicts = verdicts
        self.skip = set(skip)
        self.model_name = model_name
        self.calls = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "wish_fit" and schema is WishFitBatch
        recipes = json.loads(variables["recipes"])
        self.calls.append({**variables, "recipes": recipes})
        reviews = [
            fit(r["recipe_id"], self.verdicts.get(r["recipe_id"], Fit.FITS))
            for r in recipes
            if r["recipe_id"] not in self.skip
        ]
        return WishFitBatch(reviews=[*reviews, fit(999999, Fit.NO)])  # an id never asked


def candidates(conn, query=BAKING):
    return [vc.candidate for vc in find_verified(conn, query).approved]


# --- the decision rules --------------------------------------------------------------


def test_fits_and_no_verdict_are_kept_silently():
    assert outcome_for(fit(1, Fit.FITS), "x").keep
    assert outcome_for(None, "x").keep and outcome_for(None, "x").note is None  # not safety


def test_partly_is_kept_with_a_note():
    outcome = outcome_for(fit(1, Fit.PARTLY, '"a side dish, not a dinner."'), "x")
    assert outcome.keep and outcome.note == "Partly fits: a side dish, not a dinner."


def test_no_is_removed_as_a_preference_mismatch():
    outcome = outcome_for(fit(1, Fit.NO), "pepperoni pizza")
    assert not outcome.keep
    assert outcome.reason.code is FailureCode.PREFERENCE_MISMATCH
    assert outcome.reason.item == "pepperoni pizza"
    assert "mostly bread and cheese" in outcome.reason.detail


def test_the_least_fitting_of_several_verdicts_wins():
    assert least_fitting([fit(1, Fit.FITS), fit(1, Fit.NO)]).fit is Fit.NO
    assert least_fitting([fit(1, Fit.PARTLY), fit(1, Fit.FITS)]).fit is Fit.PARTLY


def test_a_wish_never_leaves_the_user_with_nothing():
    all_no = {1: outcome_for(fit(1, Fit.NO), "a"), 2: outcome_for(fit(2, Fit.NO, None), "b")}
    kept = never_empty(all_no)
    assert all(o.keep for o in kept.values())
    assert kept[1].note == "May not match what you asked for (mostly bread and cheese)."
    assert kept[2].note == "May not match what you asked for."
    some_fit = {1: outcome_for(fit(1, Fit.NO), "a"), 2: outcome_for(fit(2, Fit.FITS), "b")}
    assert never_empty(some_fit) == some_fit


# --- the checker ---------------------------------------------------------------------


def test_no_wish_and_no_goals_means_no_call(enriched_conn):
    llm = FakeFitLLM({})
    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    outcomes = WishFitChecker(llm, enriched_conn).check(query, candidates(enriched_conn, query))
    assert llm.calls == [] and all(o.keep for o in outcomes.values())


def test_the_checker_sends_the_wish_goals_and_code_facts(enriched_conn):
    llm = FakeFitLLM({})
    WishFitChecker(llm, enriched_conn).check(BAKING, candidates(enriched_conn))
    [call] = llm.calls
    assert call["wish"] == "dinner" and call["goals"] == "high protein"
    pancakes = next(r for r in call["recipes"] if r["recipe_id"] == PANCAKES)
    assert pancakes["high_protein"] is False  # from recipes.is_high_protein, not guessed
    assert pancakes["ingredients"] and "description" in pancakes


def test_the_prompt_is_versioned_and_not_marked_sensitive():
    from pantry_chef.llm.prompt_loader import load_prompt

    prompt = load_prompt("wish_fit")
    assert prompt.version == "2" and not prompt.sensitive  # wishes are not health data


def test_unanswered_recipes_are_kept_and_unknown_ids_ignored(enriched_conn):
    llm = FakeFitLLM({}, skip={PANCAKES})
    outcomes = WishFitChecker(llm, enriched_conn).check(BAKING, candidates(enriched_conn))
    assert outcomes[PANCAKES].keep and 999999 not in outcomes


def test_verdicts_are_cached_per_wish_goals_and_model(enriched_conn, tmp_path):
    cands = candidates(enriched_conn)
    path = tmp_path / "fit.json"
    llm = FakeFitLLM({})
    WishFitChecker(llm, enriched_conn, path).check(BAKING, cands)
    WishFitChecker(llm, enriched_conn, path).check(BAKING, cands)
    assert len(llm.calls) == 1
    other_wish = BAKING.model_copy(update={"preferences_text": "breakfast"})
    WishFitChecker(llm, enriched_conn, path).check(other_wish, cands)
    assert len(llm.calls) == 2
    large = FakeFitLLM({}, model_name="large")
    WishFitChecker(large, enriched_conn, path).check(BAKING, cands)
    assert len(large.calls) == 1  # another model is asked again


# --- in the search pipeline ----------------------------------------------------------


def test_misfits_are_removed_and_recorded_as_failures(enriched_conn):
    checker = WishFitChecker(FakeFitLLM({PANCAKES: Fit.NO}), enriched_conn)
    result = find_verified(
        enriched_conn, BAKING, SearchOptions(use_wish_fit=True), wish_checker=checker
    )
    assert PANCAKES not in [vc.candidate.recipe_id for vc in result.top]
    failed = next(vc for vc in result.checked if vc.candidate.recipe_id == PANCAKES)
    assert failed.verification.status is VerificationStatus.FAIL
    assert [r.code for r in failed.verification.reasons] == [FailureCode.PREFERENCE_MISMATCH]


def test_partly_fitting_recipes_keep_their_note_on_the_card(enriched_conn):
    from pantry_chef.graph.nodes import recipe_option

    llm = FakeFitLLM({WAFFLES: Fit.PARTLY})
    checker = WishFitChecker(llm, enriched_conn)
    result = find_verified(
        enriched_conn, BAKING, SearchOptions(use_wish_fit=True), wish_checker=checker
    )
    waffles = next(vc for vc in result.top if vc.candidate.recipe_id == WAFFLES)
    assert recipe_option(1, waffles).fit_note == "Partly fits: mostly bread and cheese."


def test_wish_fit_option_requires_a_checker(enriched_conn):
    with pytest.raises(ValueError, match="WishFitChecker"):
        find_verified(enriched_conn, BAKING, SearchOptions(use_wish_fit=True))


def test_without_the_option_nothing_is_checked(enriched_conn):
    llm = FakeFitLLM({PANCAKES: Fit.NO})
    result = find_verified(
        enriched_conn, BAKING, SearchOptions(), wish_checker=WishFitChecker(llm, enriched_conn)
    )
    assert llm.calls == [] and PANCAKES in [vc.candidate.recipe_id for vc in result.top]
