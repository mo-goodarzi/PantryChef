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

from pantry_chef.agents.verifier import (
    check_allergens,
    check_diet,
    check_other_allergies,
    check_time,
    needs_match,
)
from pantry_chef.db.repository import load_nutrition, load_recipe_ingredients
from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.judge import CachedJudge
from pantry_chef.evaluation.metrics import GOOD_SCORE, MAX_MISSING_KEY, percentile
from pantry_chef.evaluation.parsing_eval import same_item
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
from pantry_chef.models.query import AmountStatus, Diet
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
    # recipe ingredient names the pantry also covers, beyond the whole-word rule
    # ("pasta" -> ["spaghetti", "penne"]); written by hand per case
    also_ok: list[str] = Field(default_factory=list)
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


def missing_key(candidate: Candidate, case: E2ECase) -> list[str]:
    """Key ingredients the user does not have. Code only, independent of the pipeline's
    LLM matcher: a pantry item covers a recipe ingredient when one is a whole-word part of
    the other ("garlic" / "garlic clove", "tortilla" / "corn tortilla"), or when the case
    lists it in also_ok ("pasta" -> "spaghetti"). Generous on purpose: it decides "can
    they make it", never safety ("cheese" also covers "cream cheese")."""
    have = case.pantry + case.also_ok
    return [
        i.canonical_name
        for i in candidate.ingredients
        if needs_match(i)
        and not any(same_item(h, i.canonical_name) or same_item(h, i.name) for h in have)
    ]


SAFETY_PREFIXES = ("allergen", "diet_violation")


def truth_failures(conn: sqlite3.Connection, recipe_id: int, case: E2ECase) -> list[str]:
    """Hard rules the recipe breaks for this user's TRUE needs (empty = fine). Uses our
    ingredient labels, so a recipe the labels get wrong is not caught here either."""
    candidate = load_candidate(conn, recipe_id)
    checks = [
        check_allergens(candidate, set(case.allergens)),
        check_other_allergies(candidate, case.other_allergies),
        check_diet(candidate, set(case.diets)),
        check_time(candidate, case.max_minutes),
    ]
    failures = [str(r) for c in checks for r in c.reasons]
    missing = missing_key(candidate, case)
    if len(missing) > MAX_MISSING_KEY:
        failures.append(f"missing_key: {', '.join(missing)}")
    return failures


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
    judge_score: int | None = None  # 1-5: how well the final recipe fits the request
    judge_reason: str | None = None
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


def judged_case(case: E2ECase) -> SearchCase:
    """The case as the judge sees it: the user's own message is the wish."""
    return SearchCase(id=case.id, group=case.group, pantry=case.pantry, preferences=case.message)


def run_case(
    conversation: Conversation,
    conn: sqlite3.Connection,
    case: E2ECase,
    judge: CachedJudge | None = None,
) -> E2EResult:
    """Drive one conversation to its end and check what was shown and chosen. The judge
    (optional) rates the final recipe; its cost is not part of the case's cost."""
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
    if judge is not None and result.recipe_id is not None and not result.error:
        try:
            judgment = judge.judge(conn, judged_case(case), [result.recipe_id]).get(
                result.recipe_id
            )
        except Exception as error:  # the ruler failing must not lose the case
            judgment = None
            result.judge_reason = f"not judged: {type(error).__name__}"
        if judgment is not None:
            result.judge_score, result.judge_reason = judgment.score, judgment.reason
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


def mean(values: list[int]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(results: list[E2EResult]) -> dict:
    n = len(results)
    ran = [r for r in results if not r.error]
    expected = [r for r in results if r.expect_recipe]
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
        "mean_judge_score": mean([r.judge_score for r in ran if r.judge_score is not None]),
        # a recipe that is safe and makeable AND fits the request (judge >= GOOD_SCORE)
        "good_answer_rate": (
            sum(r.success and (r.judge_score or 0) >= GOOD_SCORE for r in expected) / len(expected)
            if expected
            else None
        ),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
        "cost_usd": sum(r.cost_usd for r in results),
        "cost_per_case_usd": sum(r.cost_usd for r in results) / n if n else 0.0,
        "llm_calls_per_case": sum(r.llm_calls for r in results) / n if n else 0.0,
    }


