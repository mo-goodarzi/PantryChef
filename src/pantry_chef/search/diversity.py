"""Maximal Marginal Relevance: keep the top results relevant but not near-duplicates."""

import numpy as np

from pantry_chef.models.recipe import Candidate


def mmr(
    candidates: list[Candidate],
    vectors: dict[int, np.ndarray],
    k: int,
    lambda_: float = 0.7,
) -> list[Candidate]:
    """Pick k candidates one by one, maximizing
    lambda * relevance - (1 - lambda) * (max similarity to anything already picked).

    Relevance is final_score rescaled to 0..1 within the list; similarity is cosine
    between recipe embeddings. Candidates without a vector count as dissimilar.
    """
    if not candidates:
        return []
    scores = [c.final_score for c in candidates]
    low, high = min(scores), max(scores)
    relevance = {
        c.recipe_id: (c.final_score - low) / (high - low) if high > low else 1.0 for c in candidates
    }

    remaining = list(candidates)
    picked: list[Candidate] = []
    while remaining and len(picked) < k:

        def mmr_score(c: Candidate) -> float:
            v = vectors.get(c.recipe_id)
            max_sim = max(
                (
                    float(np.dot(v, vectors[p.recipe_id]))
                    for p in picked
                    if v is not None and p.recipe_id in vectors
                ),
                default=0.0,
            )
            return lambda_ * relevance[c.recipe_id] - (1 - lambda_) * max_sim

        best = max(remaining, key=mmr_score)  # ties keep the earlier (better ranked) one
        picked.append(best)
        remaining.remove(best)
    return picked
