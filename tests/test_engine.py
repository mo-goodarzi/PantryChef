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
