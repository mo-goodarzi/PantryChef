"""Build the real conversation (models, databases, search pipeline) from Settings."""

import sqlite3
from dataclasses import dataclass, field

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph

from pantry_chef.agents.allergy_review import AllergyReviewer
from pantry_chef.agents.wish_fit import WishFitChecker
from pantry_chef.config import Settings
from pantry_chef.db.connection import connect
from pantry_chef.db.repository import load_image_url, load_servings, load_steps
from pantry_chef.db.state import ProfileStore, open_state_db
from pantry_chef.graph.builder import build_graph
from pantry_chef.graph.nodes import ChatDeps
from pantry_chef.llm.factory import create_llm
from pantry_chef.models.query import PantryItem, RecipeQuery
from pantry_chef.observability import flush_tracing, tracing_from_settings
from pantry_chef.search.engine import (
    FindResult,
    SearchOptions,
    find_verified,
    matching_from_settings,
    semantic_from_settings,
)
from pantry_chef.search.rerank import LLMReranker


@dataclass
class ChatApp:
    graph: CompiledStateGraph
    profiles: ProfileStore | None = None
    connections: list[sqlite3.Connection] = field(default_factory=list)

    def close(self) -> None:
        flush_tracing()
        for conn in self.connections:
            conn.close()


def chat_from_settings(
    settings: Settings,
    threaded: bool = False,
    options: SearchOptions | None = None,
    final_allergen_check: bool = True,
) -> ChatApp:
    """The measured best pipeline: semantic + matcher + pantry usage + LLM rerank.

    threaded=True (the API) opens connections that worker threads may share; the caller
    must serialize graph calls. `options` and `final_allergen_check` change the pipeline
    for the eval's comparisons only; the app always uses the defaults.
    """
    for path, how in [
        (settings.db_path, "scripts/build_db.py and scripts/enrich_db.py"),
        (settings.chroma_path, "scripts/build_embeddings.py"),
    ]:
        if not path.exists():
            # Without embeddings there is no matcher, and so no hidden-allergen check:
            # refuse rather than run with a safety layer missing.
            raise FileNotFoundError(f"{path} is missing; build it with {how}")

    tracing_from_settings(settings)
    conn = connect(settings.db_path, check_same_thread=not threaded)
    state = open_state_db(settings.state_db_path, check_same_thread=not threaded)
    checkpoints = sqlite3.connect(settings.state_db_path, check_same_thread=False)

    llm = create_llm(settings)
    semantic = semantic_from_settings(settings)
    expander, verifier = matching_from_settings(settings, conn, semantic.embedder, state)
    reranker = LLMReranker(llm, conn)
    review_settings = settings.model_copy(update={"llm_model": settings.allergy_review_model})
    review_llm = create_llm(review_settings)
    allergy_reviewer = AllergyReviewer(
        review_llm, conn, cache_path=settings.db_path.parent / "allergy_reviews.json"
    )
    wish_llm = create_llm(settings.model_copy(update={"llm_model": settings.wish_fit_model}))
    wish_checker = WishFitChecker(
        wish_llm, conn, cache_path=settings.state_db_path.parent / "wish_fit.json"
    )
    options = options or SearchOptions(
        use_semantic=True,
        use_matcher=True,
        use_rerank=True,
        use_wish_fit=settings.wish_fit_enabled,
        usage_weight=settings.usage_weight,
    )

    def find(query: RecipeQuery, pantry_items: list[PantryItem]) -> FindResult:
        return find_verified(
            conn,
            query,
            options,
            semantic,
            reranker,
            verifier,
            expander,
            allergy_reviewer,
            wish_checker,
            pantry_items,
        )

    profiles = ProfileStore(state)
    deps = ChatDeps(
        llm=llm,
        find=find,
        reverify=verifier.verify_all,
        steps=lambda recipe_id: load_steps(conn, recipe_id),
        servings=lambda recipe_id: load_servings(conn, recipe_id),
        image=lambda recipe_id: load_image_url(conn, recipe_id),
        profiles=profiles,
        quantity_question=settings.quantity_question,
        final_allergen_check=final_allergen_check,
    )
    graph = build_graph(deps, SqliteSaver(checkpoints))
    return ChatApp(graph=graph, profiles=profiles, connections=[conn, state, checkpoints])
