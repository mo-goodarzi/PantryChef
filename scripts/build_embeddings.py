"""Embed recipes (name + description + tags) and ingredient names into Chroma.

Resumable: items already in a collection are skipped.

Usage:
    uv run python scripts/build_embeddings.py                  # recipes + ingredient names
    uv run python scripts/build_embeddings.py --limit 2000     # quick test (recipes)
"""

import argparse
import time
from collections import defaultdict
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.search.semantic import (
    ChromaNameIndex,
    ChromaRecipeStore,
    SentenceTransformerEmbedder,
)
from pantry_chef.search.text import recipe_text

BATCH = 2000


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument("--chroma", type=Path, default=settings.chroma_path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = connect(args.db)
    tags: dict[int, list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT rt.recipe_id, t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id"
    ):
        tags[row["recipe_id"]].append(row["name"])
    recipes = conn.execute("SELECT id, name, description FROM recipes ORDER BY id").fetchall()

    store = ChromaRecipeStore(args.chroma)
    done = store.existing_ids()
    todo = [r for r in recipes if r["id"] not in done][: args.limit]
    print(f"{len(recipes):,} recipes, {len(done):,} already embedded, {len(todo):,} to do")

    embedder = SentenceTransformerEmbedder(settings.embedding_model)
    start = time.perf_counter()
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        texts = [recipe_text(r["name"], r["description"], tags[r["id"]]) for r in batch]
        store.add([r["id"] for r in batch], embedder.embed_documents(texts))
        done_now = i + len(batch)
        rate = done_now / (time.perf_counter() - start)
        print(f"  {done_now:,}/{len(todo):,}  ({rate:.0f}/s)", flush=True)

    names = sorted(
        {
            r["canonical_name"]
            for r in conn.execute("SELECT DISTINCT canonical_name FROM ingredients")
        }
    )
    index = ChromaNameIndex(args.chroma)
    todo_names = sorted(set(names) - index.existing())
    print(f"{len(names):,} ingredient names, {len(todo_names):,} to embed")
    for i in range(0, len(todo_names), BATCH):
        batch = todo_names[i : i + BATCH]
        index.add(batch, embedder.embed_documents(batch))


if __name__ == "__main__":
    main()
