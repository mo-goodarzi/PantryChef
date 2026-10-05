"""Final allergy review: an LLM reads the WHOLE recipe (steps too) for the user's
allergies, after the code checks passed.

Earlier layers (SQL filter, verifier, hidden-allergen check) only see ingredient lists,
but recipes also add allergens in the steps ("suggested condiments include peanuts").
The review can only make results safer, and the decision rules live in code:
- unsafe (allergen is a required part of the dish) -> removed
- optional (only a garnish, variation or substitution) -> kept with "leave it out"
- uncertain (a product that often contains it, e.g. curry paste) -> kept with "check the label"
- safe -> kept
- no verdict for a recipe -> removed (we never show what was not reviewed)
A keyword scan of the steps (code) runs first; its hits are passed to the LLM as hints,
and become warnings on their own when no reviewer is configured.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from pantry_chef.db.repository import load_steps
from pantry_chef.ingredients.allergens import detect_allergens, mentions_word
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.verification import FailureCode, FailureReason
from pantry_chef.observability import get_logger, score

log = get_logger("agents.allergy_review")


class Verdict(StrEnum):
    SAFE = "safe"
    OPTIONAL = "optional"
    UNCERTAIN = "uncertain"
    UNSAFE = "unsafe"


# Most serious last: when the model returns several verdicts for one recipe (e.g. one per
# allergen), the most serious one decides.
SEVERITY = [Verdict.SAFE, Verdict.OPTIONAL, Verdict.UNCERTAIN, Verdict.UNSAFE]


class AllergyReview(BaseModel):
    recipe_id: int
    verdict: Verdict
    allergen: str | None = None  # EU code or the user's own word
    evidence: str | None = None  # exact quote from the recipe


class AllergyReviewBatch(BaseModel):
    reviews: list[AllergyReview]


def most_serious(reviews: list[AllergyReview]) -> AllergyReview:
    return max(reviews, key=lambda r: SEVERITY.index(r.verdict))


@dataclass(frozen=True)
class ReviewOutcome:
    keep: bool
    warning: str | None = None  # shown to the user when kept
    reason: FailureReason | None = None  # why it was removed


def outcome_for(review: AllergyReview | None, recipe_name: str) -> ReviewOutcome:
    """The decision rules (code, not prompt)."""
    if review is None:
        return ReviewOutcome(
            keep=False,
            reason=FailureReason(
                code=FailureCode.HIDDEN_ALLERGEN,
                item=recipe_name,
                detail="the allergy review gave no answer for this recipe",
            ),
        )
    allergen = review.allergen or "an allergen"
    evidence = (review.evidence or "").strip().strip('"').strip("'").strip()
    quote = f'"{evidence}"' if evidence else "the recipe"
    if review.verdict is Verdict.UNSAFE:
        return ReviewOutcome(
            keep=False,
            reason=FailureReason(
                code=FailureCode.ALLERGEN,
                item=allergen,
                detail=f"allergy review: {quote} ({allergen})",
            ),
        )
    if review.verdict is Verdict.OPTIONAL:
        return ReviewOutcome(keep=True, warning=f"Leave out the {allergen}: {quote}.")
    if review.verdict is Verdict.UNCERTAIN:
        return ReviewOutcome(keep=True, warning=f"Check the label for {allergen}: {quote}.")
    return ReviewOutcome(keep=True)


def keyword_hints(texts: list[str], query: RecipeQuery) -> list[str]:
    """Code scan: text lines that mention one of the user's allergies (rules + words)."""
    codes = set(query.required_allergen_free)
    hints = []
    for text in texts:
        found = {a.value for a in detect_allergens(text) & codes}
        found |= {w for w in query.other_allergies if mentions_word(text, w)}
        for allergen in sorted(found):
            hints.append(f'{allergen}: "{text[:120]}"')
    return hints


def has_allergies(query: RecipeQuery) -> bool:
    return bool(query.required_allergen_free or query.other_allergies)


