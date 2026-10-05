import json

import pytest

from pantry_chef.agents.allergy_review import (
    AllergyReview,
    AllergyReviewBatch,
    AllergyReviewer,
    Verdict,
    keyword_hints,
    outcome_for,
)
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.verification import FailureCode, VerificationStatus
from pantry_chef.search.engine import SearchOptions, find_verified

QUICK_MIX = 81  # "all purpose quick mix with 28 variations": steps mention peanut
PEANUTS = RecipeQuery(
    ingredients=["flour", "whole wheat flour", "butter"], required_allergen_free=[Allergen.PEANUTS]
)


def review(recipe_id, verdict, allergen="peanuts", evidence="add chopped peanuts"):
    return AllergyReview(recipe_id=recipe_id, verdict=verdict, allergen=allergen, evidence=evidence)


# --- the decision rules --------------------------------------------------------------


def test_unsafe_is_removed_with_the_evidence():
    outcome = outcome_for(review(1, Verdict.UNSAFE), "x")
    assert not outcome.keep
    assert outcome.reason.code is FailureCode.ALLERGEN
    assert '"add chopped peanuts"' in outcome.reason.detail


def test_optional_is_kept_with_a_leave_it_out_warning():
    outcome = outcome_for(review(1, Verdict.OPTIONAL, evidence="variation: peanut chips"), "x")
    assert outcome.keep
    assert outcome.warning == 'Leave out the peanuts: "variation: peanut chips".'


def test_uncertain_is_kept_with_a_check_the_label_warning():
    outcome = outcome_for(review(1, Verdict.UNCERTAIN, "tree_nuts", "store-bought granola"), "x")
    assert outcome.keep and outcome.warning.startswith("Check the label for tree_nuts")


def test_safe_is_kept_without_warning():
    assert (
        outcome_for(review(1, Verdict.SAFE, None, None), "x")
        == outcome_for(review(1, Verdict.SAFE, None, None), "x")
        and outcome_for(review(1, Verdict.SAFE, None, None), "x").warning is None
    )


def test_no_verdict_means_removed():
    outcome = outcome_for(None, "quick mix")
    assert not outcome.keep and "no answer" in outcome.reason.detail


# --- keyword scan (code) -------------------------------------------------------------


def test_keyword_hints_find_eu_allergens_and_other_words_in_steps():
    query = RecipeQuery(
        ingredients=[], required_allergen_free=[Allergen.PEANUTS], other_allergies=["kiwi"]
    )
    hints = keyword_hints(
        ["mix the flour", "suggested condiments include peanuts", "serve with sliced kiwi fruit"],
        query,
    )
    assert hints == [
        'peanuts: "suggested condiments include peanuts"',
        'kiwi: "serve with sliced kiwi fruit"',
    ]


# --- the reviewer with a fake LLM ----------------------------------------------------


class FakeReviewLLM:
    def __init__(self, verdicts, skip=()):
        self.verdicts = verdicts  # recipe_id -> Verdict
        self.skip = set(skip)
        self.calls = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "allergy_review" and prompt.sensitive
        assert schema is AllergyReviewBatch
        recipes = json.loads(variables["recipes"])
        self.calls.append(
            {"recipes": recipes, **{k: v for k, v in variables.items() if k != "recipes"}}
        )
        reviews = [
            review(r["recipe_id"], self.verdicts.get(r["recipe_id"], Verdict.SAFE))
            for r in recipes
            if r["recipe_id"] not in self.skip
        ]
        reviews.append(review(999999, Verdict.SAFE))  # an id that was never asked about
        return AllergyReviewBatch(reviews=reviews)


def candidates(conn, query):
    return [vc.candidate for vc in find_verified(conn, query).approved]


def test_no_allergies_means_no_review_call(enriched_conn):
    llm = FakeReviewLLM({})
    query = RecipeQuery(ingredients=["flour", "butter"])
    outcomes = AllergyReviewer(llm, enriched_conn).review(query, candidates(enriched_conn, query))
    assert llm.calls == [] and all(o.keep for o in outcomes.values())


def test_reviewer_sends_steps_and_keyword_hints(enriched_conn):
    llm = FakeReviewLLM({})
    cands = candidates(enriched_conn, PEANUTS)
    AllergyReviewer(llm, enriched_conn).review(PEANUTS, cands)
    [call] = llm.calls
    assert call["allergen_codes"] == "peanuts" and call["other_allergies"] == "none"
    quick_mix = next(r for r in call["recipes"] if r["recipe_id"] == QUICK_MIX)
    assert quick_mix["steps"] and any(h.startswith("peanuts:") for h in quick_mix["keyword_hints"])


def test_unanswered_recipe_is_removed_and_ids_not_asked_are_ignored(enriched_conn):
    cands = candidates(enriched_conn, PEANUTS)
    outcomes = AllergyReviewer(FakeReviewLLM({}, skip={QUICK_MIX}), enriched_conn).review(
        PEANUTS, cands
    )
    assert not outcomes[QUICK_MIX].keep
    assert 999999 not in outcomes


def test_reviews_are_cached_per_recipe_and_allergy_set(enriched_conn, tmp_path):
    cands = candidates(enriched_conn, PEANUTS)
    llm = FakeReviewLLM({})
    AllergyReviewer(llm, enriched_conn, tmp_path / "r.json").review(PEANUTS, cands)
    AllergyReviewer(llm, enriched_conn, tmp_path / "r.json").review(PEANUTS, cands)
    assert len(llm.calls) == 1
    other = PEANUTS.model_copy(update={"other_allergies": ["kiwi"]})
    AllergyReviewer(llm, enriched_conn, tmp_path / "r.json").review(other, cands)
    assert len(llm.calls) == 2  # a different allergy set is a different question


