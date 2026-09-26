"""Label all ingredients in pantry.db with the LLM (most frequent first).

Results are cached in data/processed/ingredient_labels.json; rerunning only labels
what is missing, so an interrupted run can simply be started again.

Usage:
    uv run python scripts/label_ingredients.py --limit 100     # pilot
    uv run python scripts/label_ingredients.py                 # everything
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.labeler import label_ingredients
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging, trace

DEFAULT_CACHE = Path("data/processed/ingredient_labels.json")


def ingredient_names_by_frequency(db_path: Path) -> list[str]:
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT i.name FROM ingredients i JOIN recipe_ingredients ri ON ri.ingredient_id = i.id "
        "GROUP BY i.id ORDER BY COUNT(*) DESC, i.name"
    ).fetchall()
    conn.close()
    return [row["name"] for row in rows]


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="label at most N new names")
    parser.add_argument("--workers", type=int, default=8, help="parallel LLM calls")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    names = ingredient_names_by_frequency(args.db)
    with trace("label_ingredients"):
        summary = label_ingredients(
            names,
            create_llm(settings),
            args.cache,
            batch_size=args.batch_size,
            limit=args.limit,
            workers=args.workers,
        )
    print(f"\nModel: {settings.llm_model}")
    print(f"  ingredients       {summary.requested:>7,}")
    print(f"  already cached    {summary.already_cached:>7,}")
    print(f"  newly labeled     {summary.labeled:>7,}")
    print(f"  skipped by LLM    {summary.missing:>7,}  (retried on next run)")
    print(f"  failed batches    {summary.failed_batches:>7,}")


if __name__ == "__main__":
    main()
