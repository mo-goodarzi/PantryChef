"""Measure the two text-reading steps on hand-written cases: the safety intake (allergies,
diets, health) and request parsing (pantry, wish, time, goals, allergies, avoid).

Scored on what code builds from the LLM's answer (the profile and the query), since that
is what the rest of the system uses. A missed allergen code is the one critical mistake;
extra codes are over-strict (safe, but still counted as a mistake).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pantry_chef.agents.finder import build_query
from pantry_chef.agents.safety import SafetyIntake
from pantry_chef.ingredients.normalize import normalize
from pantry_chef.llm.usage import PRICES, cost_usd  # noqa: F401  (re-exported for the runner)
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import RecipeQuery


@dataclass
class IntakeCase:
    id: str
    answer: str
    allergens: list[str] = field(default_factory=list)
    other_allergies: list[str] = field(default_factory=list)
    diets: list[str] = field(default_factory=list)
    health_diets: list[str] = field(default_factory=list)
    health_not_covered: bool = False


@dataclass
class RequestCase:
    id: str
    message: str
    pantry: list[str] = field(default_factory=list)
    wish_words: list[str] = field(default_factory=list)  # each must appear in the wish
    max_minutes: int | None = None
    goals: list[str] = field(default_factory=list)
    allergens: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    allowed_extra_avoid: list[str] = field(default_factory=list)  # allergy words, stricter


@dataclass
class CaseResult:
    case_id: str
    kind: str  # intake | request
    mistakes: list[str] = field(default_factory=list)  # e.g. "pantry missing: milk"
    allergens_missed: list[str] = field(default_factory=list)  # critical
    failed: bool = False  # the LLM call raised (no valid structured answer)

    @property
    def passed(self) -> bool:
        return not self.failed and not self.mistakes and not self.allergens_missed


def load_parsing_cases(path: Path) -> tuple[list[IntakeCase], list[RequestCase]]:
    data = json.loads(path.read_text())
    return (
        [IntakeCase(**c) for c in data["intake"]],
        [RequestCase(**c) for c in data["requests"]],
    )


def same_item(a: str, b: str) -> bool:
    """'eggs' = 'egg', 'rice' = 'leftover rice': normalized names, or one inside the other."""
    a, b = normalize(a), normalize(b)
    return a == b or f" {a} " in f" {b} " or f" {b} " in f" {a} "


def compare_items(label: str, expected: list[str], got: list[str]) -> list[str]:
    missing = [e for e in expected if not any(same_item(e, g) for g in got)]
    extra = [g for g in got if not any(same_item(e, g) for e in expected)]
    return [f"{label} missing: {m}" for m in missing] + [f"{label} extra: {x}" for x in extra]


def compare_sets(label: str, expected: list[str], got: list[str]) -> list[str]:
    if set(expected) == set(got):
        return []
    return [f"{label}: expected {sorted(set(expected))}, got {sorted(set(got))}"]


def allergen_mistakes(expected: list[str], got: list[str]) -> tuple[list[str], list[str]]:
    """(missed codes, mistakes for extra codes)."""
    missed = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    return missed, [f"allergens extra: {extra}"] if extra else []


def score_intake(case: IntakeCase, intake: SafetyIntake) -> CaseResult:
    profile = intake.profile
    missed, mistakes = allergen_mistakes(case.allergens, [a.value for a in profile.allergens])
    mistakes += compare_items("other allergies", case.other_allergies, profile.other_allergies)
    mistakes += compare_sets("diets", case.diets, [d.value for d in profile.diets])
    mistakes += compare_sets(
        "health diets", case.health_diets, [d.value for d in profile.health_diets]
    )
    if intake.health_not_covered != case.health_not_covered:
        mistakes.append(f"health_not_covered: expected {case.health_not_covered}")
    return CaseResult(case.id, "intake", mistakes, missed)


def score_request(case: RequestCase, query: RecipeQuery) -> CaseResult:
    got_codes = [a.value for a in query.required_allergen_free]
    missed, mistakes = allergen_mistakes(case.allergens, got_codes)
    mistakes += compare_items("pantry", case.pantry, query.ingredients)
    wish = query.preferences_text.lower()
    mistakes += [f"wish lacks: {w}" for w in case.wish_words if w.lower() not in wish]
    if query.max_minutes != case.max_minutes:
        mistakes.append(f"max_minutes: expected {case.max_minutes}, got {query.max_minutes}")
    mistakes += compare_sets("goals", case.goals, [g.value for g in query.nutrition_goals])
    allowed = case.avoid + case.allowed_extra_avoid
    mistakes += [
        m
        for m in compare_items("avoid", allowed, query.exclude_ingredients)
        if not any(m == f"avoid missing: {extra}" for extra in case.allowed_extra_avoid)
    ]
    return CaseResult(case.id, "request", mistakes, missed)


def request_query(answer) -> RecipeQuery:
    """The query code builds from a parsed message for a user with no saved profile."""
    return build_query(UserProfile(), answer)


def summarize(results: list[CaseResult]) -> dict:
    def rate(kind: str | None) -> float | None:
        chosen = [r for r in results if kind is None or r.kind == kind]
        return sum(r.passed for r in chosen) / len(chosen) if chosen else None

    return {
        "cases": len(results),
        "passed": rate(None),
        "intake_passed": rate("intake"),
        "request_passed": rate("request"),
        # safety: a code the user said and the profile or query does not carry
        "allergens_missed": sum(len(r.allergens_missed) for r in results),
        "failed_calls": sum(r.failed for r in results),
    }
