"""Search pipeline.

search():        hard filters -> ingredient coverage -> (semantic re-scoring) -> candidates
find_verified(): search -> verify -> (diversity) -> (LLM rerank) -> top k, with the
                 verification of every candidate (find_recipes(): candidates only)
"""

import sqlite3
from dataclasses import dataclass, replace

from pantry_chef.agents.verifier import Verifier
from pantry_chef.config import Settings
from pantry_chef.db.repository import load_nutrition, load_recipe_ingredients
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.ingredients.staples import is_staple
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import VerificationResult, VerificationStatus
from pantry_chef.observability import span
from pantry_chef.search.coverage import CoverageRow, rank_by_coverage
from pantry_chef.search.diversity import mmr
from pantry_chef.search.expansion import PantryExpander
from pantry_chef.search.filters import filter_conditions
from pantry_chef.search.rerank import Reranker
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
    expander: PantryExpander | None = None,
    usage_weight: float = 0.5,
) -> SearchResult:
    pantry = pantry_names(query)
    conditions, params = filter_conditions(query)

    # Names the pantry covers -> the pantry item behind each ("spaghetti" -> "pasta").
    covered = {name: name for name in pantry}
    if expander is not None:
        with span("search.expand_pantry", pantry_size=len(pantry)):
            for name, match in expander.expand(pantry).items():
                covered.setdefault(name, match.user_term or name)
    pantry_size = sum(not is_staple(name) for name in pantry)

    with span("search.coverage", pantry_size=len(covered), filters=len(conditions)):
        rows, matched = rank_by_coverage(
            conn, covered, conditions, params, usage_weight=usage_weight, pantry_size=pantry_size
        )
    if semantic is not None and query.preferences_text.strip():
        with span("search.semantic", pool=min(len(rows), semantic.coverage_pool)):
            rows = rescore_with_semantics(rows, query.preferences_text, semantic)
    rows = rows[:limit]

    with span("search.load_ingredients", candidates=len(rows)):
        ingredients = load_recipe_ingredients(conn, [row.recipe_id for row in rows])
        nutrition = load_nutrition(conn, [row.recipe_id for row in rows])

    candidates = [
        Candidate(
            recipe_id=row.recipe_id,
            name=row.name,
            minutes=row.minutes,
            ingredients=ingredients.get(row.recipe_id, []),
            have_key=row.have_key,
            total_key=row.total_key,
            missing_key=missing_key_names(ingredients.get(row.recipe_id, []), set(covered)),
            coverage=row.have_key / row.total_key if row.total_key else 1.0,
            avg_rating=row.avg_rating,
            n_ratings=row.n_ratings,
            ingredient_score=row.ingredient_score,
            semantic_score=row.semantic_score,
            final_score=row.final_score if row.final_score is not None else row.ingredient_score,
            sugar_pdv=nutrition.get(row.recipe_id, {}).get("sugar_pdv"),
            sodium_pdv=nutrition.get(row.recipe_id, {}).get("sodium_pdv"),
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
    use_diversity: bool = False  # needs semantic (recipe vectors)
    use_rerank: bool = False
    use_matcher: bool = False  # pantry expansion + matcher-based verification
    usage_weight: float = 0.5  # weight of pantry usage in the ingredient score (0 = off)
    shortlist_size: int = 20  # verified recipes passed to diversity / rerank
    mmr_lambda: float = 0.7


@dataclass
class VerifiedCandidate:
    candidate: Candidate
    verification: VerificationResult


@dataclass
class FindResult:
    top: list[VerifiedCandidate]  # the best pass/adapt recipes, at most top_k
    checked: list[VerifiedCandidate]  # every verified candidate, failures included
    matched_recipes: int  # recipes with >= 1 pantry key ingredient that passed the filters

    @property
    def approved(self) -> list[VerifiedCandidate]:
        return [vc for vc in self.checked if vc.verification.status is not VerificationStatus.FAIL]

    @property
    def verifications(self) -> list[VerificationResult]:
        """All verification results, for the finder's feedback on a retry."""
        return [vc.verification for vc in self.checked]


def find_recipes(
    conn: sqlite3.Connection,
    query: RecipeQuery,
    options: SearchOptions | None = None,
    semantic: SemanticSearch | None = None,
    reranker: Reranker | None = None,
    verifier: Verifier | None = None,
    expander: PantryExpander | None = None,
) -> list[Candidate]:
    """The top verified candidates only (see find_verified for their verification)."""
    result = find_verified(conn, query, options, semantic, reranker, verifier, expander)
    return [vc.candidate for vc in result.top]


def find_verified(
    conn: sqlite3.Connection,
    query: RecipeQuery,
    options: SearchOptions | None = None,
    semantic: SemanticSearch | None = None,
    reranker: Reranker | None = None,
    verifier: Verifier | None = None,
    expander: PantryExpander | None = None,
) -> FindResult:
    """Full pipeline: search -> verify -> (diversity) -> (rerank) -> top k verified.

    Candidates with status pass or adapt are kept (adapt = works with a substitute or a
    smaller batch). Every candidate keeps its VerificationResult, so callers can show
    adaptations and turn failures into feedback."""
    options = options or SearchOptions()
    if (options.use_semantic or options.use_diversity) and semantic is None:
        raise ValueError("use_semantic / use_diversity need a SemanticSearch")
    if options.use_rerank and reranker is None:
        raise ValueError("use_rerank=True needs a Reranker")
    if options.use_matcher and (expander is None or verifier is None):
        raise ValueError("use_matcher=True needs a PantryExpander and a matcher Verifier")
    result = search(
        conn,
        query,
        limit=options.pool_size,
        semantic=semantic if options.use_semantic else None,
        expander=expander if options.use_matcher else None,
        usage_weight=options.usage_weight,
    )
    verifier = verifier if options.use_matcher and verifier else Verifier(conn)
    with span("search.verify", candidates=len(result.candidates)):
        results = verifier.verify_all(result.candidates, query)
    found = FindResult(
        top=[],
        checked=[VerifiedCandidate(c, v) for c, v in zip(result.candidates, results, strict=True)],
        matched_recipes=result.matched_recipes,
    )
    verification = {vc.candidate.recipe_id: vc.verification for vc in found.checked}
    shortlist = [vc.candidate for vc in found.approved[: options.shortlist_size]]

    if options.use_diversity and semantic is not None:
        with span("search.diversity", candidates=len(shortlist)):
            vectors = semantic.store.get_vectors([c.recipe_id for c in shortlist])
            shortlist = mmr(shortlist, vectors, k=len(shortlist), lambda_=options.mmr_lambda)
    if options.use_rerank and reranker is not None:
        with span("search.rerank", candidates=len(shortlist)):
            shortlist = reranker.rerank(query, shortlist, options.top_k)
    # The reranker returns copies (with rerank_reason); pair them with their verification.
    found.top = [
        VerifiedCandidate(c, verification[c.recipe_id]) for c in shortlist[: options.top_k]
    ]
    return found


def matching_from_settings(
    settings: Settings, conn: sqlite3.Connection, embedder: Embedder, state: sqlite3.Connection
) -> tuple[PantryExpander, Verifier]:
    """Pantry expansion and a verifier that share one CompositeMatcher; its cache lives in
    the state database (`state`), the recipes in `conn`."""
    from pathlib import Path

    from pantry_chef.agents.hidden_allergens import HiddenAllergenChecker
    from pantry_chef.db.state import import_legacy_match_cache
    from pantry_chef.ingredients.matcher import (
        CompositeMatcher,
        ExactMatcher,
        LLMMatcher,
        MatchCache,
        llm_cache_source,
        parents_from_seed,
    )
    from pantry_chef.ingredients.relations import load_seed
    from pantry_chef.llm.factory import create_llm
    from pantry_chef.search.semantic import ChromaNameIndex

    import_legacy_match_cache(state, settings.db_path)  # answers cached before state.db
    llm = create_llm(settings)
    llm_matcher = LLMMatcher(llm)
    matcher = CompositeMatcher(
        ExactMatcher(parents_from_seed(load_seed())),
        MatchCache(state, llm_cache_source(llm_matcher)),
        llm_matcher,
        log_path=Path("data/processed/match_log.jsonl"),
    )
    expander = PantryExpander(embedder, ChromaNameIndex(settings.chroma_path), matcher)
    hidden = HiddenAllergenChecker(llm, Path("data/processed/hidden_allergens.json"))
    return expander, Verifier(conn, matcher, hidden)
