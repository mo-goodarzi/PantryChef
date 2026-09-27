"""Verifier (second safety layer): pass / adapt / fail with machine-readable reasons.

Every check is a pure function over the candidate and a VerificationContext. The slow
parts (ingredient matching, hidden-allergen lookup, substitutes) are computed ONCE per
search for all candidates by Verifier.prepare(), then each candidate is checked.

Allergen and diet checks use ingredient-level data (own + inherited allergens), independent
of the SQL filter; the hidden-allergen check can only add failures. Decision: any failed
check -> fail; otherwise any adaptation (substitute, scaling) -> adapt; otherwise pass.
"""

import sqlite3
from dataclasses import dataclass, field

from pantry_chef.agents.hidden_allergens import HiddenAllergenChecker, is_compound
from pantry_chef.db.repository import SubstituteOption, load_substitutes
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.matcher import ExactMatcher, Matcher
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.ingredients.quantities import Amount, available_ratio
from pantry_chef.models.matching import MatchLabel, MatchResult
from pantry_chef.models.query import AmountStatus, Diet, PantryItem, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import (
    CheckResult,
    FailureCode,
    FailureReason,
    VerificationResult,
    VerificationStatus,
)

SCALE_DOWN_LIMIT = 0.5  # with at least half of an ingredient, scale the recipe down


@dataclass
class VerificationContext:
    """Precomputed facts shared by all candidates of one search."""

    pantry: set[str]  # canonical names
    matches: dict[str, MatchResult] = field(default_factory=dict)  # by recipe canonical
    pantry_items: dict[str, PantryItem] = field(default_factory=dict)  # by canonical
    hidden: dict[str, set[Allergen]] = field(default_factory=dict)  # by ingredient name
    substitutes: dict[str, list[SubstituteOption]] = field(default_factory=dict)


def needs_match(ingredient: RecipeIngredient) -> bool:
    return ingredient.is_key and not ingredient.is_staple and not ingredient.is_optional


def match_for(ingredient: RecipeIngredient, context: VerificationContext) -> MatchResult:
    """The match for a key ingredient; exact pantry membership if nothing was prepared."""
    found = context.matches.get(ingredient.canonical_name)
    if found is not None:
        return found
    if ingredient.canonical_name in context.pantry:
        return MatchResult(
            recipe_term=ingredient.canonical_name,
            user_term=ingredient.canonical_name,
            label=MatchLabel.SAME,
            source="exact",
        )
    return MatchResult(
        recipe_term=ingredient.canonical_name,
        user_term=None,
        label=MatchLabel.DIFFERENT,
        source="none",
    )


# --- checks --------------------------------------------------------------------------


def check_ingredients(
    candidate: Candidate, pantry: set[str], context: VerificationContext | None = None
) -> CheckResult:
    """Fail on every key ingredient the pantry cannot cover (staples are always there);
    a covering substitute is an adaptation."""
    context = context or VerificationContext(pantry=pantry)
    reasons, adaptations = [], []
    for ingredient in candidate.ingredients:
        if not needs_match(ingredient):
            continue
        match = match_for(ingredient, context)
        if match.label is MatchLabel.DIFFERENT:
            reasons.append(
                FailureReason(
                    code=FailureCode.MISSING_INGREDIENT,
                    item=ingredient.canonical_name,
                    detail=f"recipe needs {ingredient.name!r}, not in pantry",
                )
            )
        elif match.label is MatchLabel.SUBSTITUTE:
            adaptations.append(f"use your {match.user_term} instead of {ingredient.name}")
    return CheckResult(
        check="ingredients", passed=not reasons, reasons=reasons, adaptations=adaptations
    )


