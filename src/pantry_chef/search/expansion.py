"""Pantry expansion: which recipe ingredient names can the user's items cover?

"pasta" in the pantry should also find recipes that say "spaghetti"; "rice" should find
"cooked rice". For each pantry item we take the nearest ingredient names by embedding
(cheap candidate generation), then let the matcher decide (exact/hierarchy/cache/LLM).
Only matches that count as available are added to the search.
"""

from typing import Protocol

import numpy as np

from pantry_chef.ingredients.matcher import Matcher
from pantry_chef.models.matching import MatchResult
from pantry_chef.search.semantic import Embedder


class NameIndex(Protocol):
    def nearest(self, vector: np.ndarray, n: int) -> list[str]: ...


class PantryExpander:
    def __init__(self, embedder: Embedder, index: NameIndex, matcher: Matcher, neighbors: int = 15):
        self.embedder = embedder
        self.index = index
        self.matcher = matcher
        self.neighbors = neighbors

    def expand(self, pantry: list[str]) -> dict[str, MatchResult]:
        """Recipe ingredient name -> match, for every name some pantry item can cover.

        Ingredient names are embedded as documents (no query prefix): we compare
        names with names.
        """
        if not pantry:
            return {}
        vectors = self.embedder.embed_documents(pantry)
        candidates = sorted(
            {name for v in vectors for name in self.index.nearest(v, self.neighbors)} - set(pantry)
        )
        results = self.matcher.match(pantry, candidates)
        return {r.recipe_term: r for r in results if r.label.available}
