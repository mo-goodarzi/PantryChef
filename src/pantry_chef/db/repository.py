"""Read helpers that turn database rows into models."""

import json
import sqlite3
from collections import defaultdict

from pydantic import BaseModel

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.recipe import RecipeIngredient
from pantry_chef.search.text import useful_tags

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


def recipe_summaries(conn: sqlite3.Connection, recipe_ids: list[int]) -> dict[int, dict]:
    """What an LLM (judge or reranker) sees about each recipe."""
    summaries = {}
    for recipe_id in recipe_ids:
        row = conn.execute(
            "SELECT name, minutes, meal_type, cuisine, description FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        tags = useful_tags(
            [
                r["name"]
                for r in conn.execute(
                    "SELECT t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id "
                    "WHERE rt.recipe_id = ?",
                    (recipe_id,),
                )
            ]
        )
        ingredients = [
            r["name"]
            for r in conn.execute(
                "SELECT i.name FROM recipe_ingredients ri JOIN ingredients i "
                "ON i.id = ri.ingredient_id WHERE ri.recipe_id = ? ORDER BY ri.position",
                (recipe_id,),
            )
        ]
        summaries[recipe_id] = {
            "recipe_id": recipe_id,
            "name": row["name"],
            "minutes": row["minutes"],
            "meal_type": row["meal_type"],
            "cuisine": row["cuisine"],
            "description": (row["description"] or "")[:300],
            "tags": tags[:15],
            "ingredients": ingredients,
        }
    return summaries


class IngredientOption(BaseModel):
    """An ingredient (canonical name) with the facts needed to re-check allergens and
    diet: used for suggested substitutes and for pantry items standing in for a recipe
    ingredient."""

    name: str  # canonical name
    note: str | None
    allergens: list[Allergen]
    contains_meat: bool
    contains_fish: bool
    animal_product: bool


def load_substitutes(
    conn: sqlite3.Connection, canonical_names: list[str]
) -> dict[str, list[IngredientOption]]:
    """Substitutes for each canonical name, with their allergens and diet facts."""
    rows = conn.execute(
        """
        SELECT DISTINCT a.canonical_name AS original, b.id, b.canonical_name AS name,
               rel.note, b.contains_meat, b.contains_fish, b.animal_product
        FROM ingredient_relation rel
        JOIN ingredients a ON a.id = rel.a_id
        JOIN ingredients b ON b.id = rel.b_id
        WHERE rel.relation = 'substitute'
          AND a.canonical_name IN (SELECT value FROM json_each(:names))
        """,
        {"names": json.dumps(canonical_names)},
    ).fetchall()
    allergens: dict[int, set[Allergen]] = defaultdict(set)
    for row in conn.execute(
        "SELECT ingredient_id, allergen FROM ingredient_allergens "
        "WHERE ingredient_id IN (SELECT value FROM json_each(:ids))",
        {"ids": json.dumps([r["id"] for r in rows])},
    ):
        allergens[row["ingredient_id"]].add(Allergen(row["allergen"]))

    options: dict[str, dict[str, IngredientOption]] = defaultdict(dict)
    for row in rows:
        option = options[row["original"]].get(row["name"])
        merged = allergens[row["id"]] | set(option.allergens if option else [])
        options[row["original"]][row["name"]] = IngredientOption(
            name=row["name"],
            note=row["note"],
            allergens=sorted(merged),
            contains_meat=bool(row["contains_meat"]) or bool(option and option.contains_meat),
            contains_fish=bool(row["contains_fish"]) or bool(option and option.contains_fish),
            animal_product=bool(row["animal_product"]) or bool(option and option.animal_product),
        )
    return {k: list(v.values()) for k, v in options.items()}


def load_canonical_facts(
    conn: sqlite3.Connection, canonical_names: list[str]
) -> dict[str, IngredientOption]:
    """Allergens and diet facts per canonical name, merged over all raw names that share
    it (conservative: any raw name with an allergen gives the canonical name that
    allergen)."""
    facts: dict[str, IngredientOption] = {}
    rows = conn.execute(
        """
        SELECT i.canonical_name AS name, MAX(i.contains_meat) AS meat,
               MAX(i.contains_fish) AS fish, MAX(i.animal_product) AS animal,
               GROUP_CONCAT(DISTINCT ia.allergen) AS allergens
        FROM ingredients i LEFT JOIN ingredient_allergens ia ON ia.ingredient_id = i.id
        WHERE i.canonical_name IN (SELECT value FROM json_each(:names))
        GROUP BY i.canonical_name
        """,
        {"names": json.dumps(canonical_names)},
    )
    for row in rows:
        facts[row["name"]] = IngredientOption(
            name=row["name"],
            note=None,
            allergens=sorted(Allergen(a) for a in (row["allergens"] or "").split(",") if a),
            contains_meat=bool(row["meat"]),
            contains_fish=bool(row["fish"]),
            animal_product=bool(row["animal"]),
        )
    return facts
