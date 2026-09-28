"""Build the SQLite database from the Food.com CSV files.

The build writes to a temporary file and swaps it in at the end, so a failed run never
leaves a half-built database and every run starts from scratch (idempotent).
"""

import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from pantry_chef.db.connection import connect, create_schema
from pantry_chef.db.parsing import (
    NUTRITION_FIELDS,
    clean_ingredients,
    clean_name,
    clean_tags,
    clean_text,
    parse_list,
    split_nutrition,
)
from pantry_chef.db.tag_mapping import derive_cuisine, derive_meal_type
from pantry_chef.observability import get_logger, span

log = get_logger("db.loader")

RECIPE_COLUMNS = [
    "id",
    "name",
    "description",
    "minutes",
    "n_steps",
    "n_ingredients",
    "steps_json",
    "submitted",
    *NUTRITION_FIELDS,
    "cuisine",
    "meal_type",
]


@dataclass
class PreparedRecipe:
    """One cleaned recipe, ready to insert."""

    row: dict
    ingredients: list[str]
    tags: list[str]


@dataclass
class BuildSummary:
    n_recipes: int = 0
    skipped: Counter = field(default_factory=Counter)
    n_ingredients: int = 0
    n_recipe_ingredients: int = 0
    n_tags: int = 0
    n_recipe_tags: int = 0
    n_recipes_with_stats: int = 0
    minutes: dict[str, float] = field(default_factory=dict)


def prepare_recipe(raw: dict) -> PreparedRecipe | str:
    """Clean one CSV row. Returns the recipe, or a skip reason if it is unusable."""
    name = clean_name(raw["name"])
    if name is None:
        return "empty_name"

    try:
        recipe_id, minutes = int(raw["id"]), int(raw["minutes"])  # NaN raises ValueError
        steps = [s for s in (clean_text(step) for step in parse_list(raw["steps"])) if s]
        ingredients = clean_ingredients(parse_list(raw["ingredients"]))
        tags = clean_tags(parse_list(raw["tags"]))
        nutrition = split_nutrition(raw["nutrition"])
    except (ValueError, SyntaxError):
        return "parse_error"

    if not steps:
        return "no_steps"
    if not ingredients:
        return "no_ingredients"

    row = {
        "id": recipe_id,
        "name": name,
        "description": clean_text(raw["description"]),
        "minutes": minutes,
        "n_steps": len(steps),
        "n_ingredients": len(ingredients),
        "steps_json": json.dumps(steps),
        "submitted": clean_text(raw["submitted"]),
        **nutrition,
        "cuisine": derive_cuisine(tags),
        "meal_type": derive_meal_type(tags),
    }
    return PreparedRecipe(row=row, ingredients=ingredients, tags=tags)


def insert_recipes(conn: sqlite3.Connection, recipes: list[PreparedRecipe]) -> BuildSummary:
    """Insert recipes, ingredients, tags and their link tables."""
    ingredient_ids: dict[str, int] = {}
    tag_ids: dict[str, int] = {}
    recipe_ingredient_rows = []
    recipe_tag_rows = []

    for recipe in recipes:
        recipe_id = recipe.row["id"]
        for position, name in enumerate(recipe.ingredients):
            ingredient_id = ingredient_ids.setdefault(name, len(ingredient_ids) + 1)
            recipe_ingredient_rows.append((recipe_id, ingredient_id, position))
        for tag in recipe.tags:
            tag_id = tag_ids.setdefault(tag, len(tag_ids) + 1)
            recipe_tag_rows.append((recipe_id, tag_id))

    placeholders = ", ".join(f":{col}" for col in RECIPE_COLUMNS)
    conn.executemany(
        f"INSERT INTO recipes ({', '.join(RECIPE_COLUMNS)}) VALUES ({placeholders})",
        [recipe.row for recipe in recipes],
    )
    # canonical_name starts equal to name; Phase 2 normalization overwrites it.
    conn.executemany(
        "INSERT INTO ingredients (id, name, canonical_name) VALUES (?, ?, ?)",
        [(i, name, name) for name, i in ingredient_ids.items()],
    )
    conn.executemany(
        "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, position) VALUES (?, ?, ?)",
        recipe_ingredient_rows,
    )
    conn.executemany(
        "INSERT INTO tags (id, name) VALUES (?, ?)", [(i, t) for t, i in tag_ids.items()]
    )
    conn.executemany("INSERT INTO recipe_tags (recipe_id, tag_id) VALUES (?, ?)", recipe_tag_rows)

    return BuildSummary(
        n_recipes=len(recipes),
        n_ingredients=len(ingredient_ids),
        n_recipe_ingredients=len(recipe_ingredient_rows),
        n_tags=len(tag_ids),
        n_recipe_tags=len(recipe_tag_rows),
    )


