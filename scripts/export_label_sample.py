"""Export a random sample of labeled ingredients to CSV for a manual spot check.

Fill in the `correct` column (y/n) and `notes`, then record the error rate in
docs/decisions.md.

Usage:
    uv run python scripts/export_label_sample.py --n 200
"""

import argparse
import csv
import random
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect

COLUMNS = [
    "name",
    "canonical_name",
    "category",
    "is_staple",
    "quantity_matters",
    "allergens",
    "allergen_sources",
    "contains_meat",
    "contains_fish",
    "animal_product",
    "recipes",
    "correct",
    "notes",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/processed/label_spot_check.csv"))
    args = parser.parse_args()

    conn = connect(args.db)
    rows = conn.execute(
        """
        SELECT i.*, COUNT(DISTINCT ri.recipe_id) AS recipes,
               (SELECT GROUP_CONCAT(allergen, ' ') FROM ingredient_allergens
                WHERE ingredient_id = i.id) AS allergens,
               (SELECT GROUP_CONCAT(allergen || ':' || source, ' ') FROM ingredient_allergens
                WHERE ingredient_id = i.id) AS allergen_sources
        FROM ingredients i JOIN recipe_ingredients ri ON ri.ingredient_id = i.id
        GROUP BY i.id
        """
    ).fetchall()
    sample = random.Random(args.seed).sample(rows, min(args.n, len(rows)))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(sample, key=lambda r: -r["recipes"]):
            writer.writerow({**dict(row), "correct": "", "notes": ""})
    print(f"Wrote {len(sample)} ingredients to {args.out}")


if __name__ == "__main__":
    main()
