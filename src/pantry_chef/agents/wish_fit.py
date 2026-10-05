"""Wish-fit check: does each shortlisted recipe fit what the user asked for?

A separate agent (own prompt, model setting, cache, trace span and eval) that runs after
the allergy review and before the reranker, so the reranker only sees fitting recipes.
The LLM judges the fuzzy part ("is a pepperoni pizza a high-protein dinner?"); code gives
it the facts it knows (recipes.is_high_protein) and decides what happens:
- fits -> kept
- partly -> kept with a short note
- no -> removed, recorded as preference_mismatch (a retry excludes it)
- no verdict -> kept (a wish is not safety; unlike the allergy review)
- every recipe "no" -> all kept with a "may not match" note: a wish never leaves the
  user with nothing.
Skipped when the user gave no wish and no goals.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.query import RecipeQuery, goals_text
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.verification import FailureCode, FailureReason
from pantry_chef.observability import get_logger, score
from pantry_chef.search.text import useful_tags

log = get_logger("agents.wish_fit")


class Fit(StrEnum):
    FITS = "fits"
    PARTLY = "partly"
    NO = "no"


# Least fitting last: when the model returns several verdicts for one recipe, the least
# fitting decides (the same "be strict" direction as the allergy review).
FIT_ORDER = [Fit.FITS, Fit.PARTLY, Fit.NO]


class WishFitReview(BaseModel):
    recipe_id: int
    fit: Fit
    reason: str | None = None


class WishFitBatch(BaseModel):
    reviews: list[WishFitReview]


@dataclass(frozen=True)
class FitOutcome:
    keep: bool
    note: str | None = None  # shown to the user when kept
    reason: FailureReason | None = None  # why it was removed
    why: str = ""  # the model's short reason, as given


def least_fitting(reviews: list[WishFitReview]) -> WishFitReview:
    return max(reviews, key=lambda r: FIT_ORDER.index(r.fit))


def clean(reason: str | None) -> str:
    return (reason or "").strip().strip('"').strip("'").strip().rstrip(".")


def outcome_for(review: WishFitReview | None, recipe_name: str) -> FitOutcome:
    """The decision rules (code, not prompt)."""
    if review is None or review.fit is Fit.FITS:
        return FitOutcome(keep=True)
    reason = clean(review.reason)
    if review.fit is Fit.PARTLY:
        return FitOutcome(keep=True, note=f"Partly fits: {reason}." if reason else "Partly fits.")
    return FitOutcome(
        keep=False,
        why=reason,
        reason=FailureReason(
            code=FailureCode.PREFERENCE_MISMATCH,
            item=recipe_name,
            detail=f"does not fit the wish: {reason}" if reason else "does not fit the wish",
        ),
    )


def never_empty(outcomes: dict[int, FitOutcome]) -> dict[int, FitOutcome]:
    """If every recipe would be removed, keep them all with a note instead."""
    if not outcomes or any(o.keep for o in outcomes.values()):
        return outcomes
    kept = {}
    for recipe_id, o in outcomes.items():
        note = "May not match what you asked for" + (f" ({o.why})." if o.why else ".")
        kept[recipe_id] = FitOutcome(keep=True, note=note)
    return kept


def needs_check(query: RecipeQuery) -> bool:
    return bool(query.preferences_text.strip() or query.nutrition_goals)


class WishFitChecker:
    def __init__(
        self,
        llm: StructuredLLM,
        conn: sqlite3.Connection,
        cache_path: Path | None = None,
        batch_size: int = 20,
    ):
        self.llm = llm
        self.model_name = getattr(llm, "model_name", "unknown")
        self.conn = conn
        self.cache_path = cache_path
        self.batch_size = batch_size
        self.prompt = load_prompt("wish_fit")
        self.cache: dict[str, dict] = (
            json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
        )

    def key(self, recipe_id: int, query: RecipeQuery) -> str:
        wish = " ".join(query.preferences_text.lower().split())
        goals = ",".join(sorted(g.value for g in query.nutrition_goals))
        return f"{recipe_id}|{wish}|{goals}|v{self.prompt.version}|{self.model_name}"

    def recipe_payload(self, candidate: Candidate) -> dict:
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(recipes)")}
        high = "is_high_protein" if "is_high_protein" in columns else "NULL"
        row = self.conn.execute(
            f"SELECT description, meal_type, {high} AS high_protein FROM recipes WHERE id = ?",
            (candidate.recipe_id,),
        ).fetchone()
        tags = [
            r["name"]
            for r in self.conn.execute(
                "SELECT t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id "
                "WHERE rt.recipe_id = ?",
                (candidate.recipe_id,),
            )
        ]
        high_protein = (
            None if row is None or row["high_protein"] is None else bool(row["high_protein"])
        )
        return {
            "recipe_id": candidate.recipe_id,
            "name": candidate.name,
            "minutes": candidate.minutes,
            "meal_type": row["meal_type"] if row else None,
            "description": ((row["description"] if row else "") or "")[:300],
            "tags": useful_tags(tags)[:15],
            "ingredients": [i.name for i in candidate.ingredients],
            "high_protein": high_protein,
        }

    def check(self, query: RecipeQuery, candidates: list[Candidate]) -> dict[int, FitOutcome]:
        if not needs_check(query) or not candidates:
            return {c.recipe_id: FitOutcome(keep=True) for c in candidates}
        todo = [c for c in candidates if self.key(c.recipe_id, query) not in self.cache]
        for start in range(0, len(todo), self.batch_size):
            batch = todo[start : start + self.batch_size]
            wanted = {c.recipe_id for c in batch}
            result = self.llm.generate(
                self.prompt,
                WishFitBatch,
                wish=query.preferences_text.strip() or "anything",
                goals=goals_text(query.nutrition_goals) or "none",
                recipes=json.dumps([self.recipe_payload(c) for c in batch], indent=1),
            )
            answered: dict[int, list[WishFitReview]] = {}
            for review in result.reviews:
                if review.recipe_id in wanted:  # ignore ids that were not asked about
                    answered.setdefault(review.recipe_id, []).append(review)
            for recipe_id, reviews in answered.items():
                decided = least_fitting(reviews)
                self.cache[self.key(recipe_id, query)] = decided.model_dump(mode="json")
            self.save()

        outcomes = {}
        for c in candidates:
            cached = self.cache.get(self.key(c.recipe_id, query))
            found = WishFitReview.model_validate(cached) if cached else None
            outcomes[c.recipe_id] = outcome_for(found, c.name)
        outcomes = never_empty(outcomes)
        removed = sum(not o.keep for o in outcomes.values())
        score("wish_fit_removed", removed)
        log.info(
            "wish_fit",
            candidates=len(candidates),
            removed=removed,
            partly=sum(bool(o.note) for o in outcomes.values()),
        )
        return outcomes

    def save(self) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        os.replace(tmp, self.cache_path)
