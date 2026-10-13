"""Approximate ingredient weights: conversions, the seed file and their eval."""

import pytest

from pantry_chef.evaluation.weights_eval import accuracy, coverage, weight_checks
from pantry_chef.graph.nodes import ask_unit
from pantry_chef.graph.state import ChatState
from pantry_chef.ingredients.quantities import (
    Amount,
    Weights,
    approximate_ratio,
    to_grams,
    unit_kind,
)
from pantry_chef.ingredients.weights import load_weights, read_seed, save_seed
from pantry_chef.models.chat import AmountUnit
from pantry_chef.models.ingredient import WeightLabel
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import (
    VerificationResult,
    VerificationStatus,
    VerifiedCandidate,
)

BREAST = Weights(piece_g=200, cup_g=140)


@pytest.mark.parametrize(
    ("unit", "kind"),
    [(None, "count"), ("large", "count"), ("lb", "weight"), ("g", "weight"), ("cups", "volume")],
)
def test_unit_kind(unit, kind):
    assert unit_kind(unit) == kind


@pytest.mark.parametrize("unit", ["can", "bunch", "package"])
def test_unit_kind_of_units_we_cannot_read(unit):
    assert unit_kind(unit) is None


@pytest.mark.parametrize(
    ("amount", "grams"),
    [
        (Amount(2, None), 400),  # 2 breasts
        (Amount(1, "lb"), 453.6),
        (Amount(300, "g"), 300),
        (Amount(0.5, "cup"), 70),
    ],
)
def test_to_grams(amount, grams):
    assert to_grams(amount, BREAST) == pytest.approx(grams, abs=0.1)


def test_to_grams_without_the_weight_it_needs():
    assert to_grams(Amount(2, None), Weights(cup_g=140)) is None
    assert to_grams(Amount(1, "cup"), Weights(piece_g=200)) is None
    assert to_grams(Amount(1, "can"), BREAST) is None


def test_grams_against_a_number_of_items():
    """300 g of chicken for a recipe with 2 breasts (about 400 g)."""
    assert approximate_ratio(Amount(300, "g"), Amount(2, None), BREAST) == pytest.approx(0.75)


def test_a_number_of_items_against_cups():
    """1 breast (200 g) for 2 cups chopped (280 g)."""
    assert approximate_ratio(Amount(1, None), Amount(2, "cup"), BREAST) == pytest.approx(200 / 280)


def test_the_exact_comparison_wins_when_units_are_alike():
    """Counts against counts never go through the (approximate) weights."""
    odd = Weights(piece_g=1, cup_g=1)
    assert approximate_ratio(Amount(3, None), Amount(4, "large"), odd) == pytest.approx(0.75)
    assert approximate_ratio(Amount(250, "g"), Amount(1, "lb"), odd) == pytest.approx(0.551, 0.01)


def test_without_weights_nothing_is_guessed():
    assert approximate_ratio(Amount(300, "g"), Amount(2, None), None) is None
    assert approximate_ratio(Amount(300, "g"), Amount(2, None), Weights()) is None


def test_a_recipe_amount_of_zero_is_not_compared():
    assert approximate_ratio(Amount(300, "g"), Amount(0, None), BREAST) is None


def test_seed_round_trip_drops_empty_labels(tmp_path):
    path = tmp_path / "weights.json"
    save_seed(
        {
            "egg": WeightLabel(name="egg", piece_g=50),
            "salt": WeightLabel(name="salt"),
            "cheddar cheese": WeightLabel(name="cheddar cheese", cup_g=113),
        },
        path,
    )
    assert read_seed(path) == {"cheddar cheese": Weights(None, 113), "egg": Weights(50, None)}
    assert path.read_text().count("\n") == 4  # one ingredient per line


def test_the_seed_in_the_repository_loads():
    weights = load_weights()
    assert len(weights) > 1000
    assert 40 <= (weights["egg"].piece_g or 0) <= 60
    assert weights["egg"].cup_g is None


# --- which unit the quantity question asks for --------------------------------------


def option(recipe_id: int, unit: str | None, canonical: str = "chicken breast"):
    line = RecipeIngredient(
        name=canonical, canonical_name=canonical, category="protein", is_key=True,
        quantity=2, unit=unit,
    )  # fmt: skip
    candidate = Candidate(
        recipe_id=recipe_id, name=f"r{recipe_id}", minutes=10, ingredients=[line], have_key=1,
        total_key=1, coverage=1, ingredient_score=1, final_score=1,
    )  # fmt: skip
    verification = VerificationResult(
        candidate_id=recipe_id, status=VerificationStatus.PASS, checks=[]
    )
    return VerifiedCandidate(candidate=candidate, verification=verification)


@pytest.mark.parametrize(
    ("units", "asked"),
    [
        ([None, None, "lb"], AmountUnit.COUNT),  # mostly "2 chicken breasts"
        ([None, "lb", "cups"], AmountUnit.GRAMS),  # mostly measured
        ([None, "lb"], AmountUnit.GRAMS),  # a tie: grams
        (["can", None], AmountUnit.COUNT),  # unreadable units do not vote
    ],
)
def test_ask_unit_follows_how_the_options_measure_it(units, asked):
    state = ChatState(results=[option(n, unit) for n, unit in enumerate(units, start=1)])
    assert ask_unit(state, "chicken breast") is asked


def test_ask_unit_without_amounts_in_the_options_uses_the_weights():
    assert ask_unit(ChatState(), "egg") is AmountUnit.COUNT  # only ever counted
    assert ask_unit(ChatState(), "cheddar cheese") is AmountUnit.GRAMS
    assert ask_unit(ChatState(), "not an ingredient") is AmountUnit.GRAMS


# --- eval -----------------------------------------------------------------------------


def test_accuracy_against_the_reference():
    reference = {"piece_g": {"egg": 50, "apple": 182}, "cup_g": {"feta cheese": 150}}
    weights = {"egg": Weights(55, None), "apple": Weights(100, None)}
    checks = weight_checks(reference, weights)
    result = accuracy(checks)
    assert (result.checked, result.missing, result.close) == (2, 1, 1)
    assert result.median_error == pytest.approx((0.1 + 82 / 182) / 2)


def test_coverage_before_and_after_the_weights():
    rows = [
        {"name": "chicken breast", "quantity": 2, "unit": None, "n": 6},
        {"name": "chicken breast", "quantity": 1, "unit": "lb", "n": 3},
        {"name": "chicken breast", "quantity": 1, "unit": "can", "n": 1},
    ]
    result = coverage(rows, {"chicken breast": BREAST})
    assert result.lines == 10
    assert result.before == {"grams": 3, "number": 6}
    assert result.after == {"grams": 9, "number": 9}
