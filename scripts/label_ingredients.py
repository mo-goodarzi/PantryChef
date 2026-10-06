"""Label all ingredients in pantry.db with the LLM (most frequent first).

Two tasks, each with its own prompt and cache in data/processed/:
- ingredients: category, allergens, diet fields -> ingredient_labels.json
- quantity:    quantity_matters                  -> quantity_labels.json
Rerunning only labels what is missing, so an interrupted run can simply be restarted.

Usage:
    uv run python scripts/label_ingredients.py --limit 100            # pilot
    uv run python scripts/label_ingredients.py                        # everything
    uv run python scripts/label_ingredients.py --task quantity --batch-size 100
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.labeler import INGREDIENT_TASK, QUANTITY_TASK, label_ingredients
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging, trace

TASKS = {
    "ingredients": (INGREDIENT_TASK, Path("data/processed/ingredient_labels.json")),
    "quantity": (QUANTITY_TASK, Path("data/processed/quantity_labels.json")),
}


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
    parser.add_argument("--task", choices=TASKS, default="ingredients")
    parser.add_argument("--cache", type=Path, default=None, help="default depends on --task")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="label at most N new names")
    parser.add_argument("--workers", type=int, default=8, help="parallel LLM calls")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    task, default_cache = TASKS[args.task]
    names = ingredient_names_by_frequency(args.db)
    with trace("label_ingredients"):
        summary = label_ingredients(
            names,
            # LABEL_MODEL, not LLM_MODEL: the allergen labels feed the SQL allergen filter,
            # so a cheaper LLM_MODEL must never reach them
            create_llm(settings.model_copy(update={"llm_model": settings.label_model})),
            args.cache or default_cache,
            task=task,
            batch_size=args.batch_size,
            limit=args.limit,
            workers=args.workers,
        )
    print(f"\nTask: {args.task} | model: {settings.label_model}")
    print(f"  ingredients       {summary.requested:>7,}")
    print(f"  already cached    {summary.already_cached:>7,}")
    print(f"  newly labeled     {summary.labeled:>7,}")
    print(f"  skipped by LLM    {summary.missing:>7,}  (retried on next run)")
    print(f"  failed batches    {summary.failed_batches:>7,}")


if __name__ == "__main__":
    main()
