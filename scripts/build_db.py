"""Build data/processed/pantry.db from the Food.com CSV files.

Usage:
    uv run python scripts/build_db.py --csv data/raw/RAW_recipes.csv
    uv run python scripts/build_db.py --limit 1000        # quick development build
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.loader import BuildSummary, build_database
from pantry_chef.observability import configure_logging, trace


def print_summary(summary: BuildSummary) -> None:
    print("\nDatabase summary")
    print(f"  recipes               {summary.n_recipes:>10,}")
    for reason, count in sorted(summary.skipped.items()):
        print(f"  skipped ({reason:<13}) {count:>10,}")
    print(f"  unique ingredients    {summary.n_ingredients:>10,}")
    print(f"  recipe-ingredients    {summary.n_recipe_ingredients:>10,}")
    print(f"  unique tags           {summary.n_tags:>10,}")
    print(f"  recipe-tags           {summary.n_recipe_tags:>10,}")
    print(f"  recipes with reviews  {summary.n_recipes_with_stats:>10,}")
    m = summary.minutes
    print(
        f"  minutes: p10={m['p10']:.0f} median={m['median']:.0f} p90={m['p90']:.0f} "
        f"p99={m['p99']:.0f} | zero={m['zero']:,} over_24h={m['over_24h']:,}"
    )


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=settings.raw_recipes_csv)
    parser.add_argument("--interactions", type=Path, default=settings.raw_interactions_csv)
    parser.add_argument("--no-interactions", action="store_true", help="skip recipe_stats")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--limit", type=int, default=None, help="only load the first N rows")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    with trace("build_db"):
        summary = build_database(
            recipes_csv=args.csv,
            db_path=args.db,
            interactions_csv=None if args.no_interactions else args.interactions,
            limit=args.limit,
        )
    print_summary(summary)


if __name__ == "__main__":
    main()
