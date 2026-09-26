"""Deterministic verifier (second safety layer).

Each check is a pure function returning a CheckResult with machine-readable reasons.
The allergen and diet checks use ingredient-level data (including allergens inherited
from parent and contained ingredients), independent of the SQL filter, so a mistake in
one layer is caught by the other.

Phase 3 checks: ingredients, allergens, diet. Quantities, hidden allergens (LLM),
preferences and substitutions are added in Phase 5a.
"""

from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.models.query import Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import (
    CheckResult,
    FailureCode,
    FailureReason,
    VerificationResult,
    VerificationStatus,
)


def check_ingredients(candidate: Candidate, pantry: set[str]) -> CheckResult:
    """Fail on every key ingredient the user does not have (staples are always available)."""
    reasons = [
        FailureReason(
            code=FailureCode.MISSING_INGREDIENT,
            item=ingredient.canonical_name,
            detail=f"recipe needs {ingredient.name!r}, not in pantry",
        )
        for ingredient in candidate.ingredients
        if ingredient.is_key
        and not ingredient.is_staple
        and not ingredient.is_optional
        and ingredient.canonical_name not in pantry
    ]
    return CheckResult(check="ingredients", passed=not reasons, reasons=reasons)


def check_allergens(candidate: Candidate, allergens: set[Allergen]) -> CheckResult:
    """Fail on every ingredient that contains one of the user's allergens."""
    reasons = [
        FailureReason(
            code=FailureCode.ALLERGEN,
            item=ingredient.canonical_name,
            detail=f"{ingredient.name!r} contains {allergen.value}",
        )
        for ingredient in candidate.ingredients
        for allergen in sorted(set(ingredient.allergens) & allergens)
    ]
    return CheckResult(check="allergens", passed=not reasons, reasons=reasons)


def breaks_diet(ingredient: RecipeIngredient, diet: Diet) -> bool:
    if diet is Diet.VEGETARIAN:
        return ingredient.contains_meat or ingredient.contains_fish
    if diet is Diet.VEGAN:
        return ingredient.animal_product or ingredient.contains_meat or ingredient.contains_fish
    if diet is Diet.GLUTEN_FREE:
        return Allergen.GLUTEN in ingredient.allergens
    raise ValueError(f"unsupported diet: {diet}")


def check_diet(candidate: Candidate, diets: set[Diet]) -> CheckResult:
    reasons = [
        FailureReason(
            code=FailureCode.DIET_VIOLATION,
            item=ingredient.canonical_name,
            detail=f"{ingredient.name!r} is not {diet.value.replace('_', '-')}",
        )
        for ingredient in candidate.ingredients
        for diet in sorted(diets)
        if breaks_diet(ingredient, diet)
    ]
    return CheckResult(check="diet", passed=not reasons, reasons=reasons)


def verify(candidate: Candidate, query: RecipeQuery) -> VerificationResult:
    """Run all checks; the candidate passes only if every check passes."""
    pantry = {normalize(name) for name in query.ingredients}
    checks = [
        check_ingredients(candidate, pantry),
        check_allergens(candidate, set(query.required_allergen_free)),
        check_diet(candidate, set(query.diets)),
    ]
    status = VerificationStatus.PASS if all(c.passed for c in checks) else VerificationStatus.FAIL
    return VerificationResult(candidate_id=candidate.recipe_id, status=status, checks=checks)