# --- in the search pipeline ----------------------------------------------------------


def test_unsafe_recipe_is_removed_and_recorded_as_a_failure(enriched_conn):
    reviewer = AllergyReviewer(FakeReviewLLM({QUICK_MIX: Verdict.UNSAFE}), enriched_conn)
    result = find_verified(enriched_conn, PEANUTS, SearchOptions(), allergy_reviewer=reviewer)
    assert QUICK_MIX not in [vc.candidate.recipe_id for vc in result.top]
    checked = next(vc for vc in result.checked if vc.candidate.recipe_id == QUICK_MIX)
    assert checked.verification.status is VerificationStatus.FAIL
    assert checked.verification.checks[-1].check == "allergy_review"


def test_optional_recipe_is_shown_with_its_warning(enriched_conn):
    reviewer = AllergyReviewer(FakeReviewLLM({QUICK_MIX: Verdict.OPTIONAL}), enriched_conn)
    result = find_verified(enriched_conn, PEANUTS, SearchOptions(), allergy_reviewer=reviewer)
    shown = next(vc for vc in result.top if vc.candidate.recipe_id == QUICK_MIX)
    assert shown.verification.warnings == ['Leave out the peanuts: "add chopped peanuts".']


def test_without_a_reviewer_steps_mentions_become_warnings(enriched_conn):
    result = find_verified(enriched_conn, PEANUTS, SearchOptions())
    shown = next(vc for vc in result.top if vc.candidate.recipe_id == QUICK_MIX)
    assert shown.verification.warnings and "peanut" in shown.verification.warnings[0]


def test_recipe_card_carries_the_warnings(enriched_conn):
    from pantry_chef.graph.nodes import recipe_option

    reviewer = AllergyReviewer(FakeReviewLLM({QUICK_MIX: Verdict.OPTIONAL}), enriched_conn)
    result = find_verified(enriched_conn, PEANUTS, SearchOptions(), allergy_reviewer=reviewer)
    shown = next(vc for vc in result.top if vc.candidate.recipe_id == QUICK_MIX)
    assert recipe_option(1, shown).warnings == shown.verification.warnings


@pytest.mark.parametrize("verdict", list(Verdict))
def test_verdicts_never_remove_recipes_without_allergies(enriched_conn, verdict):
    query = PEANUTS.model_copy(update={"required_allergen_free": []})
    llm = FakeReviewLLM({QUICK_MIX: verdict})
    result = find_verified(
        enriched_conn, query, SearchOptions(), allergy_reviewer=AllergyReviewer(llm, enriched_conn)
    )
    assert QUICK_MIX in [vc.candidate.recipe_id for vc in result.top]
    assert llm.calls == []


def test_quotes_from_the_model_are_not_doubled():
    outcome = outcome_for(review(1, Verdict.OPTIONAL, evidence='"top with almonds"'), "x")
    assert outcome.warning == 'Leave out the peanuts: "top with almonds".'


# --- several answers for one recipe, and the cache key ---------------------------------


class ScriptedReviewLLM:
    """Returns the given reviews for every batch."""

    def __init__(self, reviews, model_name="model-a"):
        self.reviews = reviews
        self.model_name = model_name
        self.calls = 0

    def generate(self, prompt, schema, **variables):
        self.calls += 1
        return AllergyReviewBatch(reviews=self.reviews)


@pytest.mark.parametrize("order", [[Verdict.UNSAFE, Verdict.SAFE], [Verdict.SAFE, Verdict.UNSAFE]])
def test_the_most_serious_of_several_verdicts_wins(enriched_conn, order):
    # e.g. one entry per allergen: required peanuts, no sesame. Order must not matter.
    cands = [c for c in candidates(enriched_conn, PEANUTS) if c.recipe_id == QUICK_MIX]
    llm = ScriptedReviewLLM([review(QUICK_MIX, verdict) for verdict in order])
    outcome = AllergyReviewer(llm, enriched_conn).review(PEANUTS, cands)[QUICK_MIX]
    assert not outcome.keep


def test_severity_order():
    from pantry_chef.agents.allergy_review import most_serious

    reviews = [review(1, v) for v in (Verdict.SAFE, Verdict.OPTIONAL, Verdict.UNCERTAIN)]
    assert most_serious(reviews).verdict is Verdict.UNCERTAIN
    assert most_serious([*reviews, review(1, Verdict.UNSAFE)]).verdict is Verdict.UNSAFE


def test_cached_verdicts_belong_to_the_model_that_gave_them(enriched_conn, tmp_path):
    cands = [c for c in candidates(enriched_conn, PEANUTS) if c.recipe_id == QUICK_MIX]
    mini = ScriptedReviewLLM([review(QUICK_MIX, Verdict.SAFE)], model_name="mini")
    AllergyReviewer(mini, enriched_conn, tmp_path / "r.json").review(PEANUTS, cands)
    large = ScriptedReviewLLM([review(QUICK_MIX, Verdict.UNSAFE)], model_name="large")
    outcome = AllergyReviewer(large, enriched_conn, tmp_path / "r.json").review(PEANUTS, cands)
    assert large.calls == 1  # switching ALLERGY_REVIEW_MODEL asks again
    assert not outcome[QUICK_MIX].keep


def test_prompt_asks_for_the_most_serious_verdict():
    from pantry_chef.llm.prompt_loader import load_prompt

    text = load_prompt("allergy_review").template
    assert "most serious" in text
