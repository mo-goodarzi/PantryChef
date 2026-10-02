import json

import pytest

from pantry_chef.ingredients.matcher import (
    CompositeMatcher,
    ExactMatcher,
    LLMMatchBatch,
    LLMMatcher,
    LLMPick,
    MatchCache,
    parents_from_seed,
)
from pantry_chef.models.matching import MatchLabel

PARENTS = {
    "cheddar cheese": {"cheese"},
    "sharp cheddar cheese": {"cheddar cheese"},
    "spaghetti": {"pasta"},
}


def labels(results):
    return {r.recipe_term: (r.user_term, r.label.value, r.source) for r in results}


# --- exact + hierarchy ---------------------------------------------------------------


def test_exact_name_is_same():
    [r] = ExactMatcher().match(["egg", "milk"], ["egg"])
    assert (r.user_term, r.label, r.source) == ("egg", MatchLabel.SAME, "exact")


def test_specific_kind_covers_the_general_recipe_ingredient():
    # user has sharp cheddar; recipe asks for cheese (two levels up)
    [r] = ExactMatcher(PARENTS).match(["sharp cheddar cheese"], ["cheese"])
    assert (r.label, r.source) == (MatchLabel.SAME, "hierarchy")


def test_general_pantry_item_is_only_a_substitute_for_a_specific_one():
    [r] = ExactMatcher(PARENTS).match(["pasta"], ["spaghetti"])
    assert r.label is MatchLabel.SUBSTITUTE


def test_egg_whites_do_not_cover_eggs_without_other_knowledge():
    [r] = ExactMatcher().match(["egg white"], ["egg"])
    assert r.label is MatchLabel.DIFFERENT and r.user_term is None


def test_exact_beats_hierarchy_when_both_apply():
    [r] = ExactMatcher(PARENTS).match(["pasta", "spaghetti"], ["spaghetti"])
    assert (r.user_term, r.label) == ("spaghetti", MatchLabel.SAME)


def test_parents_from_seed():
    seed = {
        "a": {"parents": ["b"], "contains": [], "substitutes": []},
        "c": {"parents": [], "contains": [], "substitutes": []},
    }
    assert parents_from_seed(seed) == {"a": {"b"}}


def test_reviewed_seed_relates_beef_steak_cuts_but_not_fish_or_pork_steaks():
    from pantry_chef.ingredients.relations import load_seed

    matcher = ExactMatcher(parents_from_seed(load_seed()))
    recipe = ["flank steak", "steak", "sirloin steak", "tuna steak", "ham steak", "pork steak"]
    result = labels(matcher.match(["beef steak"], recipe))
    assert result["flank steak"] == ("beef steak", "substitute", "hierarchy")
    assert result["sirloin steak"] == ("beef steak", "substitute", "hierarchy")
    assert result["steak"] == ("beef steak", "same", "hierarchy")
    assert {result[n][1] for n in ["tuna steak", "ham steak", "pork steak"]} == {"different"}
    # A specific cut covers a recipe that only says "steak".
    assert labels(matcher.match(["rib eye steak"], ["steak"]))["steak"][1] == "same"


# --- LLM and composite ---------------------------------------------------------------

# What a sensible LLM would answer, keyed by (user term, recipe term).
KNOWLEDGE = {
    ("egg", "egg white"): MatchLabel.CONTAINS,
    ("bread", "toast"): MatchLabel.SUBSTITUTE,
    ("toast", "bread"): MatchLabel.SUBSTITUTE,
    ("rice", "cooked rice"): MatchLabel.CONTAINS,
}


class FakeMatchLLM:
    def __init__(self, extra_picks=()):
        self.calls = []
        self.extra_picks = list(extra_picks)

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "ingredient_match" and schema is LLMMatchBatch
        users, recipes = json.loads(variables["user_terms"]), json.loads(variables["recipe_terms"])
        self.calls.append(recipes)
        picks = []
        for r in recipes:
            hits = [(u, KNOWLEDGE[(u, r)]) for u in users if (u, r) in KNOWLEDGE]
            u, label = hits[0] if hits else (None, MatchLabel.DIFFERENT)
            picks.append(LLMPick(recipe_term=r, user_term=u, label=label))
        return LLMMatchBatch(matches=picks + self.extra_picks)


