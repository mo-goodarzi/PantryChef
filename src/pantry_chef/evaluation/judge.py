"""LLM-as-judge for preference fit, with a JSON cache so reruns and repeated recipes
across variants cost nothing."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.llm.prompt_loader import load_prompt
from pantry_chef.search.text import useful_tags

PROMPT_NAME = "preference_judge"


class Judgment(BaseModel):
    recipe_id: int
    score: int = Field(ge=1, le=5)
    reason: str


class JudgmentBatch(BaseModel):
    judgments: list[Judgment]


def recipe_summaries(conn: sqlite3.Connection, recipe_ids: list[int]) -> dict[int, dict]:
    """What the judge sees about each recipe: name, time, meal, cuisine, text, tags."""
    summaries = {}
    for recipe_id in recipe_ids:
        row = conn.execute(
            "SELECT name, minutes, meal_type, cuisine, description FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        tags = useful_tags(
            [
                r["name"]
                for r in conn.execute(
                    "SELECT t.name FROM recipe_tags rt JOIN tags t ON t.id = rt.tag_id "
                    "WHERE rt.recipe_id = ?",
                    (recipe_id,),
                )
            ]
        )
        ingredients = [
            r["name"]
            for r in conn.execute(
                "SELECT i.name FROM recipe_ingredients ri JOIN ingredients i "
                "ON i.id = ri.ingredient_id WHERE ri.recipe_id = ? ORDER BY ri.position",
                (recipe_id,),
            )
        ]
        summaries[recipe_id] = {
            "recipe_id": recipe_id,
            "name": row["name"],
            "minutes": row["minutes"],
            "meal_type": row["meal_type"],
            "cuisine": row["cuisine"],
            "description": (row["description"] or "")[:300],
            "tags": tags[:15],
            "ingredients": ingredients,
        }
    return summaries


def case_key(case: SearchCase) -> str:
    """Changes when the case's wish or pantry changes, so old judgments are not reused."""
    payload = json.dumps([case.preferences, sorted(case.pantry)])
    return hashlib.sha1(payload.encode()).hexdigest()[:10]


class CachedJudge:
    def __init__(self, llm: StructuredLLM, cache_path: Path):
        self.llm = llm
        self.cache_path = cache_path
        self.prompt = load_prompt(PROMPT_NAME)
        self.cache: dict[str, dict] = (
            json.loads(cache_path.read_text()) if cache_path.exists() else {}
        )
        self.calls = 0

    def key(self, case: SearchCase, recipe_id: int) -> str:
        return f"{case.id}:{case_key(case)}:{recipe_id}:v{self.prompt.version}"

    def judge(
        self, conn: sqlite3.Connection, case: SearchCase, recipe_ids: list[int]
    ) -> dict[int, Judgment]:
        """Judgments for all recipe_ids; only uncached ones are sent to the LLM."""
        todo = [rid for rid in dict.fromkeys(recipe_ids) if self.key(case, rid) not in self.cache]
        if todo:
            summaries = recipe_summaries(conn, todo)
            result = self.llm.generate(
                self.prompt,
                JudgmentBatch,
                wish=case.preferences,
                pantry=", ".join(case.pantry),
                recipes=json.dumps(list(summaries.values()), indent=1),
            )
            self.calls += 1
            for judgment in result.judgments:
                if judgment.recipe_id in summaries:
                    self.cache[self.key(case, judgment.recipe_id)] = judgment.model_dump()
            self.save()
        return {
            rid: Judgment.model_validate(self.cache[self.key(case, rid)])
            for rid in recipe_ids
            if self.key(case, rid) in self.cache
        }

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        os.replace(tmp, self.cache_path)
