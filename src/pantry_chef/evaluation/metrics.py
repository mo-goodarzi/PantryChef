"""Ranking metrics and hard-rule checks for search evaluation.

A result is "good" when it passes the hard rules (checked by code) AND the judge rates
its preference fit >= GOOD_SCORE.
"""

from pantry_chef.agents.verifier import check_allergens, check_diet, check_ingredients
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate

GOOD_SCORE = 4
MAX_MISSING_KEY = 1  # a result may miss at most one key ingredient to count as makeable


def hard_rule_failures(candidate: Candidate, query: RecipeQuery) -> list[str]:
    """Reasons the candidate breaks a hard rule (empty list = passes)."""
    failures = []
    allergens = check_allergens(candidate, set(query.required_allergen_free))
    failures += [str(r) for r in allergens.reasons]
    failures += [str(r) for r in check_diet(candidate, set(query.diets)).reasons]
    missing = check_ingredients(candidate, {normalize(n) for n in query.ingredients}).reasons
    if len(missing) > MAX_MISSING_KEY:
        failures.append(f"missing {len(missing)} key ingredients")
    if query.max_minutes is not None and candidate.minutes > query.max_minutes:
        failures.append(f"too_long: {candidate.minutes} min")
    return failures


def has_allergen_violation(candidate: Candidate, query: RecipeQuery) -> bool:
    return not check_allergens(candidate, set(query.required_allergen_free)).passed


def hit_at_k(relevant: list[bool], k: int = 5) -> float:
    """1.0 if any of the first k results is relevant."""
    return 1.0 if any(relevant[:k]) else 0.0


def reciprocal_rank(relevant: list[bool], k: int = 5) -> float:
    """1 / rank of the first relevant result within the first k, else 0."""
    for rank, is_relevant in enumerate(relevant[:k], start=1):
        if is_relevant:
            return 1.0 / rank
    return 0.0


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100) of a non-empty list."""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(p / 100 * len(ordered) + 0.5) - 1))
    return ordered[index]
