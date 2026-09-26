"""Check the LLM judge against a human reviewer.

1. Export a sample of judge verdicts from an eval run (spread across scores 1-5):
       uv run python eval/judge_calibration.py export --results eval/reports/search_<stamp>.json
2. Fill in `human_score` (1-5, same rubric as the judge) in the CSV.
3. Score agreement:
       uv run python eval/judge_calibration.py score
"""

import argparse
import json
from pathlib import Path

from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.evaluation.calibration import agreement, export_sample
from pantry_chef.evaluation.cases import load_cases

ROOT = Path(__file__).parent
DEFAULT_CSV = ROOT / "reports" / "judge_calibration.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--results", type=Path, required=True)
    export.add_argument("--per-score", type=int, default=8, help="max verdicts per score 1-5")
    export.add_argument("--out", type=Path, default=DEFAULT_CSV)
    score = sub.add_parser("score")
    score.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    if args.command == "export":
        results = json.loads(args.results.read_text())["results"]
        cases = load_cases(ROOT / "cases" / "search.json")
        n = export_sample(connect(get_settings().db_path), results, cases, args.out, args.per_score)
        print(f"Wrote {n} verdicts to {args.out}; fill in human_score (1-5).")
    else:
        for key, value in agreement(args.csv).items():
            print(f"  {key:<24}{value:.0%}" if isinstance(value, float) else f"  {key:<24}{value}")


if __name__ == "__main__":
    main()
