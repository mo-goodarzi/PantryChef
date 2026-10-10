"""Measure the video agent against your own blind labels (Phase 8).

1. Export (runs YouTube search once per recipe, cached in
   data/processed/eval_cache/video_source.json; LLM calls for the two checked variants):
       uv run python eval/video_eval.py export --n 40
   Writes eval/reports/video_choices.json (which video each variant shows) and the sheet
   eval/reports/video_labels.csv (no variant names, shuffled).
2. Fill in `label` in eval/reports/video_labels.csv: yes | no
   yes = you could cook this recipe by following the video (the same dish, mostly the same
   main ingredients; small variations are fine). no = another dish, a compilation, a review
   or vlog, or too little of the recipe to follow. `note` is optional.
3. Score (writes eval/reports/video_<stamp>.md):
       uv run python eval/video_eval.py score
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.video import VideoFinder, YouTubeSource
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.repository import load_steps
from pantry_chef.evaluation.e2e import load_candidate
from pantry_chef.evaluation.video_eval import (
    VARIANTS,
    CachedSource,
    pick_recipes,
    read_labels,
    run_recipe,
    sheet_rows,
    summarize,
    write_sheet,
)
from pantry_chef.llm import usage
from pantry_chef.llm.factory import create_llm
from pantry_chef.observability import configure_logging

REPORTS = Path(__file__).parent / "reports"
CHOICES = REPORTS / "video_choices.json"
SHEET = REPORTS / "video_labels.csv"
SOURCE_CACHE = Path("data/processed/eval_cache/video_source.json")


def export(n: int) -> None:
    settings = get_settings()
    if settings.youtube_api_key is None:
        raise SystemExit("YOUTUBE_API_KEY is missing in .env")
    conn = connect(settings.db_path)
    source = CachedSource(YouTubeSource(settings.youtube_api_key.get_secret_value()), SOURCE_CACHE)
    llm = create_llm(settings.model_copy(update={"llm_model": settings.video_model}))
    finders = {
        "description": VideoFinder(source, llm, settings.video_model, use_transcripts=False),
        "transcript": VideoFinder(source, llm, settings.video_model, use_transcripts=True),
    }
    start = usage.snapshot()
    runs = []
    for i, recipe_id in enumerate(pick_recipes(conn, n), start=1):
        run = run_recipe(
            load_candidate(conn, recipe_id), load_steps(conn, recipe_id), source, finders
        )
        runs.append(run)
        print(
            f"[{i}/{n}] {run.recipe.name[:50]:50} "
            + " ".join(f"{v}={run.chosen[v]}" for v in VARIANTS)
        )
    CHOICES.write_text(
        json.dumps(
            {
                "model": settings.video_model,
                "prompt": f"video_match v{finders['transcript'].prompt.version}",
                "choices": {str(r.recipe.recipe_id): r.chosen for r in runs},
                "transcript_blocked": [r.recipe.recipe_id for r in runs if r.blocked],
            },
            indent=1,
        )
        + "\n"
    )
    todo = write_sheet(sheet_rows(runs), SHEET)
    blocked = sum(r.blocked for r in runs)
    if blocked:
        print(
            f"\nWARNING: YouTube blocked transcripts for {blocked} recipes; their 'transcript' "
            "choice read only title + description. Run export again later (cached results "
            "are reused, only the missing transcripts are fetched)."
        )
    print(f"\nLLM cost ${usage.priced_cost(usage.since(start)):.3f}")
    print(f"Label {todo} rows in {SHEET} (yes/no), then: uv run python eval/video_eval.py score")


def score() -> None:
    saved = json.loads(CHOICES.read_text())
    choices = {int(k): v for k, v in saved["choices"].items()}
    result = summarize(choices, read_labels(SHEET))
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    names = {
        "youtube_top1": "YouTube's first result, no check",
        "description": "checked, title + description only",
        "transcript": "checked with the transcript (the app)",
    }
    lines = [
        f"# Video agent eval — {stamp}",
        "",
        f"Recipes: {len(choices)} | model: {saved['model']} ({saved['prompt']}) | labels: "
        f"`{SHEET.name}` (blind, by the owner)",
        "",
        "| Variant | videos shown | precision (right of shown) | wrong videos shown "
        "| recipes with a right video | unlabeled |",
        "|---|---|---|---|---|---|",
    ]
    for variant in VARIANTS:
        s = result[variant]
        precision = "-" if s["precision"] is None else f"{s['precision']:.0%}"
        lines.append(
            f"| {names[variant]} | {s['shown']} of {s['recipes']} ({s['shown_rate']:.0%}) "
            f"| {precision} | {s['wrong']} | {s['right_of_recipes']:.0%} | {s['unlabeled']} |"
        )
    blocked = saved.get("transcript_blocked", [])
    if blocked:
        lines += [
            "",
            f"**Not final:** YouTube blocked transcripts for {len(blocked)} recipes, so the "
            "transcript row read only title + description for them.",
        ]
    REPORTS.mkdir(exist_ok=True)
    md = REPORTS / f"video_{stamp}.md"
    md.write_text("\n".join(lines) + "\n")
    (REPORTS / f"video_{stamp}.json").write_text(json.dumps(result, indent=1))
    print("\n".join(lines))
    print(f"\nReport: {md}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export").add_argument("--n", type=int, default=40)
    sub.add_parser("score")
    args = parser.parse_args()
    configure_logging("WARNING")
    if args.command == "export":
        export(args.n)
    else:
        score()


if __name__ == "__main__":
    main()
