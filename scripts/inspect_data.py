"""Report data quality issues in the built database.

Usage:
    uv run python scripts/inspect_data.py [--db data/processed/pantry.db]
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect

QUERIES = {
    "Recipes": "SELECT COUNT(*) FROM recipes",
    "Missing description": "SELECT COUNT(*) FROM recipes WHERE description IS NULL",
    "Minutes = 0": "SELECT COUNT(*) FROM recipes WHERE minutes = 0",
    "Minutes > 24h": "SELECT COUNT(*) FROM recipes WHERE minutes > 1440",
    "No meal_type": "SELECT COUNT(*) FROM recipes WHERE meal_type IS NULL",
    "No cuisine": "SELECT COUNT(*) FROM recipes WHERE cuisine IS NULL",
    "No ratings": (
        "SELECT COUNT(*) FROM recipes r LEFT JOIN recipe_stats s ON s.recipe_id = r.id "
        "WHERE COALESCE(s.n_ratings, 0) = 0"
    ),
    "Ingredients used once": (
        "SELECT COUNT(*) FROM (SELECT ingredient_id FROM recipe_ingredients "
        "GROUP BY ingredient_id HAVING COUNT(*) = 1)"
    ),
}


def print_table(title: str, rows: list) -> None:
    print(f"\n{title}")
    for row in rows:
        print("  " + "  ".join(f"{value!s:<28}" for value in row))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    args = parser.parse_args()

    conn = connect(args.db)
    print("Counts")
    for label, sql in QUERIES.items():
        print(f"  {label:<24}{conn.execute(sql).fetchone()[0]:>10,}")

    print_table(
        "Ingredients per recipe (n_ingredients: recipes)",
        conn.execute(
            "SELECT n_ingredients, COUNT(*) FROM recipes GROUP BY n_ingredients "
            "ORDER BY n_ingredients"
        ).fetchall(),
    )
    print_table(
        "Most common ingredients",
        conn.execute(
            "SELECT i.name, COUNT(*) AS n FROM recipe_ingredients ri "
            "JOIN ingredients i ON i.id = ri.ingredient_id "
            "GROUP BY i.id ORDER BY n DESC LIMIT 30"
        ).fetchall(),
    )
    for column in ("meal_type", "cuisine"):
        print_table(
            f"Recipes by {column}",
            conn.execute(
                f"SELECT {column}, COUNT(*) AS n FROM recipes GROUP BY {column} "
                "ORDER BY n DESC LIMIT 20"
            ).fetchall(),
        )
    print_table(
        "Longest recipes (minutes outliers)",
        conn.execute("SELECT id, name, minutes FROM recipes ORDER BY minutes DESC LIMIT 5"),
    )


if __name__ == "__main__":
    main()
