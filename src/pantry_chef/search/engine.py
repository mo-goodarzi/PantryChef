"""Search pipeline: hard filters -> ingredient coverage -> candidates.

Phase 3 version (no LLM). Semantic match, diversity and rerank are added in Phase 4.
"""

import sqlite3
from dataclasses import dataclass

from pantry_chef.agents.verifier import verify
from pantry_chef.db.repository import load_recipe_ingredients
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import VerificationStatus
from pantry_chef.observability import span
from pantry_chef.search.coverage import rank_by_coverage
from pantry_chef.search.filters import filter_conditions


@dataclass
class SearchResult:
    candidates: list[Candidate]
    matched_recipes: int  # recipes with >= 1 pantry ingredient that passed the filters


def pantry_names(query: RecipeQuery) -> list[str]:
    return sorted({normalize(name) for name in query.ingredients if name.strip()})


def missing_key_names(ingredients: list[RecipeIngredient], pantry: set[str]) -> list[str]:
    """Key ingredients the user does not have, one per canonical name."""
    missing = {
        i.canonical_name: i.name
        for i in reversed(ingredients)  # reversed: keep the first name per canonical
        if i.is_key and i.canonical_name not in pantry
    }
    return [
        missing[name]
        for name in dict.fromkeys(i.canonical_name for i in ingredients)
        if name in missing
    ]


def search(conn: sqlite3.Connection, query: RecipeQuery, limit: int = 20) -> SearchResult:
    pantry = pantry_names(query)
    conditions, params = filter_conditions(query)

    with span("search.coverage", pantry_size=len(pantry), filters=len(conditions)):
        rows, matched = rank_by_coverage(conn, pantry, conditions, params, limit)
    with span("search.load_ingredients", candidates=len(rows)):
        ingredients = load_recipe_ingredients(conn, [row.recipe_id for row in rows])

    candidates = [
        Candidate(
            recipe_id=row.recipe_id,
            name=row.name,
            minutes=row.minutes,
            ingredients=ingredients.get(row.recipe_id, []),
            have_key=row.have_key,
            total_key=row.total_key,
            missing_key=missing_key_names(ingredients.get(row.recipe_id, []), set(pantry)),
            coverage=row.have_key / row.total_key if row.total_key else 1.0,
            avg_rating=row.avg_rating,
            n_ratings=row.n_ratings,
            ingredient_score=row.ingredient_score,
            final_score=row.ingredient_score,  # Phase 4 adds the semantic score
        )
        for row in rows
    ]
    return SearchResult(candidates=candidates, matched_recipes=matched)


@dataclass(frozen=True)
class SearchOptions:
    """Which pipeline steps run (each one is measured separately in eval)."""

    pool_size: int = 50  # candidates to verify
    top_k: int = 5


def find_recipes(
    conn: sqlite3.Connection, query: RecipeQuery, options: SearchOptions | None = None
) -> list[Candidate]:
    """Full pipeline: search -> verify -> top k verified recipes."""
    options = options or SearchOptions()
    result = search(conn, query, limit=options.pool_size)
    with span("search.verify", candidates=len(result.candidates)):
        verified = [
            c for c in result.candidates if verify(c, query).status is VerificationStatus.PASS
        ]
    return verified[: options.top_k]
