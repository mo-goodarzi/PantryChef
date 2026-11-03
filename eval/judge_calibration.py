"""Check LLM judges against a human reviewer.

1. Export a blind sheet: grows eval/reports/judge_calibration.csv to ~150 (case, recipe)
   pairs, stratified over the search, e2e and safety suites and over judge scores 1-5
   (fixed seed). Existing rows and their human scores are kept:
       uv run python eval/judge_calibration.py export --results \\
           eval/reports/search_20260928-1121.json eval/reports/search_20261002-1828.json \\
           eval/reports/e2e_20261009-1112.json eval/reports/e2e_20261010-1603.json \\
           eval/reports/e2e_20261009-1047.json eval/reports/e2e_20261010-1611.json
2. Fill in `human_score` (1-5) for every row: how well the recipe fits the WISH only
   (not the pantry, allergens, diet or time; those are checked by code). Scale: 5 exactly
   what was asked, 4 good fit with a small mismatch, 3 partial fit, 2 weak fit, 1 does not
   fit / not a dish. The judge scores are in judge_calibration_key.json; do not open it.
3. Score judges on the same labels (one report, test split = the reported numbers):
       uv run python eval/judge_calibration.py score --judges \\
           openai:gpt-5.4-mini:v1,anthropic:claude-sonnet-5-5:v1,anthropic:claude-sonnet-5-5:v2
4. When changing the judge prompt, look only at dev disagreements:
       uv run python eval/judge_calibration.py dev-disagreements --judge <spec>
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.evaluation.calibration import (
    dev_disagreements,
    export_sample,
    judge_rows,
    labeled_rows,
    split_metrics,
    write_report,
)
from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.e2e import judged_case, load_e2e_cases
from pantry_chef.evaluation.judge import JudgeSpec, create_judge
from pantry_chef.llm import usage
from pantry_chef.observability import configure_logging

ROOT = Path(__file__).parent
DEFAULT_CSV = ROOT / "reports" / "judge_calibration.csv"
JUDGE_CACHE = Path("data/processed/eval_cache/judgments.json")
DEFAULT_JUDGES = (
    "openai:gpt-5.4-mini:v1,anthropic:claude-sonnet-5-5:v1,anthropic:claude-sonnet-5-5:v2"
)


def all_cases() -> dict:
    """suite -> case id -> the case as the judge sees it."""
    cases = {"search": {c.id: c for c in load_cases(ROOT / "cases" / "search.json")}}
    for suite in ("e2e", "safety"):
        e2e = load_e2e_cases(ROOT / "cases" / f"{suite}.json")
        cases[suite] = {c.id: judged_case(c) for c in e2e}
    return cases


def score(csv_path: Path, specs: list[JudgeSpec], workers: int) -> None:
    settings = get_settings()
    rows = labeled_rows(csv_path)
    if not rows:
        print(f"No human scores in {csv_path} yet.")
        return
    start = usage.snapshot()
    results = {}
    for spec in specs:
        judge = create_judge(settings, JUDGE_CACHE, spec)
        scores = judge_rows(lambda: connect(settings.db_path), judge, rows, workers)
        missing = len(rows) - len(scores)
        label = spec.label + (f" ({missing} rows not judged)" if missing else "")
        results[label] = split_metrics(rows, scores)
        print(f"{label}: test kappa {results[label]['test'].get('kappa', float('nan')):.2f}")
    meta = {
        "timestamp": datetime.now().strftime("%Y%m%d-%H%M"),
        "csv": str(csv_path),
        "labeled": len(rows),
        "split_sizes": {s: sum(r.split == s for r in rows) for s in ("dev", "test")},
        "judges": [str(s) for s in specs],
        "cost_usd": usage.priced_cost(usage.since(start)),
    }
    print(f"Report: {write_report(results, meta, ROOT / 'reports')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--results", type=Path, nargs="+", required=True)
    export.add_argument("--total", type=int, default=150, help="rows in the whole sheet")
    export.add_argument("--out", type=Path, default=DEFAULT_CSV)
    scoring = sub.add_parser("score")
    scoring.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    scoring.add_argument("--judges", default=DEFAULT_JUDGES, help="provider:model:vN,...")
    scoring.add_argument("--workers", type=int, default=8)
    dev = sub.add_parser("dev-disagreements")
    dev.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    dev.add_argument("--judge", required=True, help="provider:model:vN")
    args = parser.parse_args()

    configure_logging("WARNING")
    if args.command == "export":
        reports = [json.loads(path.read_text()) for path in args.results]
        conn = connect(get_settings().db_path)
        n = export_sample(conn, reports, all_cases(), args.out, args.total)
        print(f"Added {n} rows to {args.out}; fill in human_score (1-5).")
    elif args.command == "score":
        specs = [JudgeSpec.parse(s) for s in args.judges.split(",")]
        score(args.csv, specs, args.workers)
    else:
        settings = get_settings()
        rows = [r for r in labeled_rows(args.csv) if r.split == "dev"]  # test stays unseen
        judge = create_judge(settings, JUDGE_CACHE, JudgeSpec.parse(args.judge))
        scores = judge_rows(lambda: connect(settings.db_path), judge, rows)
        for d in dev_disagreements(rows, scores):
            print(
                f"[{d['row']} {d['suite']}] wish {d['wish']!r} -> {d['recipe']}: "
                f"human {d['human']}, judge {d['judge']} ({d['reason']})"
            )


if __name__ == "__main__":
    main()
