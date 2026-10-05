"""Hard filters as SQL conditions on `recipes r` (first safety layer).

Every condition is a plain SQL string using named parameters; lists are passed as JSON
and read with json_each, so the SQL never changes with the number of items.
"""

import json

from pantry_chef.ingredients.allergens import PUNCTUATION, allergy_word_variants
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.models.query import NUTRITION_LIMITS, Diet, RecipeQuery

# Minutes outside this range are data errors (0 min, or > 24 h up to ~2 billion min).
MIN_MINUTES = 1
MAX_PLAUSIBLE_MINUTES = 24 * 60

DIET_COLUMNS = {
    Diet.VEGETARIAN: "is_vegetarian",
    Diet.VEGAN: "is_vegan",
    Diet.GLUTEN_FREE: "is_gluten_free",
}


def words_sql(expr: str) -> str:
    """SQL that turns a name into ' word word ' for whole-word LIKE matching, exactly like
    allergens.mentions_word (lowercase; the same punctuation and '-' become spaces), so
    the SQL filter and the verifier cannot disagree about a name."""
    sql = f"lower({expr})"
    for ch in PUNCTUATION + "-":
        sql = f"replace({sql}, '{ch.replace(chr(39), chr(39) * 2)}', ' ')"
    return f"(' ' || {sql} || ' ')"


def filter_conditions(query: RecipeQuery) -> tuple[list[str], dict[str, object]]:
    """SQL conditions (joined with AND by the caller) and their parameters."""
    conditions = [f"r.minutes BETWEEN {MIN_MINUTES} AND {MAX_PLAUSIBLE_MINUTES}"]
    params: dict[str, object] = {}

    if query.max_minutes is not None:
        conditions.append("r.minutes <= :max_minutes")
        params["max_minutes"] = query.max_minutes

    if query.required_allergen_free:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM recipe_allergens ra WHERE ra.recipe_id = r.id "
            "AND ra.allergen IN (SELECT value FROM json_each(:allergens)))"
        )
        params["allergens"] = json.dumps([a.value for a in query.required_allergen_free])

    # A diet flag of NULL (unknown) never passes: "= 1" is false for NULL, and so is
    # "<= limit" for unknown nutrition.
    for diet in query.diets:
        if diet in NUTRITION_LIMITS:
            column, limit = NUTRITION_LIMITS[diet]
            conditions.append(f"r.{column} <= {limit}")
        else:
            conditions.append(f"r.{DIET_COLUMNS[diet]} = 1")

    if query.exclude_ingredients:
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM recipe_ingredients ri2 "
            "JOIN ingredients i2 ON i2.id = ri2.ingredient_id WHERE ri2.recipe_id = r.id "
            "AND i2.canonical_name IN (SELECT value FROM json_each(:exclude_ingredients)))"
        )
        params["exclude_ingredients"] = json.dumps(
            sorted({normalize(name) for name in query.exclude_ingredients})
        )

    if query.other_allergies:
        # Whole-word match on the raw and canonical name: "kiwi" also excludes
        # "kiwi fruit" and "strawberry kiwi gelatin powder".
        conditions.append(
            "NOT EXISTS (SELECT 1 FROM recipe_ingredients ri3 "
            "JOIN ingredients i3 ON i3.id = ri3.ingredient_id "
            "JOIN json_each(:other_allergy_words) w "
            "WHERE ri3.recipe_id = r.id AND ("
            f"{words_sql('i3.name')} LIKE '% ' || w.value || ' %' "
            f"OR {words_sql('i3.canonical_name')} LIKE '% ' || w.value || ' %'))"
        )
        params["other_allergy_words"] = json.dumps(
            sorted({v for word in query.other_allergies for v in allergy_word_variants(word)})
        )

    if query.exclude_recipe_ids:
        conditions.append("r.id NOT IN (SELECT value FROM json_each(:exclude_recipe_ids))")
        params["exclude_recipe_ids"] = json.dumps(query.exclude_recipe_ids)

    if query.meal_type:
        conditions.append("r.meal_type = :meal_type")
        params["meal_type"] = query.meal_type

    if query.cuisine:
        conditions.append("r.cuisine = :cuisine")
        params["cuisine"] = query.cuisine

    return conditions, params
