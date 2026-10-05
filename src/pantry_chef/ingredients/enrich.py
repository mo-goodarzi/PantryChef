"""Combine rules and LLM labels into the final ingredient facts, and write them to the DB.

Combination rules (safety first):
- allergens = rule allergens OR LLM allergens; the source of each is recorded
- contains_meat / contains_fish / animal_product = rules OR LLM
- staples never have quantity_matters and are never key ingredients
Recipe-level data (recipe_allergens, is_key, diet flags) is then derived in SQL.
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from pantry_chef.ingredients.allergens import Allergen, detect_allergens
from pantry_chef.ingredients.diet import detect_meat
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.ingredients.protein import ProteinFacts, is_high_protein
from pantry_chef.ingredients.relations import load_seed
from pantry_chef.ingredients.staples import is_staple
from pantry_chef.models.ingredient import Category, IngredientLabel, QuantityLabel

# Ingredients in these categories are "key": missing one means the recipe can't be made.
KEY_CATEGORIES = frozenset({Category.PROTEIN, Category.DAIRY, Category.GRAIN, Category.PRODUCE})
SEAFOOD_ALLERGENS = frozenset({Allergen.FISH, Allergen.CRUSTACEANS, Allergen.MOLLUSCS})
ANIMAL_ALLERGENS = frozenset({Allergen.MILK, Allergen.EGGS}) | SEAFOOD_ALLERGENS


@dataclass
class IngredientFacts:
    canonical_name: str
    category: Category | None  # None = not labeled yet
    is_staple: bool
    quantity_matters: bool
    is_key: bool
    allergen_sources: dict[Allergen, str]  # allergen -> "rule" | "llm"
    contains_meat: bool
    contains_fish: bool
    animal_product: bool


def combine(
    name: str, label: IngredientLabel | None, quantity_label: QuantityLabel | None = None
) -> IngredientFacts:
    """Final facts for one ingredient from its rules and (optional) LLM labels."""
    rule_allergens = detect_allergens(name)
    llm_allergens = set(label.allergens) if label else set()
    allergen_sources = {a: "rule" for a in rule_allergens}
    allergen_sources |= {a: "llm" for a in llm_allergens - rule_allergens}
    allergens = set(allergen_sources)

    staple = is_staple(name)
    category = label.category if label else None
    contains_meat = detect_meat(name) or bool(label and label.contains_meat)
    contains_fish = bool(allergens & SEAFOOD_ALLERGENS) or bool(label and label.contains_fish)
    animal_product = (
        contains_meat
        or contains_fish
        or bool(allergens & ANIMAL_ALLERGENS)
        or bool(label and label.animal_product)
    )
    # Unlabeled non-staples count as key: assuming "needed" is the safe default.
    is_key = not staple and (category is None or category in KEY_CATEGORIES)

    return IngredientFacts(
        canonical_name=normalize(name),
        category=category,
        is_staple=staple,
        quantity_matters=bool(quantity_label and quantity_label.quantity_matters) and not staple,
        is_key=is_key,
        allergen_sources=allergen_sources,
        contains_meat=contains_meat,
        contains_fish=contains_fish,
        animal_product=animal_product,
    )


def write_ingredient_facts(conn: sqlite3.Connection, facts: dict[int, IngredientFacts]) -> None:
    """Replace all per-ingredient facts. Safe to run again (idempotent)."""
    conn.executemany(
        "UPDATE ingredients SET canonical_name = ?, category = ?, is_staple = ?, "
        "quantity_matters = ?, contains_meat = ?, contains_fish = ?, animal_product = ? "
        "WHERE id = ?",
        [
            (
                f.canonical_name,
                f.category.value if f.category else None,
                int(f.is_staple),
                int(f.quantity_matters),
                int(f.contains_meat),
                int(f.contains_fish),
                int(f.animal_product),
                ingredient_id,
            )
            for ingredient_id, f in facts.items()
        ],
    )
    conn.execute("DELETE FROM ingredient_allergens")
    conn.executemany(
        "INSERT INTO ingredient_allergens (ingredient_id, allergen, source) VALUES (?, ?, ?)",
        [
            (ingredient_id, allergen.value, source)
            for ingredient_id, f in facts.items()
            for allergen, source in f.allergen_sources.items()
        ],
    )
    conn.executemany(
        "UPDATE recipe_ingredients SET is_key = ? WHERE ingredient_id = ?",
        [(int(f.is_key), ingredient_id) for ingredient_id, f in facts.items()],
    )


def derive_recipe_data(conn: sqlite3.Connection) -> None:
    """Materialize recipe_allergens and the recipe diet flags from ingredient facts.

    A diet flag is 0 as soon as one ingredient breaks the diet, NULL when some ingredient
    is not labeled yet (unknown, so filters must treat it as "not allowed"), else 1.
    """
    conn.execute("DELETE FROM recipe_allergens")
    conn.execute(
        "INSERT INTO recipe_allergens (recipe_id, allergen) "
        "SELECT DISTINCT ri.recipe_id, ia.allergen FROM recipe_ingredients ri "
        "JOIN ingredient_allergens ia ON ia.ingredient_id = ri.ingredient_id"
    )
    conn.execute(
        """
        UPDATE recipes SET
          n_key = (
            SELECT COUNT(DISTINCT i.canonical_name)
            FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = recipes.id AND ri.is_key = 1),
          is_vegetarian = (
            SELECT CASE WHEN MAX(i.contains_meat OR i.contains_fish) = 1 THEN 0
                        WHEN MAX(i.category IS NULL) = 1 THEN NULL ELSE 1 END
            FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = recipes.id),
          is_vegan = (
            SELECT CASE WHEN MAX(i.animal_product) = 1 THEN 0
                        WHEN MAX(i.category IS NULL) = 1 THEN NULL ELSE 1 END
            FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = recipes.id),
          is_gluten_free = (
            SELECT CASE WHEN EXISTS (
                     SELECT 1 FROM recipe_allergens ra
                     WHERE ra.recipe_id = recipes.id AND ra.allergen = 'gluten') THEN 0
                   WHEN MAX(i.category IS NULL) = 1 THEN NULL ELSE 1 END
            FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
            WHERE ri.recipe_id = recipes.id)
        """
    )


HIGH_PROTEIN_FACTS_SQL = """
SELECT r.id, r.protein_pdv, r.calories,
       COALESCE(MAX(ri.is_key = 1 AND i.category = 'protein'
                    AND (i.contains_meat = 1 OR i.contains_fish = 1)), 0) AS meat_or_fish_key,
       COALESCE(MAX(ri.is_key = 1 AND i.category = 'protein'), 0) AS protein_source_key
