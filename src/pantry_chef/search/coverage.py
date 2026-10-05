"""Ingredient coverage: which key ingredients of a recipe the user already has.

Only key ingredients count (staples, spices, condiments are assumed or optional), and
they are counted by canonical name, so "egg" and "eggs" in one recipe count once.
Candidates are recipes that share at least one KEY ingredient with the pantry.

Ingredient score = (1 - u) * coverage part + u * pantry usage, where
- coverage part = have_key / total_key - 0.1 per missing key ingredient ("can I make it?")
- pantry usage  = share of the user's (non-staple) pantry items the recipe uses
  ("does it use what I have?"). Without it, a one-ingredient recipe that uses 1 of 4 pantry
  items has full coverage and beats a dish that uses all 4 but misses one garlic clove.
  Protein items (steak, chicken, tofu) weigh double: the dish is usually built around them,
  so a steak recipe should beat a potato side dish that uses the onion and garlic.
Nutrition goals subtract a fixed penalty from recipes that miss them (see goal_penalty).
Ranking: highest score, then recipes that use more of the pantry, then rating.
"""

import json
import sqlite3
from dataclasses import dataclass

from pantry_chef.ingredients.protein import (  # noqa: F401  (re-exported for callers)
    ProteinFacts,
    is_high_protein,
    protein_calorie_share,
)
from pantry_chef.models.query import NutritionGoal

MISSING_KEY_PENALTY = 0.1
# Bayesian average: a recipe needs about this many ratings before its own average
# outweighs the overall mean, so one 5-star review does not beat 500 reviews at 4.8.
RATING_PRIOR_WEIGHT = 10
# Weight of a pantry item in the usage part, by ingredient category (default 1).
CATEGORY_WEIGHTS = {"protein": 2.0}
# Subtracted from the ingredient score for each nutrition goal a recipe misses.
GOAL_PENALTY = 0.25


@dataclass
class CoverageRow:
    recipe_id: int
    name: str
    minutes: int
    total_key: int
    have_key: int
    avg_rating: float | None
    n_ratings: int
    ingredient_score: float
    weighted_rating: float
    pantry_used: float = 0.0  # distinct pantry items this recipe uses, weighted
    pantry_usage: float = 0.0  # pantry_used / weighted number of non-staple pantry items
    semantic_score: float | None = None  # set by the semantic step (0..1 within the pool)
    final_score: float | None = None  # set by the semantic step; else = ingredient_score

    def sort_key(self) -> tuple:
        final = self.final_score if self.final_score is not None else self.ingredient_score
        return (-final, -self.have_key, -self.weighted_rating, self.recipe_id)


def coverage_part(have_key: int, total_key: int) -> float:
    """have_key / total_key, minus a penalty for each missing key ingredient."""
    if total_key == 0:
        return 1.0
    missing = total_key - have_key
    return have_key / total_key - MISSING_KEY_PENALTY * missing


def ingredient_score(
    have_key: int, total_key: int, usage: float = 0.0, usage_weight: float = 0.0
) -> float:
    """(1 - usage_weight) * coverage part + usage_weight * pantry usage."""
    return (1 - usage_weight) * coverage_part(have_key, total_key) + usage_weight * usage


def pantry_usage(
    have_names: list[str], covered: dict[str, str], weights: dict[str, float] | None = None
) -> float:
    """Distinct pantry items behind the recipe's matched names, each counted with its
    weight (default 1). An item that covers two names, like "pasta" for "spaghetti" and
    "pasta noodle", counts once."""
    items = {covered[name] for name in have_names if name in covered}
    return sum((weights or {}).get(item, 1.0) for item in items)


def goal_penalty(goals: list[NutritionGoal], high_protein: bool) -> float:
    """Points subtracted from the ingredient score for each nutrition goal the recipe misses
    (high_protein is recipes.is_high_protein, computed at enrich time)."""
    missed = [goal for goal in goals if goal is NutritionGoal.HIGH_PROTEIN and not high_protein]
    return GOAL_PENALTY * len(missed)


