"""End-to-end evaluation: whole conversations with a simulated user.

Each case is a first-visit user: a safety answer, a request, and the hand-written truth
(what they are really allergic to, their diets, time limit, real pantry and amounts). The
simulated user answers every question the same way on every run; the final recipe and
every recipe shown are then checked against the truth by code, independently of what the
pipeline understood. So a parsing mistake ("coeliac" read as a diet only) shows up as a
failure here even when every later step did its job.
"""

import json
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from pantry_chef.agents.verifier import check_other_allergies
from pantry_chef.db.repository import load_nutrition, load_recipe_ingredients
from pantry_chef.evaluation.metrics import hard_rule_failures, percentile
from pantry_chef.graph.runner import Conversation
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.llm import usage
from pantry_chef.models.chat import (
    AmountReply,
    ChoiceReply,
    ConfirmReply,
    QuantityReply,
    Question,
    QuestionKind,
    SafetyReply,
)
from pantry_chef.models.query import AmountStatus, Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate

MAX_TURNS = 10  # a conversation needing more is stuck (counted as an error)


class TrueAmount(BaseModel):
    quantity: float
    unit: str | None = None  # None = a count ("2" eggs)


class E2ECase(BaseModel):
    id: str
    group: str
    safety_answer: str = "none"  # the answer to the first-visit safety question
    message: str  # the request, as the user types it
    # --- the truth, written by hand: what every recipe shown must respect ---
    pantry: list[str]  # what the user really has (for "can they make it")
    allergens: list[Allergen] = Field(default_factory=list)
    other_allergies: list[str] = Field(default_factory=list)  # outside the EU 14 ("kiwi")
    diets: list[Diet] = Field(default_factory=list)
    max_minutes: int | None = None
    amounts: dict[str, TrueAmount] = Field(default_factory=dict)  # hidden; asked -> told
    expect_recipe: bool = True  # False: "no recipe" is the right answer (empty pantry)
    note: str = ""  # why the case exists


# --- the simulated user ----------------------------------------------------------------


class SimulatedUser:
    """Answers like a cooperative user who confirms what is read back and picks the first
    option. Confirming without reading is the worst case for safety: whatever the intake
    got wrong goes through."""

    def __init__(self, case: E2ECase):
        self.case = case

    def answer(self, question: Question) -> BaseModel:
        kind = question.kind
        if kind is QuestionKind.SAFETY:
            return SafetyReply(text=self.case.safety_answer)
        if kind is QuestionKind.SAFETY_CONFIRM:
            return ConfirmReply(correct=True, consent_to_store=False)
        if kind is QuestionKind.QUANTITIES:
            return QuantityReply(amounts={item: self.amount(item) for item in question.items})
        if kind is QuestionKind.CHOICE:
            return ChoiceReply(choice=1)
        raise ValueError(f"unknown question kind: {kind}")

    def amount(self, item: str) -> AmountReply:
        true = self.case.amounts.get(item)
        if true is None:
            return AmountReply(status=AmountStatus.UNKNOWN)
        return AmountReply(quantity=true.quantity, unit=true.unit, status=AmountStatus.KNOWN)


# --- checking a recipe against the truth -------------------------------------------------


