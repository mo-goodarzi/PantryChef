"""End-to-end eval: the simulated user drives the real graph on the fixture database
(scripted LLM), and code checks what was shown against the case's truth."""

import json
from pathlib import Path

import pytest

from pantry_chef.agents.finder import RequestAnswer
from pantry_chef.agents.safety import AllergyMention, SafetyAnswer
from pantry_chef.evaluation import e2e
from pantry_chef.evaluation.e2e import (
    E2ECase,
    E2EResult,
    SimulatedUser,
    TrueAmount,
    cap_reached,
    failure_codes,
    judged_case,
    load_e2e_cases,
    missing_key,
    run_case,
    summarize,
    too_little,
    write_report,
)
from pantry_chef.evaluation.judge import CachedJudge, Judgment, JudgmentBatch
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.llm import usage
from pantry_chef.models.chat import (
    AmountReply,
    ChoiceReply,
    ConfirmReply,
    Question,
    QuestionKind,
    SafetyReply,
)
from pantry_chef.models.query import AmountStatus
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from tests.test_graph import BAKING, ScriptedLLM, conversation, make_deps

MESSAGE = "I have flour, butter, eggs and milk, something for breakfast"
PANTRY = ["flour", "butter", "eggs", "milk"]


def case(**fields) -> E2ECase:
    return E2ECase(**{"id": "t1", "group": "test", "message": MESSAGE, "pantry": PANTRY} | fields)


def run(enriched_conn, state_conn, llm, test_case):
    chat = conversation(make_deps(enriched_conn, state_conn, llm), user_id=None)
    return run_case(chat, enriched_conn, test_case)


# --- the simulated user ----------------------------------------------------------------


def test_simulated_user_answers_each_question_the_same_way():
    user = SimulatedUser(case(safety_answer="peanuts", amounts={"egg": TrueAmount(quantity=2)}))
    assert user.answer(Question(kind=QuestionKind.SAFETY, text="?")) == SafetyReply(text="peanuts")
    # confirms without reading: the worst case for safety, so intake mistakes count
    assert user.answer(Question(kind=QuestionKind.SAFETY_CONFIRM, text="?")) == ConfirmReply(
        correct=True, consent_to_store=False
    )
    assert user.answer(Question(kind=QuestionKind.CHOICE, text="?")) == ChoiceReply(choice=1)
    reply = user.answer(Question(kind=QuestionKind.QUANTITIES, text="?", items=["egg", "milk"]))
    assert reply.amounts["egg"] == AmountReply(quantity=2, status=AmountStatus.KNOWN)
    assert reply.amounts["milk"].status is AmountStatus.UNKNOWN  # not in the hidden amounts


# --- "can they make it": code only, generous ---------------------------------------------


def recipe(*keys: str, staple: str = "salt") -> Candidate:
    ingredients = [
        RecipeIngredient(name=k, canonical_name=k, category="x", is_key=True) for k in keys
    ]
    ingredients.append(
        RecipeIngredient(
            name=staple, canonical_name=staple, category="x", is_key=True, is_staple=True
        )
    )
    return Candidate(
        recipe_id=1,
        name="r",
        minutes=10,
        ingredients=ingredients,
        have_key=0,
        total_key=0,
        coverage=0,
        ingredient_score=0,
        final_score=0,
    )


@pytest.mark.parametrize(
    ("pantry", "also_ok", "keys", "missing"),
    [
        # the two false failures of the first real run
        (["garlic", "parmesan cheese"], [], ["garlic clove", "parmesan cheese"], []),
        (["cheese", "tortilla"], [], ["cheddar cheese", "corn tortilla"], []),
        (["canned tomato"], [], ["tomato"], []),  # either way round
        (["eggs"], [], ["egg"], []),  # plural
        # a different word is not covered without also_ok ...
        (["pasta"], [], ["spaghetti"], ["spaghetti"]),
        (["pasta"], ["spaghetti"], ["spaghetti"], []),  # ... and is with it
        # whole words only: "pea" is not "peanut"; staples are always there
        (["pea"], [], ["peanut"], ["peanut"]),
    ],
)
def test_missing_key_uses_whole_words_and_also_ok(pantry, also_ok, keys, missing):
    assert missing_key(recipe(*keys), case(pantry=pantry, also_ok=also_ok)) == missing


def with_amount(candidate: Candidate, name: str, quantity: float, unit: str | None) -> Candidate:
    ingredients = [
        i.model_copy(update={"quantity": quantity, "unit": unit}) if i.name == name else i
        for i in candidate.ingredients
    ]
    return candidate.model_copy(update={"ingredients": ingredients})


