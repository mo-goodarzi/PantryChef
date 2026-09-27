"""Semantic match between the user's wish and recipes.

Two small interfaces keep the pieces swappable: an Embedder (text -> unit vectors) and a
RecipeVectorStore (recipe id -> vector, nearest neighbours). Defaults: bge-small-en-v1.5
running locally, stored in a persistent Chroma collection.
"""

from pathlib import Path
from typing import Protocol

import numpy as np

# bge v1.5 models expect this prefix on short search queries (not on documents).
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class RecipeVectorStore(Protocol):
    def add(self, ids: list[int], vectors: np.ndarray) -> None: ...

    def existing_ids(self) -> set[int]: ...

    def get_vectors(self, ids: list[int]) -> dict[int, np.ndarray]: ...

    def nearest(self, vector: np.ndarray, n: int) -> list[int]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str, device: str | None = None):
        import torch
        from sentence_transformers import SentenceTransformer

        if device is None:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = SentenceTransformer(model_name, device=device)
        self.query_prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, batch_size=128, normalize_embeddings=True)

    def embed_query(self, text: str) -> np.ndarray:
        return self.model.encode([self.query_prefix + text], normalize_embeddings=True)[0]


class ChromaRecipeStore:
    """Recipe vectors in a persistent Chroma collection (cosine distance)."""

    def __init__(self, path: Path, collection: str = "recipes"):
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        self.collection = client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, ids: list[int], vectors: np.ndarray) -> None:
        self.collection.upsert(ids=[str(i) for i in ids], embeddings=vectors.tolist())

    def existing_ids(self) -> set[int]:
        return {int(i) for i in self.collection.get(include=[])["ids"]}

    def get_vectors(self, ids: list[int]) -> dict[int, np.ndarray]:
        if not ids:
            return {}
        result = self.collection.get(ids=[str(i) for i in ids], include=["embeddings"])
        embeddings = result["embeddings"]
        if embeddings is None:
            return {}
        return {int(i): np.asarray(v) for i, v in zip(result["ids"], embeddings, strict=True)}

    def nearest(self, vector: np.ndarray, n: int) -> list[int]:
        result = self.collection.query(query_embeddings=[vector.tolist()], n_results=n, include=[])
        return [int(i) for i in result["ids"][0]]


def min_max(values: dict[int, float]) -> dict[int, float]:
    """Rescale to 0..1 within this candidate pool (all equal -> all 0.5)."""
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-9:
        return {k: 0.5 for k in values}
    return {k: (v - low) / (high - low) for k, v in values.items()}


def semantic_scores(query_vector: np.ndarray, vectors: dict[int, np.ndarray]) -> dict[int, float]:
    """Cosine similarity (vectors are unit length), rescaled to 0..1 within the pool."""
    return min_max({rid: float(np.dot(query_vector, v)) for rid, v in vectors.items()})


class ChromaNameIndex:
    """Nearest ingredient names (canonical) by embedding, for pantry expansion."""

    def __init__(self, path: Path, collection: str = "ingredients"):
        import chromadb

        client = chromadb.PersistentClient(path=str(path))
        self.collection = client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, names: list[str], vectors: np.ndarray) -> None:
        self.collection.upsert(ids=names, embeddings=vectors.tolist())

    def existing(self) -> set[str]:
        return set(self.collection.get(include=[])["ids"])

    def nearest(self, vector: np.ndarray, n: int) -> list[str]:
        result = self.collection.query(query_embeddings=[vector.tolist()], n_results=n, include=[])
        return list(result["ids"][0])
