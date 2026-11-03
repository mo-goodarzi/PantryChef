"""LLM-as-judge for preference fit, with a JSON cache so reruns and repeated recipes
across variants cost nothing.

A judge is a provider + model + prompt version (JudgeSpec). The cache key holds all three,
so a verdict from one judge is never reused for another.
"""

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from pantry_chef.config import Settings
from pantry_chef.db.repository import recipe_summaries
from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.llm.factory import StructuredLLM, create_llm
from pantry_chef.llm.prompt_loader import load_prompt

PROMPT_NAME = "preference_judge"


class Judgment(BaseModel):
    recipe_id: int
    score: int = Field(ge=1, le=5)
    reason: str


class JudgmentBatch(BaseModel):
    """Prompt v1's answer: the score comes before the reason."""

    judgments: list[Judgment]


class ReasonedJudgment(BaseModel):
    recipe_id: int
    reasoning: str  # written before the score, so the score follows from it
    score: int = Field(ge=1, le=5)


class ReasonedJudgmentBatch(BaseModel):
    """Prompt v2's answer: reasoning first, then the score."""

    judgments: list[ReasonedJudgment]


@dataclass(frozen=True)
class JudgeSpec:
    provider: str
    model: str
    prompt_version: str

    @classmethod
    def parse(cls, text: str) -> "JudgeSpec":
        """ "anthropic:claude-sonnet-5-5:v2" -> JudgeSpec."""
        provider, model, version = text.split(":")
        return cls(provider, model, version.removeprefix("v"))

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}:v{self.prompt_version}"

    @property
    def label(self) -> str:
        return f"{self.model} ({PROMPT_NAME} v{self.prompt_version})"


def judge_spec(settings: Settings) -> JudgeSpec:
    return JudgeSpec(settings.judge_provider, settings.judge_model, settings.judge_prompt_version)


def create_judge(
    settings: Settings, cache_path: Path, spec: JudgeSpec | None = None
) -> "CachedJudge":
    """The judge from the settings (or `spec`), built like any other LLM: the judge's own
    provider and model, so changing LLM_MODEL changes the pipeline, not the ruler."""
    spec = spec or judge_spec(settings)
    llm = create_llm(
        settings.model_copy(update={"llm_provider": spec.provider, "llm_model": spec.model})
    )
    return CachedJudge(llm, cache_path, spec)


def case_key(case: SearchCase) -> str:
    """Changes when the case's wish or pantry changes, so old judgments are not reused."""
    payload = json.dumps([case.judged_wish, sorted(case.pantry)])
    return hashlib.sha1(payload.encode()).hexdigest()[:10]


class CachedJudge:
    def __init__(self, llm: StructuredLLM, cache_path: Path, spec: JudgeSpec | None = None):
        self.llm = llm
        self.cache_path = cache_path
        self.spec = spec or JudgeSpec(
            getattr(llm, "provider", "unknown"),
            getattr(llm, "model_name", "unknown"),
            load_prompt(PROMPT_NAME).version,
        )
        self.prompt = load_prompt(PROMPT_NAME, self.spec.prompt_version)
        self.cache: dict[str, dict] = (
            json.loads(cache_path.read_text()) if cache_path.exists() else {}
        )
        self.calls = 0
        self._lock = threading.Lock()  # calibration judges rows from worker threads

    def key(self, case: SearchCase, recipe_id: int) -> str:
        spec = self.spec
        return (
            f"{case.id}:{case_key(case)}:{recipe_id}:v{spec.prompt_version}:"
            f"{spec.provider}/{spec.model}"
        )

    def judge(
        self, conn: sqlite3.Connection, case: SearchCase, recipe_ids: list[int]
    ) -> dict[int, Judgment]:
        """Judgments for all recipe_ids; only uncached ones are sent to the LLM."""
        todo = [rid for rid in dict.fromkeys(recipe_ids) if self.key(case, rid) not in self.cache]
        if todo:
            summaries = recipe_summaries(conn, todo)
            judgments = self._ask(case, summaries)
            with self._lock:
                self.calls += 1
                for judgment in judgments:
                    if judgment.recipe_id in summaries:
                        self.cache[self.key(case, judgment.recipe_id)] = judgment.model_dump()
                self.save()
        return {
            rid: Judgment.model_validate(self.cache[self.key(case, rid)])
            for rid in recipe_ids
            if self.key(case, rid) in self.cache
        }

    def _ask(self, case: SearchCase, summaries: dict[int, dict]) -> list[Judgment]:
        variables = dict(
            wish=case.judged_wish,
            pantry=", ".join(case.pantry),
            recipes=json.dumps(list(summaries.values()), indent=1),
        )
        if self.prompt.version == "1":
            return self.llm.generate(self.prompt, JudgmentBatch, **variables).judgments
        reasoned = self.llm.generate(self.prompt, ReasonedJudgmentBatch, **variables)
        return [
            Judgment(recipe_id=j.recipe_id, score=j.score, reason=j.reasoning)
            for j in reasoned.judgments
        ]

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True))
        os.replace(tmp, self.cache_path)
