import pytest

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.query import Diet, RecipeQuery
from pantry_chef.search.filters import filter_conditions

PANCAKES, WAFFLES, POACHED_EGGS, EGG_FOO_YUNG = 5170, 31750, 118761, 133513
CHICKEN_SUPREME, CHICKEN_SOUP, CRAB_MELT, THAI_SOUP = 174, 194, 245, 282
FETTUCCINE, CALZONES, TUNA_BURRITOS = 361, 402, 422
FROZEN_DESSERT, TEST_ZERO_MINUTES, BREAD_ONLY = 38, 9000001, 9000002
LEMONADE, QUICK_MIX, APPLE_SALAD, SYRUP = 40, 81, 92, 133


def allowed(conn, **query_fields) -> set[int]:
    conditions, params = filter_conditions(RecipeQuery(ingredients=[], **query_fields))
    sql = f"SELECT id FROM recipes r WHERE {' AND '.join(conditions)}"
    return {row["id"] for row in conn.execute(sql, params)}


def test_minutes_outliers_are_always_excluded(enriched_conn):
    ids = allowed(enriched_conn)
    assert FROZEN_DESSERT not in ids  # 1485 minutes
    assert TEST_ZERO_MINUTES not in ids  # 0 minutes
    assert PANCAKES in ids


def test_max_minutes(enriched_conn):
    minutes = dict(enriched_conn.execute("SELECT id, minutes FROM recipes").fetchall())
    ids = allowed(enriched_conn, max_minutes=20)
    assert ids and all(minutes[i] <= 20 for i in ids)
    assert PANCAKES in ids  # exactly 20 minutes is allowed


def test_egg_allergy_excludes_every_egg_recipe(enriched_conn):
    ids = allowed(enriched_conn, required_allergen_free=[Allergen.EGGS])
    assert not ids & {PANCAKES, WAFFLES, POACHED_EGGS, EGG_FOO_YUNG}
    assert TUNA_BURRITOS not in ids  # salad dressing: hidden egg from the LLM label
    assert BREAD_ONLY in ids


def test_several_allergens(enriched_conn):
    ids = allowed(enriched_conn, required_allergen_free=[Allergen.FISH, Allergen.CELERY])
    assert not ids & {THAI_SOUP, CRAB_MELT, TUNA_BURRITOS, CHICKEN_SUPREME, CHICKEN_SOUP}


def test_vegetarian_excludes_meat_and_fish(enriched_conn):
    ids = allowed(enriched_conn, diets=[Diet.VEGETARIAN])
    meat_or_fish = {CHICKEN_SUPREME, CHICKEN_SOUP, CRAB_MELT, THAI_SOUP, CALZONES, TUNA_BURRITOS}
    assert not ids & meat_or_fish
    assert {PANCAKES, APPLE_SALAD} <= ids


def test_vegan_excludes_animal_products(enriched_conn):
    ids = allowed(enriched_conn, diets=[Diet.VEGAN])
    assert APPLE_SALAD not in ids  # honey
    assert PANCAKES not in ids  # eggs, milk, butter
    assert {LEMONADE, SYRUP, BREAD_ONLY} <= ids


def test_gluten_free(enriched_conn):
    ids = allowed(enriched_conn, diets=[Diet.GLUTEN_FREE])
    assert not ids & {PANCAKES, QUICK_MIX, FETTUCCINE, BREAD_ONLY, EGG_FOO_YUNG}
    assert POACHED_EGGS in ids


def test_unknown_diet_flag_never_passes(enriched_conn):
    enriched_conn.execute("UPDATE recipes SET is_vegan = NULL WHERE id = ?", (LEMONADE,))
    assert LEMONADE not in allowed(enriched_conn, diets=[Diet.VEGAN])


@pytest.mark.parametrize("word", ["mushroom", "Mushrooms", "fresh mushrooms"])
def test_excluded_ingredients_match_by_canonical_name(enriched_conn, word):
    ids = allowed(enriched_conn, exclude_ingredients=[word])
    assert not ids & {CHICKEN_SUPREME, FETTUCCINE, CALZONES}
    assert PANCAKES in ids


def test_excluded_recipe_ids(enriched_conn):
    assert PANCAKES not in allowed(enriched_conn, exclude_recipe_ids=[PANCAKES, 1])


def test_meal_type_and_cuisine(enriched_conn):
    breakfast = allowed(enriched_conn, meal_type="breakfast")
    assert {PANCAKES, WAFFLES, POACHED_EGGS} <= breakfast
    assert CALZONES not in breakfast
    assert allowed(enriched_conn, cuisine="no-such-cuisine") == set()