def compute_recipe_stats(interactions: pd.DataFrame, recipe_ids: set[int]) -> pd.DataFrame:
    """Review counts and average star rating per recipe.

    In Food.com a rating of 0 means "review without a rating", so it counts as a review
    but not in the average.
    """
    df = interactions[interactions["recipe_id"].isin(recipe_ids)]
    rated = df[df["rating"] > 0]
    stats = pd.DataFrame(
        {
            "n_reviews": df.groupby("recipe_id").size(),
            "n_ratings": rated.groupby("recipe_id").size(),
            "avg_rating": rated.groupby("recipe_id")["rating"].mean().round(3),
        }
    )
    stats["n_ratings"] = stats["n_ratings"].fillna(0).astype(int)
    return stats.rename_axis("recipe_id").reset_index()


def insert_recipe_stats(conn: sqlite3.Connection, stats: pd.DataFrame) -> None:
    rows = [
        (
            int(r.recipe_id),
            int(r.n_reviews),
            int(r.n_ratings),
            None if pd.isna(r.avg_rating) else float(r.avg_rating),
        )
        for r in stats.itertuples(index=False)
    ]
    conn.executemany(
        "INSERT INTO recipe_stats (recipe_id, n_reviews, n_ratings, avg_rating) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )


def minutes_distribution(minutes: pd.Series) -> dict[str, float]:
    return {
        "p10": float(minutes.quantile(0.10)),
        "median": float(minutes.median()),
        "p90": float(minutes.quantile(0.90)),
        "p99": float(minutes.quantile(0.99)),
        "zero": int((minutes == 0).sum()),
        "over_24h": int((minutes > 24 * 60).sum()),
    }


def build_database(
    recipes_csv: Path,
    db_path: Path,
    interactions_csv: Path | None = None,
    limit: int | None = None,
) -> BuildSummary:
    """Build the database from scratch at `db_path`."""
    with span("build_db.read_csv", limit=limit):
        raw = pd.read_csv(recipes_csv, nrows=limit, dtype={"name": str, "description": str})

    with span("build_db.prepare", rows=len(raw)):
        recipes: list[PreparedRecipe] = []
        skipped: Counter = Counter()
        for record in raw.to_dict("records"):
            result = prepare_recipe(record)
            if isinstance(result, str):
                skipped[result] += 1
            else:
                recipes.append(result)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = connect(tmp_path)
    try:
        with span("build_db.insert", recipes=len(recipes)), conn:
            create_schema(conn)
            summary = insert_recipes(conn, recipes)

            if interactions_csv is not None:
                interactions = pd.read_csv(interactions_csv, usecols=["recipe_id", "rating"])
                stats = compute_recipe_stats(interactions, {r.row["id"] for r in recipes})
                insert_recipe_stats(conn, stats)
                summary.n_recipes_with_stats = len(stats)
    finally:
        conn.close()

    os.replace(tmp_path, db_path)

    summary.skipped = skipped
    summary.minutes = minutes_distribution(pd.Series([r.row["minutes"] for r in recipes]))
    log.info(
        "build_db.done",
        db_path=str(db_path),
        recipes=summary.n_recipes,
        skipped=dict(skipped),
        ingredients=summary.n_ingredients,
        tags=summary.n_tags,
    )
    return summary
