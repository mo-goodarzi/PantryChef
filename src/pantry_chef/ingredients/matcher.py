"""Ingredient matching: which pantry item (if any) covers each recipe ingredient.

Layers, cheapest first (CompositeMatcher):
1. ExactMatcher: equal canonical names and the reviewed parent hierarchy (no cost).
2. MatchCache: earlier LLM answers stored per (user_term, recipe_term) pair.
3. LLMMatcher: one batched call for everything still unknown; every answer is cached and
   appended to match_log.jsonl (training data for the Phase 9 pair classifier).
All terms are canonical names (see normalize).
"""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.matching import MatchLabel, MatchResult, best_match


class Matcher(Protocol):
    def match(self, user_terms: list[str], recipe_terms: list[str]) -> list[MatchResult]:
        """One result per recipe term, in the same order."""
        ...


# --- layer 1: exact + hierarchy ------------------------------------------------------


class ExactMatcher:
    """Equal names, plus the parent hierarchy: parents[child] = {parent, ...}."""

    def __init__(self, parents: dict[str, set[str]] | None = None):
        self.parents = parents or {}

    def ancestors(self, term: str) -> set[str]:
        seen: set[str] = set()
        todo = list(self.parents.get(term, ()))
        while todo:
            parent = todo.pop()
            if parent not in seen:
                seen.add(parent)
                todo.extend(self.parents.get(parent, ()))
        return seen

    def match_one(self, user_terms: list[str], recipe_term: str) -> MatchResult | None:
        """A result if the hierarchy can decide, else None (unknown, not "different")."""
        found = []
        for user_term in user_terms:
            if user_term == recipe_term:
                found.append(
                    MatchResult(
                        recipe_term=recipe_term,
                        user_term=user_term,
                        label=MatchLabel.SAME,
                        source="exact",
                    )
                )
            elif recipe_term in self.ancestors(user_term):  # user: cheddar, recipe: cheese
                found.append(
                    MatchResult(
                        recipe_term=recipe_term,
                        user_term=user_term,
                        label=MatchLabel.SAME,
                        source="hierarchy",
                    )
                )
            elif user_term in self.ancestors(recipe_term):  # user: pasta, recipe: spaghetti
                found.append(
                    MatchResult(
                        recipe_term=recipe_term,
                        user_term=user_term,
                        label=MatchLabel.SUBSTITUTE,
                        source="hierarchy",
                    )
                )
        return best_match(found) if found else None

    def match(self, user_terms: list[str], recipe_terms: list[str]) -> list[MatchResult]:
        return [
            self.match_one(user_terms, r)
            or MatchResult(recipe_term=r, user_term=None, label=MatchLabel.DIFFERENT, source="none")
            for r in recipe_terms
        ]


def parents_from_seed(seed: dict[str, dict]) -> dict[str, set[str]]:
    return {name: set(rel["parents"]) for name, rel in seed.items() if rel.get("parents")}


# --- layer 2: cache ------------------------------------------------------------------


class MatchCache:
    """Pair labels in the match_cache table of pantry.db.

    Entries are tagged with their source (e.g. "llm:v2"); only entries from the current
    source are used, so changing the prompt version invalidates old answers.
    """

    def __init__(self, conn: sqlite3.Connection, source: str = "llm"):
        self.conn = conn
        self.source = source

    def get(self, user_term: str, recipe_term: str) -> MatchLabel | None:
        row = self.conn.execute(
            "SELECT label FROM match_cache WHERE user_term = ? AND recipe_term = ? AND source = ?",
            (user_term, recipe_term, self.source),
        ).fetchone()
        return MatchLabel(row["label"]) if row else None

    def put_many(self, pairs: list[tuple[str, str, MatchLabel]]) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO match_cache "
                "(user_term, recipe_term, label, source, created_at) VALUES (?, ?, ?, ?, ?)",
                [(u, r, label.value, self.source, now) for u, r, label in pairs],
            )


# --- layer 3: LLM --------------------------------------------------------------------


