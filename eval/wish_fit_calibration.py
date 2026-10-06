"""Measure the wish-fit check against your own blind labels.

1. Export recipes to label (runs the chat search WITHOUT the wish-fit check for the
   probe wishes in eval/cases/wish_fit_probes.json; recipes from the top and the bottom
   of each shortlist, so both fits and misfits appear; no model verdicts in the sheet):
       uv run python eval/wish_fit_calibration.py export
2. Fill in `human_label` in eval/reports/wish_fit_labels.csv: fits | partly | no
   (fits = you would accept it for that wish; partly = close but with a clear gap;
    no = clearly not what was asked, e.g. pepperoni pizza for a high-protein dinner).
3. Score one or more models on your labels (writes eval/reports/wish_fit_<stamp>.md):
       uv run python eval/wish_fit_calibration.py score --models gpt-5.4-mini,gpt-5.4
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.wish_fit import WishFitChecker
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.repository import load_recipe_ingredients
from pantry_chef.db.state import open_state_db
from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.wish_fit_eval import (
    pick_positions,
    read_labels,
    summarize,
    write_sheet,
)
from pantry_chef.llm.factory import create_llm
from pantry_chef.models.recipe import Candidate
from pantry_chef.observability import configure_logging
from pantry_chef.search.engine import (
    SearchOptions,
    find_verified,
    matching_from_settings,
    semantic_from_settings,
)

ROOT = Path(__file__).parent
PROBES = ROOT / "cases" / "wish_fit_probes.json"
SHEET = ROOT / "reports" / "wish_fit_labels.csv"


def export(per_probe: int) -> None:
    settings = get_settings()
    conn = connect(settings.db_path)
    semantic = semantic_from_settings(settings)
    state = open_state_db(settings.state_db_path)
    expander, verifier = matching_from_settings(settings, conn, semantic.embedder, state)
    options = SearchOptions(use_semantic=True, use_matcher=True, top_k=20)
    rows = []
    for probe in load_cases(PROBES):
        top = find_verified(
            conn, probe.to_query(), options, semantic, verifier=verifier, expander=expander
        ).top
        for position in pick_positions(len(top), per_probe):
            c = top[position].candidate
            description = conn.execute(
                "SELECT description FROM recipes WHERE id = ?", (c.recipe_id,)
            ).fetchone()["description"]
            rows.append(
                {
                    "row_id": len(rows) + 1,
                    "probe_id": probe.id,
                    "wish": probe.preferences,
                    "goals": ",".join(g.value for g in probe.goals),
                    "recipe_id": c.recipe_id,
                    "recipe": c.name,
                    "ingredients": ", ".join(i.name for i in c.ingredients),
                    "description": (description or "")[:300],
                }
            )
    write_sheet(rows, SHEET)
    print(f"Wrote {len(rows)} rows to {SHEET}; fill in human_label (fits / partly / no).")


def candidate(conn, recipe_id: int) -> Candidate:
    row = conn.execute("SELECT name, minutes FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
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
    )


def score(models: list[str]) -> None:
    settings = get_settings()
    conn = connect(settings.db_path)
    probes = {p.id: p for p in load_cases(PROBES)}
    labeled = read_labels(SHEET)
    by_probe = defaultdict(list)
    for row in labeled:
        by_probe[row.probe_id].append(row)

    lines = [
        f"# Wish-fit check vs blind human labels ({len(labeled)} rows)",
        "",
        "| Model | agreement | misfits removed | misfits kept | good removed | partly removed |",
        "|---|---|---|---|---|---|",
    ]
    results = {}
    for model in models:
        llm = create_llm(settings.model_copy(update={"llm_model": model}))
        checker = WishFitChecker(llm, conn)  # no cache: every model answers fresh
        pairs = []
        per_row = []  # every verdict, so a disagreement can be traced to its recipe
        for probe_id, rows in by_probe.items():
            query = probes[probe_id].to_query()
            candidates = {r.recipe_id: candidate(conn, r.recipe_id) for r in rows}
            verdicts = checker.verdicts(query, list(candidates.values()))
            for r in rows:
                verdict = verdicts[r.recipe_id]
                fit = verdict.fit.value if verdict else "none"
                pairs.append((r.label, fit))
                per_row.append(
                    {
                        "row_id": r.row_id,
                        "probe_id": probe_id,
                        "recipe": candidates[r.recipe_id].name,
                        "human": r.label,
                        "model": fit,
                        "reason": verdict.reason if verdict else None,
                    }
                )
        s = summarize(pairs)
        results[model] = {**s, "per_row": per_row}
        removed = f"{s['misfits_removed']:.0%}" if s["misfits_removed"] is not None else "-"
        lines.append(
            f"| {model} | {s['agreement']:.0%} | {removed} | {s['misfits_kept']} "
            f"| {s['good_removed']} | {s['partly_removed']} |"
        )
    lines += ["", "Counts (human label / model verdict):", ""]
    for model, s in results.items():
        lines.append(f"- {model}: " + ", ".join(f"{k} {v}" for k, v in s["table"].items()))
    for model, s in results.items():
        lines += ["", f"Disagreements, {model} (human -> model):", ""]
        lines += [
            f"- {r['probe_id']} {r['recipe']}: {r['human']} -> {r['model']}"
            + (f" ({r['reason']})" if r["reason"] else "")
            for r in s["per_row"]
            if r["human"] != r["model"]
        ]

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = ROOT / "reports" / f"wish_fit_{stamp}.md"
    path.write_text("\n".join(lines) + "\n")
    (ROOT / "reports" / f"wish_fit_{stamp}.json").write_text(json.dumps(results, indent=1))
    print("\n".join(lines) + f"\n\nReport: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("--per-probe", type=int, default=4, help="rows per probe wish")
    sc = sub.add_parser("score")
    sc.add_argument("--models", default=get_settings().wish_fit_model)
    args = parser.parse_args()
    configure_logging("WARNING")
    if args.command == "export":
        export(args.per_probe)
    else:
        score(args.models.split(","))


if __name__ == "__main__":
    main()
