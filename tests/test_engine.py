import pytest

from pantry_chef.agents.verifier import verify
from pantry_chef.db.repository import load_recipe_ingredients
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.verification import VerificationStatus
from pantry_chef.search.engine import missing_key_names, search

PANCAKES, WAFFLES, TUNA_BURRITOS = 5170, 31750, 422


def test_search_returns_ranked_candidates_with_ingredients(enriched_conn):
    result = search(enriched_conn, RecipeQuery(ingredients=["Flour", "butter", "eggs", "milk"]))
    top = result.candidates[0]
    assert top.recipe_id in {PANCAKES, WAFFLES}
    assert top.missing_key == []
    assert {i.canonical_name for i in top.ingredients} >= {"flour", "egg", "milk", "salt"}
    assert result.matched_recipes >= len(result.candidates)


def test_missing_key_ingredients_are_reported_once_per_canonical_name(enriched_conn):
    result = search(enriched_conn, RecipeQuery(ingredients=["milk"]))
    pancakes = next(c for c in result.candidates if c.recipe_id == PANCAKES)
    assert pancakes.missing_key == ["flour", "eggs", "butter"]  # recipe order


def test_contained_ingredients_add_allergens_for_the_verifier(enriched_conn):
    # salad dressing contains swiss cheese (relation seed) -> milk, plus its own eggs label
    ingredients = load_recipe_ingredients(enriched_conn, [TUNA_BURRITOS])[TUNA_BURRITOS]
    dressing = next(i for i in ingredients if i.name == "salad dressing")
    assert set(dressing.allergens) == {Allergen.EGGS, Allergen.MILK}


def test_filter_and_verifier_agree_on_allergens(enriched_conn):
    query = RecipeQuery(
        ingredients=["flour", "butter", "eggs", "milk", "bread", "tuna"],
        required_allergen_free=[Allergen.EGGS],
    )
    for candidate in search(enriched_conn, query).candidates:
        assert all(Allergen.EGGS not in i.allergens for i in candidate.ingredients)
        assert verify(candidate, query).status is VerificationStatus.PASS or any(
            r.code.value == "missing_ingredient" for r in verify(candidate, query).reasons
        )


def test_missing_key_names_keeps_order_and_dedupes():
    from pantry_chef.models.recipe import RecipeIngredient

    def ing(name, canonical, key=True):
        return RecipeIngredient(name=name, canonical_name=canonical, category=None, is_key=key)

    ingredients = [
        ing("eggs", "egg"),
        ing("flour", "flour"),
        ing("egg", "egg"),
        ing("salt", "salt", key=False),
    ]
    assert missing_key_names(ingredients, {"milk"}) == ["eggs", "flour"]


# --- full pipeline with verification results -----------------------------------------


def test_find_verified_keeps_the_verification_of_every_candidate(enriched_conn):
    from pantry_chef.search.engine import SearchOptions, find_verified

    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    result = find_verified(enriched_conn, query, SearchOptions(top_k=2))

    assert result.matched_recipes >= len(result.checked) > len(result.top)
    assert all(vc.candidate.recipe_id == vc.verification.candidate_id for vc in result.checked)
    assert [vc.candidate.recipe_id for vc in result.top] == [
        vc.candidate.recipe_id for vc in result.approved[:2]
    ]
    assert all(vc.verification.status is not VerificationStatus.FAIL for vc in result.top)
    # Failures stay available for the finder's feedback, with their reasons.
    failed = [vc for vc in result.checked if vc.verification.status is VerificationStatus.FAIL]
    assert failed and all(vc.verification.reasons for vc in failed)


def test_find_recipes_returns_the_top_candidates_of_find_verified(enriched_conn):
    from pantry_chef.search.engine import SearchOptions, find_recipes, find_verified

    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    top = find_verified(enriched_conn, query, SearchOptions()).top
    assert find_recipes(enriched_conn, query, SearchOptions()) == [vc.candidate for vc in top]