# --- report -----------------------------------------------------------------------------


def summary_row(variant: str, s: dict) -> str:
    judge = "-" if s["mean_judge_score"] is None else f"{s['mean_judge_score']:.2f}"
    good = "-" if s["good_answer_rate"] is None else f"{s['good_answer_rate']:.0%}"
    return (
        f"| {variant} | {s['task_success']:.0%} | {good} | {judge} "
        f"| {s['safety_violations']} | {s['unsafe_options_shown']} "
        f"| {s['rule_violation_rate']:.0%} | {s['no_recipe']} "
        f"| {s['questions_per_request']:.2f} | {s['retries_per_request']:.2f} "
        f"| {s['errors']} | {s['latency_p50_s']:.1f} | {s['latency_p95_s']:.1f} "
        f"| ${s['cost_usd']:.3f} | ${s['cost_per_case_usd']:.4f} |"
    )


def write_report(
    all_results: dict[str, list[E2EResult]], meta: dict, out_dir: Path
) -> tuple[Path, Path]:
    """Markdown report (committed) and full JSON results (git-ignored); one row per
    variant (pipeline configuration)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = meta["timestamp"]
    md_path, json_path = out_dir / f"e2e_{stamp}.md", out_dir / f"e2e_{stamp}.json"
    summaries = {v: summarize(r) for v, r in all_results.items()}
    n = max((s["cases"] for s in summaries.values()), default=0)
    lines = [
        f"# End-to-end evaluation — {stamp}",
        "",
        f"Cases: {n} (`{meta['cases_file']}`) | models: {meta['models']} | judge: "
        f"{meta.get('judge', '-')} | simulated user: confirms the safety read-back, answers "
        "amounts from the hidden truth, picks option 1",
    ]
    if meta.get("stopped"):
        lines += ["", f"**Stopped early:** {meta['stopped']}"]
    lines += [
        "",
        "| Variant | task success | good answer | mean judge | safety violations "
        "| unsafe options shown | rule violations (of recipes given) | no recipe "
        "| questions / request | retries / request | errors | p50 s | p95 s | cost "
        "| cost / case |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [summary_row(v, s) for v, s in summaries.items()]
    lines += [
        "",
        "Task success = a recipe that respects every true need (allergens, other "
        "allergies, diets, time, at most one missing key ingredient), or an honest "
        "reply when no recipe is right. Checked by code against the hand-written truth, "
        "not against what the pipeline understood. Good answer = task success AND the "
        f"judge rates the final recipe >= {GOOD_SCORE} for the user's message.",
    ]
    for variant, results in all_results.items():
        lines += ["", f"## {variant}: failures by reason", ""]
        codes = failure_codes(results)
        lines += [f"- {code}: {k}" for code, k in codes.most_common()] or ["- none"]
        lines += ["", f"## {variant}: cases that failed", ""]
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
        weak = [
            r
            for r in results
            if r.success and r.judge_score is not None and r.judge_score < GOOD_SCORE
        ]
        if weak:
            lines += [
                "",
                f"## {variant}: safe and makeable, but a weak fit (judge < {GOOD_SCORE})",
                "",
            ]
            lines += [
                f"- {r.case_id}: {r.recipe_name} ({r.judge_score}: {r.judge_reason})" for r in weak
            ]
    md_path.write_text("\n".join(lines) + "\n")
    json_path.write_text(
        json.dumps(
            {
                "meta": meta,
                "summaries": summaries,
                "results": {v: [asdict(r) for r in rs] for v, rs in all_results.items()},
            },
            indent=1,
        )
    )
    return md_path, json_path


def load_e2e_cases(path: Path) -> list[E2ECase]:
    cases = [E2ECase.model_validate(item) for item in json.loads(path.read_text())]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases
