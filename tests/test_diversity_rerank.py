import json

import numpy as np
import pytest

from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.search.diversity import mmr
from pantry_chef.search.engine import SearchOptions, SemanticSearch, find_recipes
from pantry_chef.search.rerank import LLMReranker, RankedPick, RerankResult, apply_picks

from .test_semantic import KeywordEmbedder, MemoryStore


def cand(recipe_id, score):
    return Candidate(
        recipe_id=recipe_id,
        name=f"r{recipe_id}",
        minutes=10,
        have_key=1,
        total_key=1,
        coverage=1,
        ingredient_score=score,
        final_score=score,
    )


def unit(*values):
    v = np.array(values, dtype=float)
    return v / np.linalg.norm(v)


# --- diversity -----------------------------------------------------------------------


def test_mmr_skips_a_near_duplicate():
    # 4 is a weak candidate so that 3 keeps a high relevance after 0..1 rescaling
    candidates = [cand(1, 1.0), cand(2, 0.99), cand(3, 0.95), cand(4, 0.5)]
    vectors = {1: unit(1, 0), 2: unit(1, 0.01), 3: unit(0, 1), 4: unit(1, 1)}  # 2 ~ 1
    assert [c.recipe_id for c in mmr(candidates, vectors, k=2)] == [1, 3]


def test_mmr_with_lambda_one_keeps_relevance_order():
    candidates = [cand(1, 1.0), cand(2, 0.99), cand(3, 0.9)]
    vectors = {1: unit(1, 0), 2: unit(1, 0.01), 3: unit(0, 1)}
    assert [c.recipe_id for c in mmr(candidates, vectors, k=3, lambda_=1.0)] == [1, 2, 3]


def test_mmr_handles_missing_vectors_and_small_lists():
    assert mmr([], {}, k=5) == []
    assert [c.recipe_id for c in mmr([cand(1, 0.5), cand(2, 0.5)], {}, k=5)] == [1, 2]


# --- rerank --------------------------------------------------------------------------


def test_apply_picks_orders_and_fills_up():
    candidates = [cand(1, 1), cand(2, 1), cand(3, 1), cand(4, 1)]
    picks = [
        RankedPick(recipe_id=3, reason="best"),
        RankedPick(recipe_id=99, reason="made up"),
        RankedPick(recipe_id=3, reason="again"),
        RankedPick(recipe_id=1, reason="ok"),
    ]
    result = apply_picks(candidates, picks, k=3)
    assert [c.recipe_id for c in result] == [3, 1, 2]
    assert result[0].rerank_reason == "best" and result[2].rerank_reason is None


class FakeRerankLLM:
    def __init__(self, order):
        self.order = order
        self.seen = None

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "rerank" and schema is RerankResult
        self.seen = json.loads(variables["recipes"])
        assert variables["k"] == "2"
        return RerankResult(picks=[RankedPick(recipe_id=i, reason=f"pick {i}") for i in self.order])


def test_llm_reranker_can_only_choose_verified_candidates(enriched_conn):
    llm = FakeRerankLLM(order=[31750, 424242])  # 424242 was never a candidate
    reranker = LLMReranker(llm, enriched_conn)
    candidates = [cand(5170, 1.0), cand(31750, 1.0), cand(118761, 0.9)]
    result = reranker.rerank(
        RecipeQuery(ingredients=["egg"], preferences_text="waffles"), candidates, k=2
    )
    assert [c.recipe_id for c in result] == [31750, 5170]
    assert {r["recipe_id"] for r in llm.seen} == {5170, 31750, 118761}
    assert all("missing_key" in r for r in llm.seen)


# --- pipeline ------------------------------------------------------------------------


@pytest.fixture
def semantic(enriched_conn):
    store = MemoryStore()
    rows = enriched_conn.execute("SELECT id, name FROM recipes").fetchall()
    store.add([r["id"] for r in rows], KeywordEmbedder().embed_documents([r["name"] for r in rows]))
    return SemanticSearch(embedder=KeywordEmbedder(), store=store)


def test_full_pipeline_with_rerank(enriched_conn, semantic):
    llm = FakeRerankLLM(order=[118761])
    options = SearchOptions(use_semantic=True, use_diversity=True, use_rerank=True, top_k=2)
    query = RecipeQuery(ingredients=["flour", "butter", "egg", "milk"], preferences_text="eggs")
    result = find_recipes(
        enriched_conn, query, options, semantic=semantic, reranker=LLMReranker(llm, enriched_conn)
    )
    assert result[0].recipe_id == 118761 and result[0].rerank_reason == "pick 118761"
    assert len(result) == 2


def test_rerank_option_requires_a_reranker(enriched_conn):
    with pytest.raises(ValueError, match="Reranker"):
        find_recipes(
            enriched_conn, RecipeQuery(ingredients=["egg"]), SearchOptions(use_rerank=True)
        )
