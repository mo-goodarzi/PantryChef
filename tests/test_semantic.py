import numpy as np
import pytest

from pantry_chef.models.query import RecipeQuery
from pantry_chef.search.engine import SearchOptions, SemanticSearch, find_recipes, search
from pantry_chef.search.semantic import ChromaRecipeStore, min_max, semantic_scores
from pantry_chef.search.text import recipe_text

VOCAB = ["pancake", "waffle", "egg", "soup", "chicken", "salad", "sweet", "breakfast"]
PANCAKES, WAFFLES, POACHED_EGGS = 5170, 31750, 118761


def keyword_vector(text: str) -> np.ndarray:
    text = text.lower()
    v = np.array([text.count(word) for word in VOCAB], dtype=float) + 0.01
    return v / np.linalg.norm(v)


class KeywordEmbedder:
    def embed_documents(self, texts):
        return np.array([keyword_vector(t) for t in texts])

    def embed_query(self, text):
        return keyword_vector(text)


class MemoryStore:
    def __init__(self):
        self.vectors: dict[int, np.ndarray] = {}

    def add(self, ids, vectors):
        self.vectors.update(zip(ids, vectors, strict=True))

    def existing_ids(self):
        return set(self.vectors)

    def get_vectors(self, ids):
        return {i: self.vectors[i] for i in ids if i in self.vectors}

    def nearest(self, vector, n):
        ranked = sorted(self.vectors, key=lambda i: -float(np.dot(vector, self.vectors[i])))
        return ranked[:n]


@pytest.fixture
def semantic(enriched_conn):
    store = MemoryStore()
    rows = enriched_conn.execute("SELECT id, name FROM recipes").fetchall()
    store.add([r["id"] for r in rows], KeywordEmbedder().embed_documents([r["name"] for r in rows]))
    return SemanticSearch(embedder=KeywordEmbedder(), store=store, weight=0.3)


def test_min_max_rescales_within_pool():
    assert min_max({1: 0.2, 2: 0.6, 3: 0.4}) == pytest.approx({1: 0.0, 2: 1.0, 3: 0.5})
    assert min_max({1: 0.3, 2: 0.3}) == {1: 0.5, 2: 0.5}
    assert min_max({}) == {}


def test_semantic_scores_prefer_closer_vectors():
    query = keyword_vector("waffle")
    scores = semantic_scores(query, {1: keyword_vector("waffles"), 2: keyword_vector("soup")})
    assert scores[1] == 1.0 and scores[2] == 0.0


def test_recipe_text_drops_generic_tags_and_truncates():
    text = recipe_text("Toast", "x" * 500, ["time-to-make", "breakfast"])
    assert text.startswith("Toast. ")
    assert "time-to-make" not in text and "tags: breakfast" in text
    assert len(text) < 350


def test_wish_reorders_recipes_with_equal_coverage(enriched_conn, semantic):
    pantry = ["flour", "butter", "egg", "milk"]
    waffles = search(
        enriched_conn, RecipeQuery(ingredients=pantry, preferences_text="waffle"), semantic=semantic
    )
    pancakes = search(
        enriched_conn,
        RecipeQuery(ingredients=pantry, preferences_text="pancake"),
        semantic=semantic,
    )
    assert waffles.candidates[0].recipe_id == WAFFLES
    assert pancakes.candidates[0].recipe_id == PANCAKES
    assert waffles.candidates[0].semantic_score == 1.0


def test_final_score_mixes_ingredient_and_semantic(enriched_conn, semantic):
    result = search(
        enriched_conn, RecipeQuery(ingredients=["egg"], preferences_text="soup"), semantic=semantic
    )
    for c in result.candidates:
        assert c.final_score == pytest.approx(0.7 * c.ingredient_score + 0.3 * c.semantic_score)


def test_nearest_recipes_join_the_pool_beyond_coverage(enriched_conn, semantic):
    small_pool = SemanticSearch(
        semantic.embedder, semantic.store, weight=1.0, coverage_pool=1, neighbors=50
    )
    result = search(
        enriched_conn,
        RecipeQuery(ingredients=["egg"], preferences_text="waffle"),
        semantic=small_pool,
    )
    # waffles rank low on coverage for pantry "egg" but are the nearest match to "waffle"
    assert result.candidates[0].recipe_id == WAFFLES


def test_empty_wish_skips_semantic(enriched_conn, semantic):
    result = search(enriched_conn, RecipeQuery(ingredients=["egg"]), semantic=semantic)
    assert all(c.semantic_score is None for c in result.candidates)


def test_find_recipes_requires_semantic_when_enabled(enriched_conn):
    with pytest.raises(ValueError, match="SemanticSearch"):
        find_recipes(
            enriched_conn, RecipeQuery(ingredients=["egg"]), SearchOptions(use_semantic=True)
        )


def test_chroma_store_round_trip(tmp_path):
    store = ChromaRecipeStore(tmp_path / "chroma", collection="test")
    vectors = np.array([keyword_vector("pancake"), keyword_vector("soup")])
    store.add([1, 2], vectors)
    assert store.existing_ids() == {1, 2}
    assert np.allclose(store.get_vectors([2])[2], vectors[1])
    assert store.nearest(keyword_vector("pancakes"), 1) == [1]
