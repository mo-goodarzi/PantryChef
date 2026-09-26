"""Search pipeline.

search():        hard filters -> ingredient coverage -> (semantic re-scoring) -> candidates
find_recipes():  search -> verify -> top k (diversity and rerank are added as options)
"""

import sqlite3
from dataclasses import dataclass, replace

from pantry_chef.agents.verifier import verify
from pantry_chef.config import Settings
from pantry_chef.db.repository import load_recipe_ingredients
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import VerificationStatus
from pantry_chef.observability import span
from pantry_chef.search.coverage import CoverageRow, rank_by_coverage
from pantry_chef.search.filters import filter_conditions
from pantry_chef.search.semantic import Embedder, RecipeVectorStore, semantic_scores


@dataclass
class SemanticSearch:
    """What the semantic step needs; built once and reused for every query."""

    embedder: Embedder
    store: RecipeVectorStore
    weight: float = 0.3
    coverage_pool: int = 1000
    neighbors: int = 2000


def semantic_from_settings(settings: Settings) -> SemanticSearch:
    """Load the local embedding model and open the Chroma collection (slow: once per run)."""
    from pantry_chef.search.semantic import ChromaRecipeStore, SentenceTransformerEmbedder

    return SemanticSearch(
        embedder=SentenceTransformerEmbedder(settings.embedding_model),
        store=ChromaRecipeStore(settings.chroma_path),
        weight=settings.semantic_weight,
        coverage_pool=settings.coverage_pool,
        neighbors=settings.semantic_neighbors,
    )


@dataclass
class SearchResult:
    candidates: list[Candidate]
    matched_recipes: int  # recipes with >= 1 pantry key ingredient that passed the filters


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


def rescore_with_semantics(
    rows: list[CoverageRow], wish: str, semantic: SemanticSearch
) -> list[CoverageRow]:
    """Pool = top coverage rows + rows among the wish's nearest recipes; re-sort by
    final = (1 - w) * ingredient_score + w * semantic_score."""
    query_vector = semantic.embedder.embed_query(wish)
    nearest = set(semantic.store.nearest(query_vector, semantic.neighbors))
    pool = rows[: semantic.coverage_pool]
    pool += [r for r in rows[semantic.coverage_pool :] if r.recipe_id in nearest]

    vectors = semantic.store.get_vectors([r.recipe_id for r in pool])
    scores = semantic_scores(query_vector, vectors)
    w = semantic.weight
    rescored = [
        replace(
            r,
            semantic_score=scores.get(r.recipe_id, 0.0),
            final_score=(1 - w) * r.ingredient_score + w * scores.get(r.recipe_id, 0.0),
        )
        for r in pool
    ]
    return sorted(rescored, key=CoverageRow.sort_key)


def search(
    conn: sqlite3.Connection,
    query: RecipeQuery,
    limit: int = 20,
    semantic: SemanticSearch | None = None,
) -> SearchResult:
    pantry = pantry_names(query)
    conditions, params = filter_conditions(query)

    with span("search.coverage", pantry_size=len(pantry), filters=len(conditions)):
        rows, matched = rank_by_coverage(conn, pantry, conditions, params)
    if semantic is not None and query.preferences_text.strip():
        with span("search.semantic", pool=min(len(rows), semantic.coverage_pool)):
            rows = rescore_with_semantics(rows, query.preferences_text, semantic)
    rows = rows[:limit]

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
            semantic_score=row.semantic_score,
            final_score=row.final_score if row.final_score is not None else row.ingredient_score,
        )
        for row in rows
    ]
    return SearchResult(candidates=candidates, matched_recipes=matched)


@dataclass(frozen=True)
class SearchOptions:
    """Which pipeline steps run (each one is measured separately in eval)."""

    pool_size: int = 50  # candidates to verify
    top_k: int = 5
    use_semantic: bool = False


def find_recipes(
    conn: sqlite3.Connection,
    query: RecipeQuery,
    options: SearchOptions | None = None,
    semantic: SemanticSearch | None = None,
) -> list[Candidate]:
    """Full pipeline: search -> verify -> top k verified recipes."""
    options = options or SearchOptions()
    if options.use_semantic and semantic is None:
        raise ValueError("use_semantic=True needs a SemanticSearch")
    result = search(
        conn, query, limit=options.pool_size, semantic=semantic if options.use_semantic else None
    )
    with span("search.verify", candidates=len(result.candidates)):
        verified = [
            c for c in result.candidates if verify(c, query).status is VerificationStatus.PASS
        ]
    return verified[: options.top_k]
