"""Measure the ingredient weights: accuracy against reference values, and how many recipe
lines an amount in grams or a number of items can be checked against.

    uv run python eval/weights_eval.py        # writes eval/reports/weights_<stamp>.md
"""

import json
from datetime import datetime
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.evaluation.weights_eval import (
    CLOSE_ENOUGH,
    accuracy,
    coverage,
    recipe_lines,
    weight_checks,
)
from pantry_chef.ingredients.weights import load_weights

CASES = Path("eval/cases/weights.json")
REPORTS = Path("eval/reports")


def pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def main() -> None:
    reference = json.loads(CASES.read_text())
    weights = load_weights()
    checks = weight_checks(reference, weights)
    acc = accuracy(checks)
    conn = connect(get_settings().db_path)
    cov = coverage(recipe_lines(conn), weights)
    conn.close()

    lines = [
        f"# Ingredient weights — {datetime.now():%Y%m%d-%H%M}",
        "",
        f"Seed: {len(weights):,} ingredients. Reference: `{CASES}` ({reference['source']})",
        "",
        "## Accuracy against the reference",
        "",
        f"| checked | no label | median error | within {CLOSE_ENOUGH:.0%} |",
        "|---|---|---|---|",
        f"| {acc.checked} | {acc.missing} | {acc.median_error:.1%} | "
        f"{acc.close} ({pct(acc.close, acc.checked)}) |",
        "",
        "| ingredient | measure | reference g | label g | error |",
        "|---|---|---|---|---|",
    ]
    for c in sorted(checks, key=lambda c: -(c.error or 0)):
        label = "-" if c.label is None else f"{c.label:g}"
        error = "-" if c.error is None else f"{c.error:.0%}"
        lines.append(f"| {c.name} | {c.measure} | {c.reference:g} | {label} | {error} |")
    lines += [
        "",
        "## Recipe lines an answer can be checked against",
        "",
        f"Key, amount-matters, non-staple ingredient lines with an amount: {cov.lines:,}.",
        "",
        "| answer | same kind of unit only | with the weights |",
        "|---|---|---|",
    ]
    for kind in cov.before:
        lines.append(
            f"| {kind} | {pct(cov.before[kind], cov.lines)} | {pct(cov.after[kind], cov.lines)} |"
        )
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"weights_{datetime.now():%Y%m%d-%H%M}.md"
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nWritten to {path}")


if __name__ == "__main__":
    main()