def test_candidates_carry_the_nutrition_the_verifier_checks(enriched_conn):
    result = search(enriched_conn, RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"]))
    pancakes = next(c for c in result.candidates if c.recipe_id == PANCAKES)
    assert (pancakes.sugar_pdv, pancakes.sodium_pdv) == (17.0, 13.0)


def test_the_hidden_allergen_check_keeps_its_own_model(monkeypatch, tmp_path, state_conn):
    """A cheaper LLM_MODEL must never reach a safety check."""
    from pantry_chef.config import Settings
    from pantry_chef.llm import factory
    from pantry_chef.search import engine, semantic

    created = []

    class Recorded:
        def __init__(self, model):
            self.model_name = model

    monkeypatch.setattr(
        factory, "create_llm", lambda s: created.append(s.llm_model) or Recorded(s.llm_model)
    )
    monkeypatch.setattr(semantic, "ChromaNameIndex", lambda path: None)
    settings = Settings(
        _env_file=None,
        db_path=tmp_path / "pantry.db",
        llm_model="gpt-5.4-nano",
        hidden_allergen_model="gpt-5.4-mini",
    )
    _, verifier = engine.matching_from_settings(settings, None, None, state_conn)
    assert verifier.hidden_checker.llm.model_name == "gpt-5.4-mini"
    assert created == ["gpt-5.4-nano", "gpt-5.4-mini"]  # matcher, hidden-allergen check


def test_verifier_off_lets_failed_recipes_through_for_the_eval_only(enriched_conn):
    from pantry_chef.search.engine import SearchOptions, find_verified

    query = RecipeQuery(ingredients=["milk"])  # every recipe misses key ingredients
    on = find_verified(enriched_conn, query, SearchOptions())
    off = find_verified(enriched_conn, query, SearchOptions(use_verifier=False))
    statuses = {vc.verification.status for vc in off.top}
    assert all(vc.verification.status is not VerificationStatus.FAIL for vc in on.top)
    assert VerificationStatus.FAIL in statuses  # the verdict is still recorded, not obeyed
    assert SearchOptions().use_verifier  # the app default never turns it off


def test_allergen_filter_off_leaves_the_verifier_as_the_only_code_layer(enriched_conn):
    from pantry_chef.search.engine import SearchOptions, find_verified

    query = RecipeQuery(
        ingredients=["flour", "butter", "eggs", "milk"], required_allergen_free=[Allergen.EGGS]
    )

    def has_eggs(result):
        return any(
            Allergen.EGGS in i.allergens for vc in result.top for i in vc.candidate.ingredients
        )

    sql_off = SearchOptions(use_allergen_filter=False)
    assert not has_eggs(find_verified(enriched_conn, query, sql_off))  # the verifier catches it
    no_safety = SearchOptions(use_allergen_filter=False, use_verifier=False)
    assert has_eggs(find_verified(enriched_conn, query, no_safety))  # nothing does
    defaults = SearchOptions()
    assert defaults.use_allergen_filter and defaults.use_allergy_review and defaults.use_verifier


@pytest.mark.parametrize(("model", "imported"), [("gpt-5.4-mini", 1), ("gpt-5.4-nano", 0)])
def test_old_matcher_answers_are_reused_only_by_their_own_model(
    monkeypatch, tmp_path, state_conn, model, imported
):
    """The old pantry.db match_cache rows carry no model: a model comparison must not
    reuse gpt-5.4-mini's answers for another model."""
    import sqlite3

    from pantry_chef.config import Settings
    from pantry_chef.llm import factory
    from pantry_chef.search import engine, semantic

    old_db = tmp_path / "pantry.db"
    old = sqlite3.connect(old_db)
    old.execute(
        "CREATE TABLE match_cache (user_term TEXT, recipe_term TEXT, label TEXT, "
        "source TEXT, created_at TEXT, PRIMARY KEY (user_term, recipe_term))"
    )
    old.execute("INSERT INTO match_cache VALUES ('pasta', 'spaghetti', 'substitute', 'llm', 't')")
    old.commit()
    old.close()
    monkeypatch.setattr(factory, "create_llm", lambda s: None)
    monkeypatch.setattr(semantic, "ChromaNameIndex", lambda path: None)
    settings = Settings(_env_file=None, db_path=old_db, llm_model=model)
    engine.matching_from_settings(settings, None, None, state_conn)
    rows = state_conn.execute("SELECT COUNT(*) FROM match_cache").fetchone()[0]
    assert rows == imported
