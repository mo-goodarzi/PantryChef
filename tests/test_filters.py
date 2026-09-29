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


def test_low_sugar_uses_the_sugar_share_of_daily_value(enriched_conn):
    # sugar_pdv: poached eggs 0, tuna burritos 10 (at the limit), pancakes/waffles 17
    ids = allowed(enriched_conn, diets=[Diet.LOW_SUGAR])
    assert {POACHED_EGGS, TUNA_BURRITOS} <= ids
    assert not ids & {PANCAKES, WAFFLES, LEMONADE, SYRUP}


def test_low_salt_uses_the_sodium_share_of_daily_value(enriched_conn):
    # sodium_pdv: poached eggs 3, pancakes 13, waffles 26
    ids = allowed(enriched_conn, diets=[Diet.LOW_SALT])
    assert POACHED_EGGS in ids
    assert not ids & {PANCAKES, WAFFLES, EGG_FOO_YUNG}


def test_unknown_nutrition_never_passes(enriched_conn):
    enriched_conn.execute("UPDATE recipes SET sugar_pdv = NULL WHERE id = ?", (POACHED_EGGS,))
    assert POACHED_EGGS not in allowed(enriched_conn, diets=[Diet.LOW_SUGAR])


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


def test_other_allergies_exclude_by_whole_word(enriched_conn):
    # "lemongrass" appears as "fresh lemongrass" and "lemongrass" in the two thai soups
    ids = allowed(enriched_conn, other_allergies=["lemongrass"])
    assert CHICKEN_SOUP not in ids and THAI_SOUP not in ids
    assert PANCAKES in ids


def test_other_allergy_words_match_plurals_in_raw_names(enriched_conn):
    # the apple salad lists "seedless grapes"
    assert APPLE_SALAD not in allowed(enriched_conn, other_allergies=["grape"])
    assert APPLE_SALAD in allowed(enriched_conn, other_allergies=["grapefruit"])