def load_candidate(conn: sqlite3.Connection, recipe_id: int) -> Candidate:
    row = conn.execute("SELECT name, minutes FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    nutrition = load_nutrition(conn, [recipe_id]).get(recipe_id, {})
    return Candidate(
        recipe_id=recipe_id,
        name=row["name"],
        minutes=row["minutes"],
        ingredients=load_recipe_ingredients(conn, [recipe_id]).get(recipe_id, []),
        have_key=0,
        total_key=0,
        coverage=0,
        ingredient_score=0,
        final_score=0,
        sugar_pdv=nutrition.get("sugar_pdv"),
        sodium_pdv=nutrition.get("sodium_pdv"),
    )


def truth_query(case: E2ECase) -> RecipeQuery:
    return RecipeQuery(
        ingredients=case.pantry,
        required_allergen_free=case.allergens,
        diets=case.diets,
        max_minutes=case.max_minutes,
    )


SAFETY_PREFIXES = ("allergen", "diet_violation")


def truth_failures(conn: sqlite3.Connection, recipe_id: int, case: E2ECase) -> list[str]:
    """Hard rules the recipe breaks for this user's TRUE needs (empty = fine). Uses our
    ingredient labels, so a recipe the labels get wrong is not caught here either."""
    candidate = load_candidate(conn, recipe_id)
    failures = hard_rule_failures(candidate, truth_query(case))
    other = check_other_allergies(candidate, case.other_allergies)
    return failures + [str(r) for r in other.reasons]


def is_safety_failure(failure: str) -> bool:
    return failure.startswith(SAFETY_PREFIXES)


# --- one conversation -------------------------------------------------------------------


@dataclass
class E2EResult:
    case_id: str
    group: str
    recipe_id: int | None = None
    recipe_name: str | None = None
    reply: str | None = None  # a plain message instead of a recipe
    failures: list[str] = field(default_factory=list)  # the final recipe vs the truth
    unsafe_shown: list[str] = field(default_factory=list)  # any option shown, safety only
    questions: dict[str, int] = field(default_factory=dict)  # kind -> times asked
    quantity_items_asked: int = 0
    searches: int = 0  # search attempts for the request (1 = no retry)
    latency_s: float = 0.0
    cost_usd: float = 0.0
    llm_calls: int = 0
    expect_recipe: bool = True
    error: str | None = None

    @property
    def success(self) -> bool:
        """The task succeeded: a recipe that respects every true need, or (when no recipe
        is right) an honest reply instead of one."""
        if self.error:
            return False
        if not self.expect_recipe:
            return self.recipe_id is None
        return self.recipe_id is not None and not self.failures

    @property
    def safety_violation(self) -> bool:
        return bool(self.unsafe_shown) or any(is_safety_failure(f) for f in self.failures)


def run_case(conversation: Conversation, conn: sqlite3.Connection, case: E2ECase) -> E2EResult:
    """Drive one conversation to its end and check what was shown and chosen."""
    result = E2EResult(case_id=case.id, group=case.group, expect_recipe=case.expect_recipe)
    user = SimulatedUser(case)
    before = usage.snapshot()
    start = time.perf_counter()
    asked: Counter[str] = Counter()
    try:
        turn = conversation.send(case.message)
        for _ in range(MAX_TURNS):
            if turn.done:
                break
            question = turn.question
            assert question is not None
            asked[question.kind.value] += 1
            if question.kind is QuestionKind.QUANTITIES:
                result.quantity_items_asked += len(question.items)
            if question.kind is QuestionKind.CHOICE:
                for option in question.options:
                    unsafe = [
                        f
                        for f in truth_failures(conn, option.recipe_id, case)
                        if is_safety_failure(f)
                    ]
                    result.unsafe_shown += [f"{option.name}: {f}" for f in unsafe]
            turn = conversation.reply(user.answer(question))
        else:
            raise RuntimeError(f"no answer after {MAX_TURNS} turns")
        result.searches = conversation.state().attempts
        if turn.answer is not None:
            result.recipe_id, result.recipe_name = turn.answer.recipe_id, turn.answer.name
            result.failures = truth_failures(conn, turn.answer.recipe_id, case)
        result.reply = turn.reply
    except Exception as error:  # one failed API call must not lose the whole run
        result.error = f"{type(error).__name__}: {error}"
    finally:
        result.latency_s = time.perf_counter() - start
        spent = usage.since(before)
        result.llm_calls = sum(u.calls for u in spent.values())
        result.cost_usd = usage.total_cost(spent)
        result.questions = dict(asked)
    return result


# --- summary ----------------------------------------------------------------------------


def failure_codes(results: list[E2EResult]) -> Counter[str]:
    """Why final recipes failed, by reason code (failure analysis)."""
    codes: Counter[str] = Counter()
    for r in results:
        if r.error:
            codes["error"] += 1
        elif r.expect_recipe and r.recipe_id is None:
            codes["no_recipe"] += 1
        elif not r.expect_recipe and r.recipe_id is not None:
            codes["recipe_when_none_expected"] += 1
        for f in r.failures:
            codes[f.split(":")[0].split(" ")[0]] += 1
    return codes


def summarize(results: list[E2EResult]) -> dict:
    n = len(results)
    ran = [r for r in results if not r.error]
    latencies = [r.latency_s for r in ran] or [0.0]
    return {
        "cases": n,
        "task_success": sum(r.success for r in results) / n if n else 0.0,
        "safety_violations": sum(r.safety_violation for r in results),
        "unsafe_options_shown": sum(len(r.unsafe_shown) for r in results),
        "rule_violation_rate": (
            sum(bool(r.failures) for r in ran if r.recipe_id)
            / max(1, sum(bool(r.recipe_id) for r in ran))
        ),
        "no_recipe": sum(r.recipe_id is None and r.expect_recipe for r in ran),
        "questions_per_request": sum(sum(r.questions.values()) for r in ran) / max(1, len(ran)),
        "quantity_items_per_request": sum(r.quantity_items_asked for r in ran) / max(1, len(ran)),
        "retries_per_request": sum(max(0, r.searches - 1) for r in ran) / max(1, len(ran)),
        "errors": n - len(ran),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "cost_usd": sum(r.cost_usd for r in results),
        "cost_per_case_usd": sum(r.cost_usd for r in results) / n if n else 0.0,
        "llm_calls_per_case": sum(r.llm_calls for r in results) / n if n else 0.0,
    }


# --- report -----------------------------------------------------------------------------


def write_report(results: list[E2EResult], meta: dict, out_dir: Path) -> tuple[Path, Path]:
    """Markdown report (committed) and full JSON results (git-ignored)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta["timestamp"]
    md_path, json_path = out_dir / f"e2e_{stamp}.md", out_dir / f"e2e_{stamp}.json"
    s = summarize(results)
    lines = [
        f"# End-to-end evaluation — {stamp}",
        "",
        f"Cases: {s['cases']} (`{meta['cases_file']}`) | models: {meta['models']} | "
        "simulated user: confirms the safety read-back, answers amounts from the hidden "
        "truth, picks option 1",
    ]
    if meta.get("stopped"):
        lines += ["", f"**Stopped early:** {meta['stopped']}"]
    lines += [
        "",
        "| task success | safety violations | unsafe options shown | rule violations "
        "(of recipes given) | no recipe | questions / request | retries / request "
        "| errors | p50 s | p95 s | cost | cost / case |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
        f"| {s['task_success']:.0%} | {s['safety_violations']} | {s['unsafe_options_shown']} "
        f"| {s['rule_violation_rate']:.0%} | {s['no_recipe']} "
        f"| {s['questions_per_request']:.2f} | {s['retries_per_request']:.2f} "
        f"| {s['errors']} | {s['latency_p50_s']:.1f} | {s['latency_p95_s']:.1f} "
        f"| ${s['cost_usd']:.3f} | ${s['cost_per_case_usd']:.4f} |",
        "",
        "Task success = a recipe that respects every true need (allergens, other "
        "allergies, diets, time, at most one missing key ingredient), or an honest "
        "reply when no recipe is right. Checked by code against the hand-written truth, "
        "not against what the pipeline understood.",
        "",
        "## Failures by reason",
        "",
    ]
    codes = failure_codes(results)
    lines += [f"- {code}: {n}" for code, n in codes.most_common()] or ["- none"]
    lines += ["", "## Cases that failed", ""]
    failed = [r for r in results if not r.success or r.safety_violation]
    for r in failed:
        what = r.error or (
            f"{r.recipe_name} ({r.recipe_id})" if r.recipe_id else f"no recipe: {r.reply}"
        )
        lines.append(f"- **{r.case_id}** ({r.group}): {what}")
        lines += [f"  - {f}" for f in r.failures]
        lines += [f"  - SHOWN UNSAFE: {u}" for u in r.unsafe_shown]
    if not failed:
        lines.append("- none")
    md_path.write_text("\n".join(lines) + "\n")
    json_path.write_text(
        json.dumps({"meta": meta, "summary": s, "results": [asdict(r) for r in results]}, indent=1)
    )
    return md_path, json_path


def load_e2e_cases(path: Path) -> list[E2ECase]:
    cases = [E2ECase.model_validate(item) for item in json.loads(path.read_text())]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases
