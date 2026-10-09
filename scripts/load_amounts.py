"""Add ingredient amounts (original lines with units) and servings to pantry.db.

Run after build_db.py; needs `recipes_w_search_terms.csv` from the Kaggle dataset
"Food.com Recipes with Search Terms and Tags". Safe to run again.

Usage:
    uv run python scripts/load_amounts.py
    uv run python scripts/load_amounts.py --csv data/raw/recipes_w_search_terms.csv
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.amounts import load_amounts, read_amounts_csv
from pantry_chef.db.connection import connect
from pantry_chef.observability import configure_logging, span, trace


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=settings.amounts_csv)
    parser.add_argument("--db", type=Path, default=settings.db_path)
    args = parser.parse_args()

    configure_logging(settings.log_level)
    with trace("load_amounts"):
        with span("load_amounts.read_csv"):
            amounts = read_amounts_csv(args.csv)
        conn = connect(args.db)
        try:
            with conn:
                summary = load_amounts(conn, amounts)
        finally:
            conn.close()

    print("\nAmounts summary")
    print(
        f"  recipes with amounts   {summary.recipes_with_amounts:>10,} of {summary.recipes_in_db:,}"
    )
    print(
        f"  ingredient rows        {summary.rows_with_amounts:>10,} of {summary.rows_in_db:,} "
        f"({summary.rows_with_amounts / max(summary.rows_in_db, 1):.1%})"
    )
    rows = max(summary.rows_with_amounts, 1)
    print(f"  ... with a number      {summary.rows_with_quantity / rows:>10.1%}")
    print(
        f"  ... comparable         {summary.rows_comparable / rows:>10.1%}  (count or kitchen unit)"
    )


if __name__ == "__main__":
    main()
