"""How a user's pantry item relates to a recipe ingredient."""

from enum import StrEnum

from pydantic import BaseModel


class MatchLabel(StrEnum):
    """Relation of the USER's item to the RECIPE's ingredient (direction matters)."""

    SAME = "same"  # the same ingredient, or a more specific kind of it (cheddar -> cheese)
    CONTAINS = "contains"  # the user's item provides it (eggs -> egg whites, rice -> cooked rice)
    SUBSTITUTE = "substitute"  # can replace it in most recipes (pasta -> spaghetti)
    DIFFERENT = "different"  # cannot be used for it

    @property
    def available(self) -> bool:
        """Counts as having the ingredient (substitutes count too, but as an adaptation)."""
        return self is not MatchLabel.DIFFERENT


# Best first: when several pantry items relate to one recipe ingredient, keep the best.
LABEL_PRIORITY = [MatchLabel.SAME, MatchLabel.CONTAINS, MatchLabel.SUBSTITUTE, MatchLabel.DIFFERENT]


class MatchResult(BaseModel):
    recipe_term: str
    user_term: str | None  # None when nothing in the pantry matches
    label: MatchLabel
    source: str  # "exact" | "hierarchy" | "cache" | "llm" | "none"


def best_match(results: list[MatchResult]) -> MatchResult:
    return min(results, key=lambda r: LABEL_PRIORITY.index(r.label))
