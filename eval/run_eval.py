"""Run an evaluation suite and write a report to eval/reports/.

Usage:
    uv run python eval/run_eval.py --suite search --variants coverage
"""

import argparse
from datetime import datetime
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.judge import CachedJudge
from pantry_chef.evaluation.search_eval import evaluate_variant, summarize, write_report
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging
from pantry_chef.search.engine import SearchOptions, find_recipes

ROOT = Path(__file__).parent
VARIANTS = {
    "coverage": SearchOptions(),
}


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=["search"], default="search")
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--cases", type=Path, default=ROOT / "cases" / "search.json")
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--db", type=Path, default=settings.db_path)
    parser.add_argument(
        "--judge-cache", type=Path, default=Path("data/processed/eval_cache/judgments.json")
    )
    args = parser.parse_args()

    configure_logging("WARNING")
    conn = connect(args.db)
    cases = load_cases(args.cases)[: args.limit]
    judge = CachedJudge(create_llm(settings), args.judge_cache)

    all_results = {}
    for variant in args.variants.split(","):
        options = VARIANTS[variant]
        all_results[variant] = evaluate_variant(
            conn, cases, variant, lambda q, o=options: find_recipes(conn, q, o), judge
        )
        s = summarize(all_results[variant])
        print(
            f"{variant:>10}: hit@5 {s['hit_at_5']:.0%}  MRR {s['mrr']:.2f}  "
            f"judge {s['mean_judge_score']:.2f}  allergen violations {s['allergen_violations']}"
        )

    meta = {
        "timestamp": datetime.now().strftime("%Y%m%d-%H%M"),
        "cases": len(cases),
        "judge_model": settings.llm_model,
        "judge_prompt_version": judge.prompt.version,
        "variants": list(all_results),
    }
    md_path, _ = write_report(all_results, ROOT / "reports", meta)
    print(f"\nReport: {md_path}  (judge calls this run: {judge.calls})")


if __name__ == "__main__":
    main()