def weighted_rating(avg: float | None, n: int, overall_mean: float) -> float:
    if avg is None or n == 0:
        return overall_mean
    return (n * avg + RATING_PRIOR_WEIGHT * overall_mean) / (n + RATING_PRIOR_WEIGHT)


# Reads only the recipe_ingredients rows that match the pantry (index on canonical_name
# and ingredient_id); the number of key ingredients per recipe is precomputed (n_key).
COVERAGE_SQL = """
WITH have AS (
    SELECT ri.recipe_id, COUNT(DISTINCT i.canonical_name) AS have_key,
           GROUP_CONCAT(DISTINCT i.canonical_name) AS have_names
    FROM ingredients i JOIN recipe_ingredients ri ON ri.ingredient_id = i.id
    WHERE i.canonical_name IN (SELECT value FROM json_each(:pantry)) AND ri.is_key = 1
    GROUP BY ri.recipe_id
)
SELECT r.id, r.name, r.minutes, r.n_key AS total_key, h.have_key, h.have_names,
       s.avg_rating, COALESCE(s.n_ratings, 0) AS n_ratings, {high_protein} AS high_protein
FROM have h
JOIN recipes r ON r.id = h.recipe_id
LEFT JOIN recipe_stats s ON s.recipe_id = r.id
WHERE {conditions}
"""


def rank_by_coverage(
    conn: sqlite3.Connection,
    pantry: list[str] | dict[str, str],
    conditions: list[str],
    params: dict[str, object],
    limit: int | None = None,
    usage_weight: float = 0.0,
    pantry_weights: dict[str, float] | None = None,
    goals: list[NutritionGoal] | None = None,
) -> tuple[list[CoverageRow], int]:
    """Recipes sorted by ingredient score (the first `limit`, or all), and how many passed
    the filters.

    `pantry` is canonical names, or {covered name: pantry item it comes from} when the
    pantry was expanded. `pantry_weights` gives each non-staple pantry item its weight
    (see CATEGORY_WEIGHTS); without it every distinct item counts 1.
    """
    covered = pantry if isinstance(pantry, dict) else {name: name for name in pantry}
    goals = goals or []
    if pantry_weights is not None:
        size = max(sum(pantry_weights.values()), 1.0)
    else:
        size = max(len(set(covered.values())), 1)
    overall_mean = (
        conn.execute("SELECT AVG(avg_rating) FROM recipe_stats WHERE n_ratings > 0").fetchone()[0]
        or 0.0
    )
    sql = COVERAGE_SQL.format(
        conditions=" AND ".join(conditions),
        # Read only when a goal needs it, so databases enriched before the column existed
        # still work without goals.
        high_protein="r.is_high_protein" if goals else "0",
    )
    try:
        rows = conn.execute(sql, {**params, "pantry": json.dumps(sorted(covered))}).fetchall()
    except sqlite3.OperationalError as error:
        if "is_high_protein" in str(error):
            raise RuntimeError(
                "this database has no recipes.is_high_protein yet; "
                "run scripts/enrich_db.py again (labels are cached, it is quick)"
            ) from error
        raise

    ranked = []
    for row in rows:
        used = pantry_usage((row["have_names"] or "").split(","), covered, pantry_weights)
        usage = min(used / size, 1.0)
        penalty = goal_penalty(goals, bool(row["high_protein"]))
        ranked.append(
            CoverageRow(
                recipe_id=row["id"],
                name=row["name"],
                minutes=row["minutes"],
                total_key=row["total_key"],
                have_key=row["have_key"],
                avg_rating=row["avg_rating"],
                n_ratings=row["n_ratings"],
                ingredient_score=ingredient_score(
                    row["have_key"], row["total_key"], usage, usage_weight
                )
                - penalty,
                weighted_rating=weighted_rating(row["avg_rating"], row["n_ratings"], overall_mean),
                pantry_used=used,
                pantry_usage=usage,
            )
        )
    ranked.sort(key=CoverageRow.sort_key)
    return ranked[:limit], len(rows)
