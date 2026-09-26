"""Ingredient coverage: which key ingredients of a recipe the user already has.

Only key ingredients count (staples, spices, condiments are assumed or optional), and
they are counted by canonical name, so "egg" and "eggs" in one recipe count once.
Candidates are recipes that share at least one KEY ingredient with the pantry.

Ranking: highest ingredient score, then recipes that use more of the pantry, then rating.
"""

import json
import sqlite3
from dataclasses import dataclass

MISSING_KEY_PENALTY = 0.1
# Bayesian average: a recipe needs about this many ratings before its own average
# outweighs the overall mean, so one 5-star review does not beat 500 reviews at 4.8.
RATING_PRIOR_WEIGHT = 10


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


def ingredient_score(have_key: int, total_key: int) -> float:
    """have_key / total_key, minus a penalty for each missing key ingredient."""
    if total_key == 0:
        return 1.0
    missing = total_key - have_key
    return have_key / total_key - MISSING_KEY_PENALTY * missing


def weighted_rating(avg: float | None, n: int, overall_mean: float) -> float:
    if avg is None or n == 0:
        return overall_mean
    return (n * avg + RATING_PRIOR_WEIGHT * overall_mean) / (n + RATING_PRIOR_WEIGHT)


# Reads only the recipe_ingredients rows that match the pantry (index on canonical_name
# and ingredient_id); the number of key ingredients per recipe is precomputed (n_key).
COVERAGE_SQL = """
WITH have AS (
    SELECT ri.recipe_id, COUNT(DISTINCT i.canonical_name) AS have_key
    FROM ingredients i JOIN recipe_ingredients ri ON ri.ingredient_id = i.id
    WHERE i.canonical_name IN (SELECT value FROM json_each(:pantry)) AND ri.is_key = 1
    GROUP BY ri.recipe_id
)
SELECT r.id, r.name, r.minutes, r.n_key AS total_key, h.have_key,
       s.avg_rating, COALESCE(s.n_ratings, 0) AS n_ratings
FROM have h
JOIN recipes r ON r.id = h.recipe_id
LEFT JOIN recipe_stats s ON s.recipe_id = r.id
WHERE {conditions}
"""


def rank_by_coverage(
    conn: sqlite3.Connection,
    pantry: list[str],
    conditions: list[str],
    params: dict[str, object],
    limit: int,
) -> tuple[list[CoverageRow], int]:
    """Top `limit` recipes by coverage, and how many recipes passed the filters.

    `pantry` must already be canonical names.
    """
    overall_mean = (
        conn.execute("SELECT AVG(avg_rating) FROM recipe_stats WHERE n_ratings > 0").fetchone()[0]
        or 0.0
    )
    sql = COVERAGE_SQL.format(conditions=" AND ".join(conditions))
    rows = conn.execute(sql, {**params, "pantry": json.dumps(sorted(set(pantry)))}).fetchall()

    ranked = [
        CoverageRow(
            recipe_id=row["id"],
            name=row["name"],
            minutes=row["minutes"],
            total_key=row["total_key"],
            have_key=row["have_key"],
            avg_rating=row["avg_rating"],
            n_ratings=row["n_ratings"],
            ingredient_score=ingredient_score(row["have_key"], row["total_key"]),
            weighted_rating=weighted_rating(row["avg_rating"], row["n_ratings"], overall_mean),
        )
        for row in rows
    ]
    ranked.sort(key=lambda r: (-r.ingredient_score, -r.have_key, -r.weighted_rating, r.recipe_id))
    return ranked[:limit], len(rows)
