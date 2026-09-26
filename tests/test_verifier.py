import random

import pytest

from pantry_chef.agents.verifier import check_allergens, check_diet, check_ingredients, verify
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import FailureCode, VerificationStatus


def ing(
    name,
    canonical=None,
    key=True,
    staple=False,
    optional=False,
    allergens=(),
    meat=False,
    fish=False,
    animal=False,
):
    return RecipeIngredient(
        name=name,
        canonical_name=canonical or name,
        category=None,
        is_key=key,
        is_staple=staple,
        is_optional=optional,
        allergens=list(allergens),
        contains_meat=meat,
        contains_fish=fish,
        animal_product=animal,
    )


def candidate(*ingredients):
    return Candidate(
        recipe_id=1,
        name="test",
        minutes=10,
        ingredients=list(ingredients),
        have_key=0,
        total_key=0,
        coverage=0.0,
        ingredient_score=0.0,
        final_score=0.0,
    )


# --- ingredients ---------------------------------------------------------------------


def test_all_key_ingredients_available_passes():
    recipe = candidate(ing("eggs", "egg"), ing("milk"), ing("salt", key=False, staple=True))
    assert check_ingredients(recipe, {"egg", "milk"}).passed


def test_missing_key_ingredient_gives_specific_reason():
    result = check_ingredients(candidate(ing("eggs", "egg"), ing("flour")), {"egg"})
    assert not result.passed
    [reason] = result.reasons
    assert (reason.code, reason.item) == (FailureCode.MISSING_INGREDIENT, "flour")
    assert str(reason) == "missing_ingredient: flour"


def test_egg_whites_are_not_satisfied_by_eggs():
    result = check_ingredients(candidate(ing("egg whites", "egg white")), {"egg"})
    assert not result.passed


def test_staples_non_key_and_optional_ingredients_never_fail():
    recipe = candidate(
        ing("salt", key=False, staple=True),
        ing("cinnamon", key=False),
        ing("walnuts", "walnut", optional=True),
    )
    assert check_ingredients(recipe, set()).passed


# --- allergens -----------------------------------------------------------------------


def test_allergen_in_any_ingredient_fails():
    recipe = candidate(
        ing("flour", allergens=[Allergen.GLUTEN]),
        ing("pesto sauce", allergens=[Allergen.TREE_NUTS, Allergen.MILK]),
    )
    result = check_allergens(recipe, {Allergen.TREE_NUTS})
    [reason] = result.reasons
    assert (reason.code, reason.item) == (FailureCode.ALLERGEN, "pesto sauce")
    assert "tree_nuts" in reason.detail


def test_non_key_and_staple_ingredients_are_checked_too():
    recipe = candidate(ing("worcestershire sauce", key=False, allergens=[Allergen.FISH]))
    assert not check_allergens(recipe, {Allergen.FISH}).passed


def test_no_user_allergens_passes():
    assert check_allergens(candidate(ing("peanuts", allergens=[Allergen.PEANUTS])), set()).passed


def test_verifier_never_passes_a_recipe_with_a_user_allergen():
    """Property-style: 500 random recipes and allergy profiles."""
    rng = random.Random(7)
    allergens = list(Allergen)
    for _ in range(500):
        ingredients = [
            ing(
                f"item {i}",
                allergens=rng.sample(allergens, rng.randint(0, 2)),
                key=rng.random() < 0.5,
            )
            for i in range(rng.randint(1, 8))
        ]
        user = set(rng.sample(allergens, rng.randint(1, 3)))
        query = RecipeQuery(
            ingredients=[i.name for i in ingredients], required_allergen_free=sorted(user)
        )
        result = verify(candidate(*ingredients), query)
        present = {a for i in ingredients for a in i.allergens}
        if present & user:
            assert result.status is VerificationStatus.FAIL


# --- diet ----------------------------------------------------------------------------


def test_vegetarian_with_chicken_stock_fails():
    recipe = candidate(ing("rice"), ing("chicken stock", key=False, meat=True, animal=True))
    result = check_diet(recipe, {Diet.VEGETARIAN})
    [reason] = result.reasons
    assert (reason.code, reason.item) == (FailureCode.DIET_VIOLATION, "chicken stock")


def test_vegetarian_with_fish_sauce_fails():
    recipe = candidate(ing("fish sauce", key=False, fish=True, allergens=[Allergen.FISH]))
    assert not check_diet(recipe, {Diet.VEGETARIAN}).passed


def test_vegan_with_honey_fails_but_vegetarian_passes():
    recipe = candidate(ing("honey", key=False, animal=True))
    assert not check_diet(recipe, {Diet.VEGAN}).passed
    assert check_diet(recipe, {Diet.VEGETARIAN}).passed


def test_gluten_free_uses_the_gluten_allergen():
    recipe = candidate(ing("soy sauce", key=False, allergens=[Allergen.SOY, Allergen.GLUTEN]))
    assert not check_diet(recipe, {Diet.GLUTEN_FREE}).passed


# --- decision ------------------------------------------------------------------------


def test_verify_passes_only_when_every_check_passes():
    recipe = candidate(ing("eggs", "egg", allergens=[Allergen.EGGS]), ing("milk"))
    ok = verify(recipe, RecipeQuery(ingredients=["Eggs", "milk"]))
    assert ok.status is VerificationStatus.PASS and ok.reasons == []

    allergic = verify(
        recipe, RecipeQuery(ingredients=["eggs", "milk"], required_allergen_free=[Allergen.EGGS])
    )
    assert allergic.status is VerificationStatus.FAIL
    assert [str(r) for r in allergic.reasons] == ["allergen: egg"]


@pytest.mark.parametrize("pantry", [["large eggs", "milk"], ["EGGS", " milk "]])
def test_pantry_names_are_normalized(pantry):
    recipe = candidate(ing("eggs", "egg"), ing("milk"))
    assert verify(recipe, RecipeQuery(ingredients=pantry)).status is VerificationStatus.PASS