class AllergyReviewer:
    def __init__(
        self,
        llm: StructuredLLM,
        conn: sqlite3.Connection,
        cache_path: Path | None = None,
        batch_size: int = 10,
    ):
        self.llm = llm
        # Part of the cache key: verdicts from one model are not reused for another.
        self.model_name = getattr(llm, "model_name", "unknown")
        self.conn = conn
        self.cache_path = cache_path
        self.batch_size = batch_size
        self.prompt = load_prompt("allergy_review")
        self.cache: dict[str, dict] = (
            json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
        )

    def key(self, recipe_id: int, query: RecipeQuery) -> str:
        codes = ",".join(sorted(a.value for a in query.required_allergen_free))
        words = ",".join(sorted(query.other_allergies))
        return f"{recipe_id}|{codes}|{words}|v{self.prompt.version}|{self.model_name}"

    def recipe_payload(self, candidate: Candidate, query: RecipeQuery) -> dict:
        row = self.conn.execute(
            "SELECT description FROM recipes WHERE id = ?", (candidate.recipe_id,)
        ).fetchone()
        description = (row["description"] if row else "") or ""
        steps = load_steps(self.conn, candidate.recipe_id)
        return {
            "recipe_id": candidate.recipe_id,
            "name": candidate.name,
            "description": description,
            "ingredients": [i.name for i in candidate.ingredients],
            "steps": steps,
            "keyword_hints": keyword_hints([candidate.name, description, *steps], query),
        }

    def review(self, query: RecipeQuery, candidates: list[Candidate]) -> dict[int, ReviewOutcome]:
        if not has_allergies(query) or not candidates:
            return {c.recipe_id: ReviewOutcome(keep=True) for c in candidates}
        todo = [c for c in candidates if self.key(c.recipe_id, query) not in self.cache]
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            wanted = {c.recipe_id for c in batch}
            result = self.llm.generate(
                self.prompt,
                AllergyReviewBatch,
                allergen_codes=", ".join(a.value for a in query.required_allergen_free) or "none",
                other_allergies=", ".join(query.other_allergies) or "none",
                recipes=json.dumps([self.recipe_payload(c, query) for c in batch], indent=1),
            )
            answered: dict[int, list[AllergyReview]] = {}
            for review in result.reviews:
                if review.recipe_id in wanted:  # ignore ids that were not asked about
                    answered.setdefault(review.recipe_id, []).append(review)
            for recipe_id, reviews in answered.items():
                decided = most_serious(reviews)  # never let a later "safe" hide an "unsafe"
                self.cache[self.key(recipe_id, query)] = decided.model_dump(mode="json")
            self.save()

        outcomes = {}
        for c in candidates:
            cached = self.cache.get(self.key(c.recipe_id, query))
            found = AllergyReview.model_validate(cached) if cached else None
            outcomes[c.recipe_id] = outcome_for(found, c.name)
        removed = sum(not o.keep for o in outcomes.values())
        score("allergy_review_removed", removed)
        log.info(
            "allergy_review",
            candidates=len(candidates),
            removed=removed,
            warned=sum(bool(o.warning) for o in outcomes.values()),
        )
        return outcomes

    def save(self) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        os.replace(tmp, self.cache_path)


def code_only_outcomes(
    conn: sqlite3.Connection, query: RecipeQuery, candidates: list[Candidate]
) -> dict[int, ReviewOutcome]:
    """Without a reviewer: steps that mention an allergy become warnings (code cannot tell
    a required step from an optional one, so it warns rather than removes)."""
    outcomes = {}
    for c in candidates:
        hints = keyword_hints(load_steps(conn, c.recipe_id), query) if has_allergies(query) else []
        warning = None
        if hints:
            warning = f"The steps mention {'; '.join(hints[:2])}. Leave it out or check."
        outcomes[c.recipe_id] = ReviewOutcome(keep=True, warning=warning)
    return outcomes