@pytest.mark.parametrize(
    ("have", "needs", "short"),
    [
        (TrueAmount(quantity=1), (2, "eggs"), False),  # half: the app scales the recipe down
        (TrueAmount(quantity=1), (6, None), True),  # 1 of 6 eggs
        (TrueAmount(quantity=6), (6, "eggs"), False),
        (TrueAmount(quantity=100, unit="g"), (1, "pound"), True),  # 100 g of 454 g
        (TrueAmount(quantity=300, unit="g"), (1, "pound"), False),
        (TrueAmount(quantity=200, unit="g"), (2, "cup"), False),  # g vs cups: not checked
    ],
)
def test_too_little_compares_true_amounts_with_the_recipe(have, needs, short):
    candidate = with_amount(recipe("eggs"), "eggs", *needs)
    failures = too_little(candidate, case(amounts={"egg": have}))
    assert bool(failures) is short
    if short:
        assert failures[0].startswith("insufficient_quantity: eggs (have ")


def test_too_little_skips_unknown_amounts_and_staples():
    candidate = with_amount(recipe("eggs", staple="salt"), "salt", 1, "cup")
    assert too_little(candidate, case(amounts={"egg": TrueAmount(quantity=1)})) == []
    assert too_little(with_amount(recipe("eggs"), "eggs", 6, None), case()) == []


def test_simulated_user_finds_the_amount_by_item_name():
    user = SimulatedUser(case(amounts={"eggs": TrueAmount(quantity=3)}))
    assert user.amount("egg").quantity == 3
    assert user.amount("milk").status is AmountStatus.UNKNOWN


# --- whole conversations ----------------------------------------------------------------


def test_a_clean_conversation_ends_with_a_recipe_that_fits_the_truth(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    result = run(enriched_conn, state_conn, llm, case())
    assert result.error is None
    assert result.recipe_id is not None and result.failures == []
    assert result.success and not result.safety_violation
    assert result.questions == {"safety": 1, "safety_confirm": 1, "choice": 1}
    assert result.searches == 1


def test_an_intake_mistake_is_caught_against_the_truth(enriched_conn, state_conn):
    # The user said "eggs", but the (scripted) intake read nothing: the pipeline searches
    # without the allergen, and only the check against the truth sees it.
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    result = run(
        enriched_conn, state_conn, llm, case(safety_answer="eggs", allergens=[Allergen.EGGS])
    )
    assert result.safety_violation and not result.success
    assert any("allergen: egg" in f for f in result.unsafe_shown)
    assert failure_codes([result])["allergen"] >= 1


def test_a_correct_intake_shows_nothing_unsafe(enriched_conn, state_conn):
    llm = ScriptedLLM(
        safety_intake=SafetyAnswer(allergies=[AllergyMention(said="egg")]),
        request_parsing=BAKING,
    )
    result = run(
        enriched_conn, state_conn, llm, case(safety_answer="eggs", allergens=[Allergen.EGGS])
    )
    assert result.unsafe_shown == [] and not result.safety_violation


def test_no_recipe_is_a_success_when_none_is_expected(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=RequestAnswer())
    result = run(enriched_conn, state_conn, llm, case(message="hello", expect_recipe=False))
    assert result.recipe_id is None and result.reply and result.success


def test_an_answer_on_the_last_allowed_turn_counts(monkeypatch, enriched_conn, state_conn):
    # the clean conversation needs 3 replies: safety, confirm, choice
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    monkeypatch.setattr(e2e, "MAX_TURNS", 3)
    assert run(enriched_conn, state_conn, llm, case()).success
    monkeypatch.setattr(e2e, "MAX_TURNS", 2)
    stuck = run(enriched_conn, state_conn, llm, case())
    assert stuck.error == "RuntimeError: no answer after 2 turns"


class UnpricedLLM(ScriptedLLM):
    def generate(self, prompt, schema, **variables):
        usage.record("unpriced-model", 10, 10)
        return super().generate(prompt, schema, **variables)


def test_a_model_without_a_price_does_not_lose_the_case(enriched_conn, state_conn, tmp_path):
    llm = UnpricedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    result = run(enriched_conn, state_conn, llm, case())
    assert result.success and result.error is None
    assert result.cost_usd == 0.0 and result.unpriced_models == ["unpriced-model"]
    meta = {"timestamp": "t", "cases_file": "c.json", "models": "m", "stopped": None}
    md, _ = write_report({"chat": [result]}, meta, tmp_path)
    assert "Costs leave out** models without a price: unpriced-model" in md.read_text()


def test_an_error_is_recorded_not_raised(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer())  # no answer for request parsing
    result = run(enriched_conn, state_conn, llm, case())
    assert result.error and "KeyError" in result.error and not result.success


# --- summary and cost -------------------------------------------------------------------


def test_summary_counts_success_safety_and_retries():
    ok = E2EResult("a", "g", recipe_id=1, searches=1, questions={"choice": 1})
    unsafe = E2EResult("b", "g", recipe_id=2, failures=["allergen: egg"], searches=3)
    missing = E2EResult("c", "g", searches=3)  # no recipe although one was expected
    broken = E2EResult("d", "g", error="TimeoutError: x")
    s = summarize([ok, unsafe, missing, broken])
    assert s["task_success"] == 0.25
    assert s["safety_violations"] == 1
    assert s["rule_violation_rate"] == 0.5  # of the 2 recipes given, 1 broke a rule
    assert s["no_recipe"] == 1 and s["errors"] == 1
    assert s["retries_per_request"] == pytest.approx(4 / 3)  # errors are not averaged in
    assert failure_codes([ok, unsafe, missing, broken]) == {
        "allergen": 1,
        "no_recipe": 1,
        "error": 1,
    }


def test_usage_meter_prices_each_model():
    before = usage.snapshot()
    usage.record("gpt-5.4-mini", 1_000_000, 0)
    usage.record("gpt-5.4-nano", 0, 1_000_000)
    spent = usage.since(before)
    assert spent["gpt-5.4-mini"].calls == 1
    assert usage.total_cost(spent) == pytest.approx(0.75 + 1.25)


def test_a_model_without_a_price_stops_the_cost_count():
    # a missing price must never let a run slip past its spending cap
    with pytest.raises(ValueError, match="no price"):
        usage.total_cost({"mystery-model": usage.ModelUsage(1, 10, 10)})


def test_the_spending_cap_stops_the_run_and_needs_every_price():
    mini = {"gpt-5.4-mini": usage.ModelUsage(1, 1_000_000, 0)}  # $0.75
    unpriced = {"unpriced-model": usage.ModelUsage(1, 10, 10)}
    assert cap_reached(None, unpriced, "chat", 3) is None  # no cap: nothing to price
    assert cap_reached(1.0, mini, "chat", 3) is None
    assert cap_reached(0.5, mini, "chat", 3) == "spending cap $0.50 reached in chat after 3 cases"
    stopped = cap_reached(1.0, unpriced, "chat", 3)
    assert stopped and "cannot be checked" in stopped and "unpriced-model" in stopped


@pytest.mark.parametrize("name", ["e2e.json", "safety.json", "quantities.json"])
def test_case_files_load(name):
    cases = load_e2e_cases(Path(__file__).parents[1] / "eval" / "cases" / name)
    assert cases and all(c.pantry or not c.expect_recipe for c in cases)


class OneScoreJudgeLLM:
    model_name = "judge-model"

    def __init__(self, score):
        self.score = score
        self.wishes = []

    def generate(self, prompt, schema, **variables):
        self.wishes.append(variables["wish"])
        ids = [r["recipe_id"] for r in json.loads(variables["recipes"])]
        return JudgmentBatch(
            judgments=[Judgment(recipe_id=i, score=self.score, reason="r") for i in ids]
        )


def test_the_judge_rates_the_final_recipe_against_the_users_message(
    enriched_conn, state_conn, tmp_path
):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm), user_id=None)
    judge_llm = OneScoreJudgeLLM(3)
    judge = CachedJudge(judge_llm, tmp_path / "judgments.json")
    result = run_case(chat, enriched_conn, case(), judge)
    assert result.success and result.judge_score == 3
    assert judge_llm.wishes == [MESSAGE]  # the user's own words are the wish
    s = summarize([result])
    assert s["mean_judge_score"] == 3 and s["good_answer_rate"] == 0.0  # safe, weak fit
    assert judged_case(case()).judged_wish == MESSAGE
    assert judged_case(case(wish="breakfast")).judged_wish == "breakfast"


def test_report_has_one_row_per_variant(tmp_path):
    good = E2EResult("a", "g", recipe_id=1, searches=1, judge_score=5)
    weak = E2EResult("a", "g", recipe_id=2, searches=1, judge_score=2, judge_reason="a sauce")
    meta = {"timestamp": "t", "cases_file": "c.json", "models": "m", "stopped": None}
    md, _ = write_report({"chat": [good], "coverage-only": [weak]}, meta, tmp_path)
    text = md.read_text()
    assert "| chat | 100% | 100% | 5.00 |" in text
    assert "| coverage-only | 100% | 0% | 2.00 |" in text
    assert "a weak fit" in text and "a sauce" in text
