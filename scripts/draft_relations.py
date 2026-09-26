"""Draft ingredient relations for the most common ingredients with the LLM.

Writes src/pantry_chef/ingredients/seed/relations.json, which is committed and reviewed
like code. Existing entries are kept, so manual edits survive a rerun; use --overwrite
to redraft everything.

Usage:
    uv run python scripts/draft_relations.py --top 300 --vocabulary 1500
"""

import argparse
from collections import Counter
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.ingredients.relations import draft_relations, load_seed, save_seed
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging, trace


def canonical_names_by_frequency(db_path: Path) -> list[str]:
    conn = connect(db_path)
    counts: Counter[str] = Counter()
    for row in conn.execute(
        "SELECT i.name, COUNT(*) AS n FROM ingredients i "
        "JOIN recipe_ingredients ri ON ri.ingredient_id = i.id GROUP BY i.id"
    ):
        counts[normalize(row["name"])] += row["n"]
    conn.close()
    return [name for name, _ in counts.most_common()]


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--top", type=int, default=300)
    parser.add_argument("--vocabulary", type=int, default=1500)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    configure_logging(settings.log_level)
    names = canonical_names_by_frequency(args.db)
    existing = {} if args.overwrite else load_seed()
    todo = [n for n in names[: args.top] if n not in existing]

    with trace("draft_relations"):
        seed, summary = draft_relations(todo, names[: args.vocabulary], create_llm(settings))
    save_seed({**existing, **seed})

    print(f"\nDrafted {summary.ingredients} ingredients, {summary.relations} relations")
    print(f"Dropped {len(summary.dropped_names)} names not in the vocabulary, e.g.:")
    print("  " + ", ".join(sorted(set(summary.dropped_names))[:30]))


if __name__ == "__main__":
    main()
