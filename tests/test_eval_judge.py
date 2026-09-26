import json

from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.judge import CachedJudge, Judgment, JudgmentBatch, recipe_summaries
from pantry_chef.evaluation.search_eval import evaluate_variant, summarize
from pantry_chef.search.engine import SearchOptions, find_recipes

PANCAKES, WAFFLES = 5170, 31750
CASE = SearchCase(
    id="b", group="breakfast", pantry=["flour", "butter", "egg", "milk"], preferences="pancakes"
)


class FakeJudgeLLM:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "preference_judge" and schema is JudgmentBatch
        recipes = json.loads(variables["recipes"])
        self.calls.append([r["recipe_id"] for r in recipes])
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