class LLMPick(BaseModel):
    recipe_term: str
    user_term: str | None
    label: MatchLabel


class LLMMatchBatch(BaseModel):
    matches: list[LLMPick]


def chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class LLMMatcher:
    def __init__(self, llm: StructuredLLM, batch_size: int = 40):
        self.llm = llm
        self.batch_size = batch_size
        self.prompt = load_prompt("ingredient_match")

    def match(self, user_terms: list[str], recipe_terms: list[str]) -> list[MatchResult]:
        picks: dict[str, LLMPick] = {}
        for batch in chunks(recipe_terms, self.batch_size):
            result = self.llm.generate(
                self.prompt,
                LLMMatchBatch,
                user_terms=json.dumps(user_terms),
                recipe_terms=json.dumps(batch),
            )
            for pick in result.matches:
                valid_user = pick.user_term is None or pick.user_term in user_terms
                if pick.recipe_term in batch and valid_user:
                    picks[pick.recipe_term] = pick
        results = []
        for r in recipe_terms:
            chosen = picks.get(r)
            if chosen is None or chosen.user_term is None:
                results.append(
                    MatchResult(
                        recipe_term=r, user_term=None, label=MatchLabel.DIFFERENT, source="llm"
                    )
                )
            else:
                results.append(
                    MatchResult(
                        recipe_term=r, user_term=chosen.user_term, label=chosen.label, source="llm"
                    )
                )
        return results


# --- composite -----------------------------------------------------------------------


def llm_cache_source(llm: "LLMMatcher") -> str:
    """Cache tag for answers from this LLM matcher's prompt version."""
    return f"llm:{llm.prompt.name}:v{llm.prompt.version}"


class CompositeMatcher:
    def __init__(
        self,
        exact: ExactMatcher,
        cache: MatchCache | None = None,
        llm: LLMMatcher | None = None,
        log_path: Path | None = None,
    ):
        self.exact = exact
        self.cache = cache
        self.llm = llm
        self.log_path = log_path

    def from_cache(self, user_terms: list[str], recipe_term: str) -> MatchResult | None:
        """A result only if EVERY pair for this recipe term is cached (else ask again)."""
        if self.cache is None:
            return None
        labels = {u: self.cache.get(u, recipe_term) for u in user_terms}
        if any(label is None for label in labels.values()):
            return None
        return best_match(
            [
                MatchResult(
                    recipe_term=recipe_term,
                    user_term=u if label.available else None,
                    label=label,
                    source="cache",
                )
                for u, label in labels.items()
                if label is not None
            ]
        )

    def match(self, user_terms: list[str], recipe_terms: list[str]) -> list[MatchResult]:
        users = sorted(set(user_terms))
        results: dict[str, MatchResult] = {}
        unknown: list[str] = []
        for r in dict.fromkeys(recipe_terms):
            found = self.exact.match_one(users, r) if users else None
            found = found or (self.from_cache(users, r) if users else None)
            if found:
                results[r] = found
            else:
                unknown.append(r)

        if unknown and users and self.llm is not None:
            for result in self.llm.match(users, unknown):
                results[result.recipe_term] = result
            self.remember(users, [results[r] for r in unknown])

        return [
            results.get(r)
            or MatchResult(recipe_term=r, user_term=None, label=MatchLabel.DIFFERENT, source="none")
            for r in recipe_terms
        ]

    def remember(self, users: list[str], results: list[MatchResult]) -> None:
        """Cache every (user, recipe) pair: the chosen one with its label, the rest as
        different; append the LLM answers to the log."""
        pairs = [
            (u, r.recipe_term, r.label if u == r.user_term else MatchLabel.DIFFERENT)
            for r in results
            for u in users
        ]
        if self.cache is not None:
            self.cache.put_many(pairs)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as log:
                version = self.llm.prompt.version if self.llm is not None else None
                for r in results:
                    entry = {
                        "user_terms": users,
                        "prompt_version": version,
                        **r.model_dump(mode="json"),
                    }
                    log.write(json.dumps(entry) + "\n")
