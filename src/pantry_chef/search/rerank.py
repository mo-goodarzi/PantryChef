"""Final reranking of the verified shortlist.

Behind a small Reranker protocol so the LLM version can later be replaced by a local
cross-encoder. The LLM can only reorder and choose among verified candidates; it can
never add a recipe, so it cannot break a safety check.
"""

import json
import sqlite3
from typing import Protocol

from pydantic import BaseModel, Field

from pantry_chef.db.repository import recipe_summaries
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate


class RankedPick(BaseModel):
    recipe_id: int
    reason: str = Field(description="Short reason for the user, max 15 words.")


class RerankResult(BaseModel):
    picks: list[RankedPick]


class Reranker(Protocol):
    def rerank(
        self, query: RecipeQuery, candidates: list[Candidate], k: int
    ) -> list[Candidate]: ...


def apply_picks(candidates: list[Candidate], picks: list[RankedPick], k: int) -> list[Candidate]:
    """Order candidates by the picks; ignore unknown or repeated ids; fill up to k with
    the remaining candidates in their original order."""
    by_id = {c.recipe_id: c for c in candidates}
    chosen: list[Candidate] = []
    for pick in picks:
        if pick.recipe_id in by_id and all(c.recipe_id != pick.recipe_id for c in chosen):
            chosen.append(by_id[pick.recipe_id].model_copy(update={"rerank_reason": pick.reason}))
    chosen_ids = {c.recipe_id for c in chosen}
    chosen += [c for c in candidates if c.recipe_id not in chosen_ids]
    return chosen[:k]


class LLMReranker:
    def __init__(self, llm: StructuredLLM, conn: sqlite3.Connection):
        self.llm = llm
        self.conn = conn
        self.prompt = load_prompt("rerank")

    def rerank(self, query: RecipeQuery, candidates: list[Candidate], k: int) -> list[Candidate]:
        if len(candidates) <= 1:
            return candidates[:k]
        summaries = recipe_summaries(self.conn, [c.recipe_id for c in candidates])
        for c in candidates:
            summaries[c.recipe_id]["missing_key"] = c.missing_key
        result = self.llm.generate(
            self.prompt,
            RerankResult,
            k=str(k),
            wish=query.preferences_text or "anything",
            pantry=", ".join(query.ingredients),
            recipes=json.dumps(list(summaries.values()), indent=1),
        )
        return apply_picks(candidates, result.picks, k)
