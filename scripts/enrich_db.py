"""Apply normalization, staples, allergen rules and LLM labels to pantry.db.

Run after build_db.py and label_ingredients.py. Safe to run again.

Usage:
    uv run python scripts/enrich_db.py
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.enrich import enrich_database
from pantry_chef.ingredients.labeler import load_cache
from pantry_chef.observability import configure_logging, span, trace

DEFAULT_LABELS = Path("data/processed/ingredient_labels.json")

REPORT_QUERIES = {
    "recipe-ingredient rows with a category (target >= 95%)": (
        "SELECT AVG(i.category IS NOT NULL) FROM recipe_ingredients ri "
        "JOIN ingredients i ON i.id = ri.ingredient_id"
    ),
    "recipe-ingredient rows that are key": "SELECT AVG(is_key) FROM recipe_ingredients",
    "recipes with at least one allergen": (
        "SELECT AVG(EXISTS (SELECT 1 FROM recipe_allergens ra WHERE ra.recipe_id = r.id)) "
        "FROM recipes r"
    ),
    "recipes vegetarian": "SELECT AVG(is_vegetarian = 1) FROM recipes",
    "recipes vegan": "SELECT AVG(is_vegan = 1) FROM recipes",
    "recipes gluten-free": "SELECT AVG(is_gluten_free = 1) FROM recipes",
    "recipes with unknown diet flags": "SELECT AVG(is_vegan IS NULL) FROM recipes",
}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    args = parser.parse_args()

    configure_logging(settings.log_level)
    conn = connect(args.db)
    with trace("enrich_db"), span("enrich_db.apply"):
        counts = enrich_database(conn, load_cache(args.labels))

    print("\nIngredients")
    for key, value in counts.items():
        print(f"  {key:<22}{value:>8,}")
    print("\nCoverage")
    for label, sql in REPORT_QUERIES.items():
        print(f"  {label:<56}{conn.execute(sql).fetchone()[0]:>7.1%}")
    print("\nRecipes per allergen")
    for row in conn.execute(
        "SELECT allergen, COUNT(*) AS n FROM recipe_allergens GROUP BY allergen ORDER BY n DESC"
    ):
        print(f"  {row['allergen']:<14}{row['n']:>8,}")
    conn.close()


if __name__ == "__main__":
    main()