def check_quantities(candidate: Candidate, context: VerificationContext) -> CheckResult:
    """Only key ingredients whose quantity matters and where both amounts are known.
    Unknown or "plenty" is fine; >= 50% available -> scale the recipe down; less -> fail."""
    reasons, notes, ratios = [], [], []
    for ingredient in candidate.ingredients:
        if not (needs_match(ingredient) and ingredient.quantity_matters):
            continue
        match = match_for(ingredient, context)
        item = context.pantry_items.get(match.user_term or "")
        if item is None or item.amount_status is not AmountStatus.KNOWN or item.quantity is None:
            continue
        if ingredient.quantity is None:
            notes.append(f"recipe amount of {ingredient.name} unknown")
            continue
        ratio = available_ratio(
            Amount(item.quantity, item.unit), Amount(ingredient.quantity, ingredient.unit)
        )
        if ratio is None:
            notes.append(
                f"cannot compare {item.unit or 'count'} with "
                f"{ingredient.unit or 'count'} for {ingredient.name}"
            )
        elif ratio < SCALE_DOWN_LIMIT:
            reasons.append(
                FailureReason(
                    code=FailureCode.INSUFFICIENT_QUANTITY,
                    item=ingredient.canonical_name,
                    detail=f"have {item.quantity:g} {item.unit or ''}, recipe needs "
                    f"{ingredient.quantity:g} {ingredient.unit or ''}".replace("  ", " "),
                )
            )
        elif ratio < 1:
            ratios.append((ratio, ingredient.name))
    adaptations = []
    if ratios and not reasons:
        ratio, name = min(ratios)
        adaptations.append(f"make {ratio:.0%} of the recipe (limited by {name})")
    return CheckResult(
        check="quantities",
        passed=not reasons,
        reasons=reasons,
        adaptations=adaptations,
        notes=notes,
    )


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


def check_hidden_allergens(
    candidate: Candidate, allergens: set[Allergen], context: VerificationContext
) -> CheckResult:
    """Allergens the second look found in compound ingredients that the labels missed."""
    reasons = [
        FailureReason(
            code=FailureCode.HIDDEN_ALLERGEN,
            item=ingredient.canonical_name,
            detail=f"{ingredient.name!r} may contain {allergen.value}",
        )
        for ingredient in candidate.ingredients
        for allergen in sorted(
            (context.hidden.get(ingredient.name, set()) & allergens) - set(ingredient.allergens)
        )
    ]
    return CheckResult(check="hidden_allergens", passed=not reasons, reasons=reasons)


def breaks_diet(ingredient: RecipeIngredient | SubstituteOption, diet: Diet) -> bool:
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


def check_time(candidate: Candidate, max_minutes: int | None) -> CheckResult:
    if max_minutes is None or candidate.minutes <= max_minutes:
        return CheckResult(check="time", passed=True)
    reason = FailureReason(
        code=FailureCode.TOO_LONG,
        item=str(candidate.minutes),
        detail=f"takes {candidate.minutes} min, limit {max_minutes}",
    )
    return CheckResult(check="time", passed=False, reasons=[reason])


def suggest_substitutions(
    candidate: Candidate, query: RecipeQuery, context: VerificationContext
) -> CheckResult:
    """For missing non-key ingredients, suggest a substitute the user HAS, but only if it
    passes the user's allergens and diets (a substitute can change them)."""
    allergens, diets = set(query.required_allergen_free), set(query.diets)
    adaptations, notes = [], []
    for ingredient in candidate.ingredients:
        missing = (
            not ingredient.is_key
            and not ingredient.is_staple
            and ingredient.canonical_name not in context.pantry
        )
        if not missing:
            continue
        safe = [
            option
            for option in context.substitutes.get(ingredient.canonical_name, [])
            if option.name in context.pantry
            and not set(option.allergens) & allergens
            and not any(breaks_diet(option, d) for d in diets)
        ]
        if safe:
            note = f" ({safe[0].note})" if safe[0].note else ""
            adaptations.append(f"use your {safe[0].name} instead of {ingredient.name}{note}")
        else:
            notes.append(f"also needs {ingredient.name}")
    return CheckResult(check="substitutions", passed=True, adaptations=adaptations, notes=notes)


# --- decision ------------------------------------------------------------------------


def ingredient_status(candidate: Candidate, context: VerificationContext) -> dict[str, str]:
    status = {}
    for ingredient in candidate.ingredients:
        if ingredient.is_staple:
            status[ingredient.name] = "staple"
        elif ingredient.is_optional:
            status[ingredient.name] = "optional"
        elif ingredient.is_key:
            label = match_for(ingredient, context).label
            status[ingredient.name] = {
                MatchLabel.DIFFERENT: "missing",
                MatchLabel.SUBSTITUTE: "substitute",
            }.get(label, "available")
        else:
            available = ingredient.canonical_name in context.pantry
            status[ingredient.name] = "available" if available else "extra"
    return status


def verify(
    candidate: Candidate, query: RecipeQuery, context: VerificationContext | None = None
) -> VerificationResult:
    """Run every check; fail if any fails, adapt if any adaptation is needed, else pass."""
    if context is None:
        context = VerificationContext(pantry={normalize(n) for n in query.ingredients})
    allergens = set(query.required_allergen_free)
    checks = [
        check_ingredients(candidate, context.pantry, context),
        check_quantities(candidate, context),
        check_allergens(candidate, allergens),
        check_hidden_allergens(candidate, allergens, context),
        check_diet(candidate, set(query.diets)),
        check_time(candidate, query.max_minutes),
        suggest_substitutions(candidate, query, context),
    ]
    adaptations = [a for c in checks for a in c.adaptations]
    if not all(c.passed for c in checks):
        status = VerificationStatus.FAIL
    elif adaptations:
        status = VerificationStatus.ADAPT
    else:
        status = VerificationStatus.PASS
    return VerificationResult(
        candidate_id=candidate.recipe_id,
        status=status,
        checks=checks,
        adaptations=adaptations,
        notes=[n for c in checks for n in c.notes],
        ingredient_status=ingredient_status(candidate, context),
    )


class Verifier:
    """Prepares the shared context for a batch of candidates (one matcher call, one
    hidden-allergen call, one substitutes query), then verifies each one."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        matcher: Matcher | None = None,
        hidden_checker: HiddenAllergenChecker | None = None,
    ):
        self.conn = conn
        self.matcher = matcher or ExactMatcher()
        self.hidden_checker = hidden_checker

    def prepare(
        self,
        candidates: list[Candidate],
        query: RecipeQuery,
        pantry_items: list[PantryItem] | None = None,
    ) -> VerificationContext:
        items = {item.canonical_name: item for item in pantry_items or []}
        pantry = {normalize(n) for n in query.ingredients if n.strip()} | set(items)
        ingredients = [i for c in candidates for i in c.ingredients]

        key_terms = sorted({i.canonical_name for i in ingredients if needs_match(i)})
        matches = {m.recipe_term: m for m in self.matcher.match(sorted(pantry), key_terms)}

        hidden: dict[str, set[Allergen]] = {}
        if self.hidden_checker is not None and query.required_allergen_free:
            compound = sorted({i.name for i in ingredients if is_compound(i)})
            hidden = self.hidden_checker.check(compound)

        missing_extra = sorted(
            {
                i.canonical_name
                for i in ingredients
                if not i.is_key and not i.is_staple and i.canonical_name not in pantry
            }
        )
        return VerificationContext(
            pantry=pantry,
            matches=matches,
            pantry_items=items,
            hidden=hidden,
            substitutes=load_substitutes(self.conn, missing_extra),
        )

    def verify_all(
        self,
        candidates: list[Candidate],
        query: RecipeQuery,
        pantry_items: list[PantryItem] | None = None,
    ) -> list[VerificationResult]:
        context = self.prepare(candidates, query, pantry_items)
        return [verify(c, query, context) for c in candidates]
