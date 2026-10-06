"""The parsing eval: scoring on the profile and query code builds (no real LLM)."""

from pathlib import Path

import pytest

from pantry_chef.agents.finder import RequestAnswer
from pantry_chef.agents.safety import AllergyMention, SafetyAnswer
from pantry_chef.evaluation.parsing_eval import (
    IntakeCase,
    RequestCase,
    cost_usd,
    load_parsing_cases,
    request_query,
    same_item,
    score_intake,
    score_request,
    summarize,
)
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import Diet, NutritionGoal

CASES = Path(__file__).parents[1] / "eval" / "cases" / "parsing.json"


class FakeParsingLLM:
    """Answers the safety intake and request parsing from scripted objects."""

    def __init__(self, intake: dict[str, SafetyAnswer], requests: dict[str, RequestAnswer]):
        self.intake, self.requests = intake, requests

    def generate(self, prompt, schema, **variables):
        if prompt.name == "safety_intake":
            return self.intake[variables["answer"]]
        assert prompt.name == "request_parsing"
        answer = self.requests.get(variables["message"])
        if answer is None:
            raise ValueError("structured output failed: invalid json")
        return answer


def test_the_cases_file_loads_and_uses_only_known_codes():
    intake, requests = load_parsing_cases(CASES)
    assert len(intake) >= 20 and len(requests) >= 25
    assert len({c.id for c in [*intake, *requests]}) == len(intake) + len(requests)
    for case in intake:
        [Allergen(a) for a in case.allergens]  # raises on an unknown code
        assert {Diet(d) for d in case.diets} >= {Diet(d) for d in case.health_diets}
    for case in requests:
        assert all(Allergen(a) for a in case.allergens)
        assert all(NutritionGoal(g) for g in case.goals)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("eggs", "egg", True),
        ("rice", "leftover rice", True),
        ("2 eggs", "eggs", True),
        ("peas", "chickpeas", False),  # whole words only
        ("milk", "coconut milk", True),
        ("tuna", "salmon", False),
    ],
)
def test_same_item(a, b, same):
    assert same_item(a, b) is same


def intake_result(case: IntakeCase, answer: SafetyAnswer):
    from pantry_chef.agents.safety import interpret_safety_answer

    return score_intake(
        case, interpret_safety_answer(FakeParsingLLM({case.answer: answer}, {}), case.answer)
    )


def test_intake_passes_when_the_profile_matches():
    case = IntakeCase(
        id="s", answer="lactose intolerant, vegetarian", allergens=["milk"], diets=["vegetarian"]
    )
    answer = SafetyAnswer(
        allergies=[AllergyMention(said="lactose", allergen=Allergen.MILK)], diets=[Diet.VEGETARIAN]
    )
    assert intake_result(case, answer).passed


def test_a_missed_allergen_is_critical_and_an_extra_one_is_a_mistake():
    case = IntakeCase(id="s", answer="cashews", allergens=["tree_nuts"])
    missed = intake_result(case, SafetyAnswer(allergies=[AllergyMention(said="cashews")]))
    assert missed.allergens_missed == ["tree_nuts"] and not missed.passed
    assert "other allergies extra: cashews" in missed.mistakes

    case = IntakeCase(id="s", answer="peanuts", allergens=["peanuts"])
    extra = intake_result(
        case,
        SafetyAnswer(allergies=[AllergyMention(said="peanuts", allergen=Allergen.TREE_NUTS)]),
    )
    assert extra.allergens_missed == [] and extra.mistakes == ["allergens extra: ['tree_nuts']"]


def test_the_alias_table_adds_codes_the_model_forgot():
    # "shellfish" -> crustaceans + molluscs in code, even if the model gives no group
    case = IntakeCase(id="s", answer="shellfish", allergens=["crustaceans", "molluscs"])
    assert intake_result(case, SafetyAnswer(allergies=[AllergyMention(said="shellfish")])).passed


def test_health_fields_are_compared():
    case = IntakeCase(id="s", answer="gout", health_not_covered=True)
    assert not intake_result(case, SafetyAnswer()).passed
    assert intake_result(case, SafetyAnswer(health_not_covered=True)).passed


def test_request_scoring_checks_every_field():
    case = RequestCase(
        id="r",
        message="m",
        pantry=["eggs", "milk"],
        wish_words=["breakfast"],
        max_minutes=20,
        goals=["high_protein"],
        allergens=["sesame"],
        avoid=["onions"],
        allowed_extra_avoid=["sesame"],
    )
    good = RequestAnswer(
        pantry=["2 eggs", "milk"],
        wish="quick breakfast",
        max_minutes=20,
        goals=[NutritionGoal.HIGH_PROTEIN],
        avoid=["onion", "sesame"],  # copying the allergy into avoid is allowed
        allergies=[AllergyMention(said="sesame", allergen=Allergen.SESAME)],
    )
    assert score_request(case, request_query(good)).passed

    bad = RequestAnswer(pantry=["eggs", "bread"], wish="dinner", avoid=["garlic"])
    result = score_request(case, request_query(bad))
    assert result.allergens_missed == ["sesame"]
    assert set(result.mistakes) == {
        "pantry missing: milk",
        "pantry extra: bread",
        "wish lacks: breakfast",
        "max_minutes: expected 20, got None",
        "goals: expected ['high_protein'], got []",
        "avoid missing: onions",
        "avoid extra: garlic",
    }


def test_the_runner_counts_failed_calls(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_parsing_eval", Path(__file__).parents[1] / "eval" / "run_parsing_eval.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    intake = [IntakeCase(id="s1", answer="none")]
    requests = [
        RequestCase(id="r1", message="eggs", pantry=["eggs"]),
        RequestCase(id="r2", message="unreadable"),
    ]
    llm = FakeParsingLLM({"none": SafetyAnswer()}, {"eggs": RequestAnswer(pantry=["eggs"])})
    results = runner.run_model(llm, intake, requests)
    s = summarize(results)
    assert s["passed"] == pytest.approx(2 / 3) and s["failed_calls"] == 1
    assert s["intake_passed"] == 1.0 and s["request_passed"] == 0.5
    assert s["allergens_missed"] == 0


def test_cost_uses_the_price_table():
    assert cost_usd("gpt-5.4-nano", 1_000_000, 1_000_000) == pytest.approx(1.45)
    assert cost_usd("some-other-model", 10, 10) is None
