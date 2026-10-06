"""Compare models on the safety intake and request parsing (eval/cases/parsing.json).

Decides whether LLM_MODEL can move to a cheaper model: the cheaper one must miss no
allergen and pass about as many cases (docs/decisions.md, "Model per role").

Usage:
    uv run python eval/run_parsing_eval.py --models gpt-5.4-mini,gpt-5.4-nano
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.finder import interpret_request
from pantry_chef.agents.safety import interpret_safety_answer
from pantry_chef.config import get_settings
from pantry_chef.evaluation.parsing_eval import (
    CaseResult,
    cost_usd,
    load_parsing_cases,
    request_query,
    score_intake,
    score_request,
    summarize,
)
from pantry_chef.llm.factory import LangChainStructuredLLM, create_llm
from pantry_chef.observability import configure_logging

ROOT = Path(__file__).parent


def run_model(llm, intake_cases, request_cases) -> list[CaseResult]:
    results = []
    for case in intake_cases:
        try:
            results.append(score_intake(case, interpret_safety_answer(llm, case.answer)))
        except ValueError as error:  # no valid structured answer after the retry
            results.append(CaseResult(case.id, "intake", [str(error)], failed=True))
    for case in request_cases:
        try:
            query = request_query(interpret_request(llm, case.message))
            results.append(score_request(case, query))
        except ValueError as error:
            results.append(CaseResult(case.id, "request", [str(error)], failed=True))
    return results


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default=settings.llm_model)
    parser.add_argument("--cases", type=Path, default=ROOT / "cases" / "parsing.json")
    args = parser.parse_args()
    configure_logging("WARNING")
    intake_cases, request_cases = load_parsing_cases(args.cases)

    lines = [
        f"# Parsing evaluation — {datetime.now():%Y%m%d-%H%M}",
        "",
        f"{len(intake_cases)} safety-intake and {len(request_cases)} request cases "
        "(`eval/cases/parsing.json`), scored on the profile and query code builds. "
        "A missed allergen is the critical mistake.",
        "",
        "| Model | passed | intake | requests | allergens missed | failed calls "
        "| tokens in / out | cost | mean s per call |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    mistakes: dict[str, list[CaseResult]] = {}
    for model in args.models.split(","):
        llm = create_llm(settings.model_copy(update={"llm_model": model}))
        start = time.perf_counter()
        results = run_model(llm, intake_cases, request_cases)
        seconds = time.perf_counter() - start
        s = summarize(results)
        tokens_in = tokens_out = calls = 0
        if isinstance(llm, LangChainStructuredLLM):
            tokens_in, tokens_out, calls = llm.input_tokens, llm.output_tokens, llm.calls
        cost = cost_usd(model, tokens_in, tokens_out)
        lines.append(
            f"| {model} | {percent(s['passed'])} | {percent(s['intake_passed'])} "
            f"| {percent(s['request_passed'])} | {s['allergens_missed']} | {s['failed_calls']} "
            f"| {tokens_in:,} / {tokens_out:,} | {'-' if cost is None else f'${cost:.4f}'} "
            f"| {seconds / max(calls, 1):.1f} |"
        )
        print(lines[-1])
        mistakes[model] = [r for r in results if not r.passed]

    lines += ["", "## Mistakes per model", ""]
    for model, failed in mistakes.items():
        lines.append(f"### {model}")
        for r in failed:
            missed = [f"ALLERGEN MISSED: {code}" for code in r.allergens_missed]
            lines.append(f"- {r.case_id}: " + "; ".join(missed + r.mistakes))
        lines.append("")
    out = ROOT / "reports" / f"parsing_{datetime.now():%Y%m%d-%H%M}.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
