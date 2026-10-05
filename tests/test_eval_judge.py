import json

import pytest

from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.judge import CachedJudge, Judgment, JudgmentBatch, recipe_summaries
from pantry_chef.evaluation.search_eval import evaluate_variant, summarize
from pantry_chef.models.query import NutritionGoal
from pantry_chef.search.engine import SearchOptions, find_recipes

PANCAKES, WAFFLES = 5170, 31750
CASE = SearchCase(
    id="b", group="breakfast", pantry=["flour", "butter", "egg", "milk"], preferences="pancakes"
)


class FakeJudgeLLM:
    def __init__(self):
        self.calls = []
        self.wishes = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "preference_judge" and schema is JudgmentBatch
        recipes = json.loads(variables["recipes"])
        self.calls.append([r["recipe_id"] for r in recipes])
        self.wishes.append(variables["wish"])
        return JudgmentBatch(
            judgments=[
                Judgment(
                    recipe_id=r["recipe_id"],
                    score=5 if "pancake" in r["name"] else 2,
                    reason="test",
                )
                for r in recipes
            ]
        )


def test_recipe_summaries_skip_generic_tags(enriched_conn):
    summary = recipe_summaries(enriched_conn, [PANCAKES])[PANCAKES]
    assert summary["name"] == "pete s scratch pancakes"
    assert "time-to-make" not in summary["tags"]
    assert "eggs" in summary["ingredients"]


def test_judge_caches_and_only_sends_new_recipes(enriched_conn, tmp_path):
    llm = FakeJudgeLLM()
    judge = CachedJudge(llm, tmp_path / "j.json")
    first = judge.judge(enriched_conn, CASE, [PANCAKES, WAFFLES])
    assert first[PANCAKES].score == 5 and first[WAFFLES].score == 2

    judge2 = CachedJudge(llm, tmp_path / "j.json")  # reloads cache from disk
    judge2.judge(enriched_conn, CASE, [PANCAKES, WAFFLES, 118761])
    assert llm.calls == [[PANCAKES, WAFFLES], [118761]]


def test_changing_the_case_wish_invalidates_cached_judgments(enriched_conn, tmp_path):
    llm = FakeJudgeLLM()
    judge = CachedJudge(llm, tmp_path / "j.json")
    judge.judge(enriched_conn, CASE, [PANCAKES])
    judge.judge(enriched_conn, CASE.model_copy(update={"preferences": "waffles"}), [PANCAKES])
    assert len(llm.calls) == 2


def test_evaluate_variant_end_to_end_on_fixture(enriched_conn, tmp_path):
    judge = CachedJudge(FakeJudgeLLM(), tmp_path / "j.json")
    results = evaluate_variant(
        enriched_conn,
        [CASE],
        "coverage",
        lambda q: find_recipes(enriched_conn, q, SearchOptions()),
        judge,
    )
    [result] = results
    assert result.recipes and result.hit == 1.0
    summary = summarize(results)
    assert summary["allergen_violations"] == 0 and summary["hit_at_5"] == 1.0


def test_a_failing_case_is_recorded_and_the_run_continues(enriched_conn, tmp_path):
    judge = CachedJudge(FakeJudgeLLM(), tmp_path / "j.json")
    other = CASE.model_copy(update={"id": "ok"})

    def find(query):
        if query.preferences_text == "boom":
            raise ConnectionError("network down")
        return find_recipes(enriched_conn, query, SearchOptions())

    failing = CASE.model_copy(update={"id": "bad", "preferences": "boom"})
    results = evaluate_variant(enriched_conn, [failing, other], "v", find, judge)
    assert results[0].error == "ConnectionError: network down" and results[0].hit == 0
    assert results[1].hit == 1.0
    summary = summarize(results)
    assert summary["errors"] == 1 and summary["hit_at_5"] == 0.5


def test_judge_rates_the_wish_with_its_goals_while_the_search_gets_them_apart(
    enriched_conn, tmp_path
):
    case = CASE.model_copy(
        update={"preferences": "breakfast", "goals": [NutritionGoal.HIGH_PROTEIN]}
    )
    assert case.to_query().preferences_text == "breakfast"  # as the finder writes it
    assert case.to_query().nutrition_goals == [NutritionGoal.HIGH_PROTEIN]
    llm = FakeJudgeLLM()
    CachedJudge(llm, tmp_path / "j.json").judge(enriched_conn, case, [PANCAKES])
    assert llm.wishes == ["breakfast (high protein)"]
    assert CASE.judged_wish == "pancakes"  # no goals: unchanged, so old judgments still apply
    assert case.model_copy(update={"preferences": ""}).judged_wish == "high protein"


def run_of(*relevant_lists):
    from pantry_chef.evaluation.search_eval import CaseResult

    return [
        CaseResult(
            case_id=f"c{i}",
            group="g",
            variant="v",
            recipes=[
                {"name": "r", "judge_score": 5 if ok else 2, "hard_rule_failures": []}
                for ok in relevant
            ],
            relevant=list(relevant),
        )
        for i, relevant in enumerate(relevant_lists)
    ]


def test_repeated_runs_report_mean_and_range():
    from pantry_chef.evaluation.search_eval import summarize_repeats

    first = run_of([True], [False])  # hit@5 50%, MRR 0.5, judge 3.5
    second = run_of([False, True], [True])  # hit@5 100%, MRR 0.75, judge 4.0
    spread = summarize_repeats([first, second])
    assert spread["hit_at_5"] == pytest.approx((0.75, 0.5, 1.0))
    assert spread["mrr"] == pytest.approx((0.625, 0.5, 0.75))
    assert spread["mean_judge_score"] == pytest.approx((3.75, 3.5, 4.0))


def test_report_has_a_repeats_table_only_with_repeats(tmp_path):
    from pantry_chef.evaluation.search_eval import write_report

    runs = [run_of([True], [False]), run_of([False, True], [True])]
    meta = {"timestamp": "t1", "cases": 2, "judge_model": "m", "judge_prompt_version": "1"}
    md, data = write_report({"v #1": runs[0], "v #2": runs[1]}, tmp_path, meta, {"v": runs})
    text = md.read_text()
    assert "| v | 2 | 75% (50%-100%) | 0.62 (0.50-0.75) | 3.75 (3.50-4.00) |" in text
    assert json.loads(data.read_text())["repeats"]["v"]["mrr"] == [0.625, 0.5, 0.75]
    md, _ = write_report({"v": runs[0]}, tmp_path, {**meta, "timestamp": "t2"})
    assert "Repeats" not in md.read_text()
