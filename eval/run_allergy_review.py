"""Evaluate the final allergy review on eval/cases/allergy_review.json.

Compares the code-only keyword scan (warns on any mention) with the LLM review.

Usage:
    uv run python eval/run_allergy_review.py                      # LLM_MODEL / ALLERGY_REVIEW_MODEL
    uv run python eval/run_allergy_review.py --models gpt-5.4-mini,gpt-5.4
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.allergy_review import AllergyReviewer, code_only_outcomes
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.repository import load_recipe_ingredients
from pantry_chef.evaluation.allergy_review_eval import CaseOutcome, action, summarize
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.llm.factory import create_llm
from pantry_chef.models.query import RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.observability import configure_logging

ROOT = Path(__file__).parent


def candidate(conn, recipe_id: int) -> Candidate:
    row = conn.execute("SELECT name, minutes FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
    ingredients = load_recipe_ingredients(conn, [recipe_id]).get(recipe_id, [])
    return Candidate(
        recipe_id=recipe_id,
        name=row["name"],
        minutes=row["minutes"],
        ingredients=ingredients,
        have_key=0,
        total_key=0,
        coverage=0,
        ingredient_score=0,
        final_score=0,
    )


def run(conn, cases: list[dict], review) -> list[CaseOutcome]:
    """`review(query, candidates) -> outcomes`; cases with the same allergies are batched."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for case in cases:
        groups[(tuple(case["allergens"]), tuple(case["other_allergies"]))].append(case)
    results = []
    for (codes, words), group in groups.items():
        query = RecipeQuery(
            ingredients=[],
            required_allergen_free=[Allergen(c) for c in codes],
            other_allergies=list(words),
        )
        outcomes = review(query, [candidate(conn, c["recipe_id"]) for c in group])
        for case in group:
            o = outcomes[case["recipe_id"]]
            detail = o.warning or (o.reason.detail if o.reason else None)
            results.append(CaseOutcome(case["recipe_id"], case["label"], action(o), detail))
    return results


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default=settings.allergy_review_model)
    args = parser.parse_args()
    configure_logging("WARNING")
    conn = connect(settings.db_path)
    cases = json.loads((ROOT / "cases" / "allergy_review.json").read_text())

    variants = {"keyword scan (code only)": lambda q, cs: code_only_outcomes(conn, q, cs)}
    for model in args.models.split(","):
        llm = create_llm(settings.model_copy(update={"llm_model": model}))
        variants[f"LLM review ({model})"] = AllergyReviewer(llm, conn).review  # no cache

    lines = [
        f"# Allergy review evaluation — {datetime.now():%Y%m%d-%H%M}",
        "",
        f"{len(cases)} hand-labeled real recipes (`eval/cases/allergy_review.json`): "
        "required = must be removed, optional = must be kept with a warning, "
        "none = should be kept without warning.",
        "",
        "| Variant | required removed | required shown silently | optional warned "
        "| optional removed | optional silent | none clean | none false warnings "
        "| none false removals |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    details = {}
    for name, review in variants.items():
        outcomes = run(conn, cases, review)
        s = summarize(outcomes)
        details[name] = outcomes
        lines.append(
            f"| {name} | {s['required_removed']:.0%} | {s['required_shown_silently']} "
            f"| {s['optional_warned']:.0%} | {s['optional_removed']} | {s['optional_silent']} "
            f"| {s['none_clean']:.0%} | {s['none_false_warnings']} | {s['none_false_removals']} |"
        )
        print(lines[-1])
    lines += ["", "## Mistakes per variant", ""]
    expected = {"required": "removed", "optional": "warned", "none": "silent"}
    for name, outcomes in details.items():
        lines.append(f"### {name}")
        for o in outcomes:
            if o.action != expected[o.label]:
                lines.append(f"- {o.recipe_id} ({o.label} -> {o.action}): {o.detail}")
        lines.append("")
    out = ROOT / "reports" / f"allergy_review_{datetime.now():%Y%m%d-%H%M}.md"
    out.write_text("\n".join(lines).rstrip() + "\n")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    main()
