"""Label approximate weights (grams per item, per cup) with the LLM, then write the seed.

Only ingredients the quantity question can ask about (amount matters, not a staple), by
canonical name, most frequent first. Answers are cached in data/processed/weight_labels.json
(rerunning only labels what is missing); the seed src/pantry_chef/ingredients/seed/
weights.json is rewritten from the cache at the end, for review in the pull request.

Usage:
    uv run python scripts/label_weights.py --limit 100      # pilot
    uv run python scripts/label_weights.py                  # everything
"""

import argparse
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.labeler import WEIGHT_TASK, label_ingredients, load_cache
from pantry_chef.ingredients.weights import SEED_PATH, save_seed
from pantry_chef.llm.factory import create_llm
from pantry_chef.models.ingredient import WeightLabel
from pantry_chef.observability import configure_logging, trace

CACHE = Path("data/processed/weight_labels.json")


def amount_names_by_frequency(db_path: Path) -> list[str]:
    """Canonical names the quantity question can ask about, most used first."""
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT i.canonical_name AS name FROM ingredients i "
        "JOIN recipe_ingredients ri ON ri.ingredient_id = i.id "
        "WHERE i.quantity_matters = 1 AND i.is_staple = 0 "
        "GROUP BY i.canonical_name ORDER BY COUNT(*) DESC, i.canonical_name"
    ).fetchall()
    conn.close()
    return [row["name"] for row in rows]


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None, help="label at most N new names")
    parser.add_argument("--workers", type=int, default=8, help="parallel LLM calls")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    names = amount_names_by_frequency(args.db)
    with trace("label_weights"):
        summary = label_ingredients(
            names,
            create_llm(settings.model_copy(update={"llm_model": settings.label_model})),
            CACHE,
            task=WEIGHT_TASK,
            batch_size=args.batch_size,
            limit=args.limit,
            workers=args.workers,
        )
    labels: dict[str, WeightLabel] = load_cache(CACHE, WeightLabel)
    save_seed({name: labels[name] for name in names if name in labels})
    print(f"\nWeights | model: {settings.label_model}")
    print(f"  ingredients       {summary.requested:>7,}")
    print(f"  already cached    {summary.already_cached:>7,}")
    print(f"  newly labeled     {summary.labeled:>7,}")
    print(f"  skipped by LLM    {summary.missing:>7,}  (retried on next run)")
    print(f"  failed batches    {summary.failed_batches:>7,}")
    print(f"  seed: {SEED_PATH}")


if __name__ == "__main__":
    main()
