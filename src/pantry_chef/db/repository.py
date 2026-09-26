"""Read helpers that turn database rows into models."""

import json
import sqlite3
from collections import defaultdict

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.recipe import RecipeIngredient

INGREDIENTS_SQL = """
SELECT ri.recipe_id, i.id AS ingredient_id, i.name, i.canonical_name, i.category,
       ri.is_key, ri.is_optional, i.is_staple, i.quantity_matters, ri.quantity, ri.unit,
       i.contains_meat, i.contains_fish, i.animal_product
FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
WHERE ri.recipe_id IN (SELECT value FROM json_each(:ids))
ORDER BY ri.recipe_id, ri.position
"""

# Allergens of an ingredient: its own, its parents' (a kind of X has X's allergens) and
# those of ingredients it contains (pesto -> pine nut). This is the verifier's own path,
# independent of the materialized recipe_allergens table used by the SQL filter.
ALLERGENS_SQL = """
WITH wanted(id) AS (SELECT value FROM json_each(:ids))
SELECT ingredient_id AS id, allergen FROM ingredient_allergens
WHERE ingredient_id IN (SELECT id FROM wanted)
UNION
SELECT p.child_id, ia.allergen FROM ingredient_parent p
JOIN ingredient_allergens ia ON ia.ingredient_id = p.parent_id
WHERE p.child_id IN (SELECT id FROM wanted)
UNION
SELECT rel.a_id, ia.allergen FROM ingredient_relation rel
JOIN ingredient_allergens ia ON ia.ingredient_id = rel.b_id
WHERE rel.relation = 'contains' AND rel.a_id IN (SELECT id FROM wanted)
"""


def load_recipe_ingredients(
    conn: sqlite3.Connection, recipe_ids: list[int]
) -> dict[int, list[RecipeIngredient]]:
    rows = conn.execute(INGREDIENTS_SQL, {"ids": json.dumps(recipe_ids)}).fetchall()
    ingredient_ids = sorted({row["ingredient_id"] for row in rows})

    allergens: dict[int, set[Allergen]] = defaultdict(set)
    for row in conn.execute(ALLERGENS_SQL, {"ids": json.dumps(ingredient_ids)}):
        allergens[row["id"]].add(Allergen(row["allergen"]))

    result: dict[int, list[RecipeIngredient]] = defaultdict(list)
    for row in rows:
        result[row["recipe_id"]].append(
            RecipeIngredient(
                name=row["name"],
                canonical_name=row["canonical_name"],
                category=row["category"],
                is_key=bool(row["is_key"]),
                is_optional=bool(row["is_optional"]),
                is_staple=bool(row["is_staple"]),
                quantity_matters=bool(row["quantity_matters"]),
                quantity=row["quantity"],
                unit=row["unit"],
                allergens=sorted(allergens[row["ingredient_id"]]),
                contains_meat=bool(row["contains_meat"]),
                contains_fish=bool(row["contains_fish"]),
                animal_product=bool(row["animal_product"]),
            )
        )
    return dict(result)