@pytest.fixture
def composite(state_conn, tmp_path):
    llm = FakeMatchLLM()
    matcher = CompositeMatcher(
        ExactMatcher(PARENTS),
        MatchCache(state_conn),
        LLMMatcher(llm),
        log_path=tmp_path / "match_log.jsonl",
    )
    return matcher, llm, tmp_path / "match_log.jsonl"


def test_eggs_cover_egg_whites_but_not_the_other_way(composite):
    matcher, _, _ = composite
    assert labels(matcher.match(["egg"], ["egg white"]))["egg white"][1] == "contains"
    assert labels(matcher.match(["egg white"], ["egg"]))["egg"][1] == "different"


def test_toast_and_bread(composite):
    matcher, _, _ = composite
    assert labels(matcher.match(["toast"], ["bread"]))["bread"][:2] == ("toast", "substitute")


def test_only_unknown_terms_go_to_the_llm_in_one_batch(composite):
    matcher, llm, _ = composite
    results = matcher.match(
        ["egg", "rice", "cheddar cheese"], ["egg", "cheese", "cooked rice", "egg white"]
    )
    assert llm.calls == [["cooked rice", "egg white"]]
    assert labels(results) == {
        "egg": ("egg", "same", "exact"),
        "cheese": ("cheddar cheese", "same", "hierarchy"),
        "cooked rice": ("rice", "contains", "llm"),
        "egg white": ("egg", "contains", "llm"),
    }


def test_second_time_the_answer_comes_from_the_cache(composite):
    matcher, llm, _ = composite
    matcher.match(["egg", "rice"], ["cooked rice", "saffron"])
    again = matcher.match(["rice", "egg"], ["saffron", "cooked rice"])
    assert len(llm.calls) == 1
    assert labels(again) == {
        "saffron": (None, "different", "cache"),
        "cooked rice": ("rice", "contains", "cache"),
    }


def test_a_new_pantry_item_means_asking_again(composite):
    matcher, llm, _ = composite
    matcher.match(["egg"], ["cooked rice"])
    matcher.match(["egg", "rice"], ["cooked rice"])  # pair (rice, cooked rice) not cached yet
    assert len(llm.calls) == 2


def test_llm_answers_are_logged_for_training(composite):
    matcher, _, log_path = composite
    matcher.match(["rice"], ["cooked rice"])
    [line] = log_path.read_text().splitlines()
    entry = json.loads(line)
    assert entry["recipe_term"] == "cooked rice" and entry["label"] == "contains"
    assert entry["user_terms"] == ["rice"]


def test_invented_names_from_the_llm_are_ignored(enriched_conn):
    llm = FakeMatchLLM(
        extra_picks=[LLMPick(recipe_term="dragon fruit", user_term="rice", label=MatchLabel.SAME)]
    )
    matcher = CompositeMatcher(ExactMatcher(), None, LLMMatcher(llm))
    results = matcher.match(["rice"], ["cooked rice"])
    assert [r.recipe_term for r in results] == ["cooked rice"]


def test_without_llm_unknown_terms_are_different(state_conn):
    matcher = CompositeMatcher(ExactMatcher(), MatchCache(state_conn), llm=None)
    [r] = matcher.match(["rice"], ["cooked rice"])
    assert (r.label, r.source) == (MatchLabel.DIFFERENT, "none")


def test_results_keep_recipe_order_including_duplicates(composite):
    matcher, _, _ = composite
    results = matcher.match(["egg"], ["egg", "milk", "egg"])
    assert [r.recipe_term for r in results] == ["egg", "milk", "egg"]


def test_cache_entries_from_an_older_prompt_version_are_ignored(state_conn):
    old = MatchCache(state_conn, source="llm:ingredient_match:v1")
    old.put_many([("egg", "egg noodle", MatchLabel.CONTAINS)])
    assert old.get("egg", "egg noodle") is MatchLabel.CONTAINS
    new = MatchCache(state_conn, source="llm:ingredient_match:v2")
    assert new.get("egg", "egg noodle") is None


def test_llm_cache_source_includes_the_prompt_version():
    from pantry_chef.ingredients.matcher import llm_cache_source

    assert llm_cache_source(LLMMatcher(FakeMatchLLM())) == "llm:ingredient_match:v2"