FROM recipes r
LEFT JOIN recipe_ingredients ri ON ri.recipe_id = r.id
LEFT JOIN ingredients i ON i.id = ri.ingredient_id
GROUP BY r.id
"""


def derive_high_protein(conn: sqlite3.Connection) -> int:
    """recipes.is_high_protein from the rule in ingredients/protein.py, computed once here
    instead of during every search. Adds the column to databases built before it existed.
    Returns the number of high-protein recipes."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
    if "is_high_protein" not in columns:
        conn.execute("ALTER TABLE recipes ADD COLUMN is_high_protein INTEGER")
    flags = [
        (
            int(
                is_high_protein(
                    ProteinFacts(
                        protein_pdv=row["protein_pdv"],
                        calories=row["calories"],
                        meat_or_fish_key=bool(row["meat_or_fish_key"]),
                        protein_source_key=bool(row["protein_source_key"]),
                    )
                )
            ),
            row["id"],
        )
        for row in conn.execute(HIGH_PROTEIN_FACTS_SQL)
    ]
    conn.executemany("UPDATE recipes SET is_high_protein = ? WHERE id = ?", flags)
    return sum(flag for flag, _ in flags)


def write_relations(conn: sqlite3.Connection, seed: dict[str, dict]) -> int:
    """Store the reviewed relation seed (keyed by canonical name) for every ingredient id.

    One canonical name can cover several raw names ("egg", "eggs", "large eggs"), so each
    relation is stored for all of them. Returns the number of rows written.
    """
    ids_by_canonical: dict[str, list[int]] = defaultdict(list)
    for row in conn.execute("SELECT id, canonical_name FROM ingredients"):
        ids_by_canonical[row["canonical_name"]].append(row["id"])

    parent_rows: set[tuple[int, int]] = set()
    relation_rows: dict[tuple[int, int, str], str | None] = {}
    for name, relations in seed.items():
        for a in ids_by_canonical.get(name, []):
            for parent in relations["parents"]:
                parent_rows |= {(a, b) for b in ids_by_canonical.get(parent, [])}
            for part in relations["contains"]:
                for b in ids_by_canonical.get(part, []):
                    relation_rows[(a, b, "contains")] = None
            for substitute in relations["substitutes"]:
                for b in ids_by_canonical.get(substitute["name"], []):
                    relation_rows[(a, b, "substitute")] = substitute["note"]

    conn.execute("DELETE FROM ingredient_parent")
    conn.execute("DELETE FROM ingredient_relation")
    conn.executemany(
        "INSERT INTO ingredient_parent (child_id, parent_id) VALUES (?, ?)", sorted(parent_rows)
    )
    conn.executemany(
        "INSERT INTO ingredient_relation (a_id, b_id, relation, note) VALUES (?, ?, ?, ?)",
        [(a, b, relation, note) for (a, b, relation), note in sorted(relation_rows.items())],
    )
    return len(parent_rows) + len(relation_rows)


def enrich_database(
    conn: sqlite3.Connection,
    labels: dict[str, IngredientLabel],
    quantity_labels: dict[str, QuantityLabel] | None = None,
    relation_seed: dict[str, dict] | None = None,
) -> dict:
    """Apply rules + labels to every ingredient, then derive recipe data. Returns counts."""
    seed = load_seed() if relation_seed is None else relation_seed
    rows = conn.execute("SELECT id, name FROM ingredients").fetchall()
    quantity_labels = quantity_labels or {}
    facts = {
        row["id"]: combine(row["name"], labels.get(row["name"]), quantity_labels.get(row["name"]))
        for row in rows
    }
    with conn:
        write_ingredient_facts(conn, facts)
        derive_recipe_data(conn)
        high_protein = derive_high_protein(conn)
        relation_rows = write_relations(conn, seed)
    return {
        "relation_rows": relation_rows,
        "high_protein_recipes": high_protein,
        "ingredients": len(facts),
        "labeled": sum(f.category is not None for f in facts.values()),
        "staples": sum(f.is_staple for f in facts.values()),
        "quantity_matters": sum(f.quantity_matters for f in facts.values()),
        "with_allergens": sum(bool(f.allergen_sources) for f in facts.values()),
        "llm_only_allergens": sum(
            source == "llm" for f in facts.values() for source in f.allergen_sources.values()
        ),
    }
