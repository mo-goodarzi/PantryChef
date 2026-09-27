"""Turn verifier failures into a stricter RecipeQuery for the finder's next attempt."""

from collections import Counter
from dataclasses import dataclass

from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.verification import FailureCode, VerificationResult, VerificationStatus

# Ingredients that make a recipe unsafe or break a diet are excluded from the next search.
EXCLUDE_ON = {FailureCode.ALLERGEN, FailureCode.HIDDEN_ALLERGEN, FailureCode.DIET_VIOLATION}
# An ingredient the user lacks in this many failed candidates is excluded too.
MISSING_IN_AT_LEAST = 2


@dataclass
class Feedback:
    query: RecipeQuery  # the updated query for the next attempt
    reason_counts: dict[str, int]  # e.g. {"missing_ingredient": 7, "allergen": 1}
    excluded_ingredients: list[str]  # newly excluded


def build_feedback(query: RecipeQuery, results: list[VerificationResult]) -> Feedback:
    failed = [r for r in results if r.status is VerificationStatus.FAIL]
    reasons = [reason for r in failed for reason in r.reasons]

    missing = Counter(
        r.item for r in reasons if r.code is FailureCode.MISSING_INGREDIENT and r.item
    )
    to_exclude = {r.item for r in reasons if r.code in EXCLUDE_ON and r.item}
    to_exclude |= {item for item, n in missing.items() if n >= MISSING_IN_AT_LEAST}
    new = sorted(to_exclude - set(query.exclude_ingredients))

    updated = query.model_copy(
        update={
            "exclude_recipe_ids": sorted(
                set(query.exclude_recipe_ids) | {r.candidate_id for r in failed}
            ),
            "exclude_ingredients": sorted(set(query.exclude_ingredients) | to_exclude),
        }
    )
    counts = Counter(reason.code.value for reason in reasons)
    return Feedback(query=updated, reason_counts=dict(counts), excluded_ingredients=new)
