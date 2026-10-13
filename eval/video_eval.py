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
3. If YouTube blocked transcripts during the export, later (when it answers again) fill in
   only the transcript choice of those recipes; other choices and your labels stay:
       uv run python eval/video_eval.py rerun-blocked
   then label any new rows.
4. Score (writes eval/reports/video_<stamp>.md):
       uv run python eval/video_eval.py score
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pantry_chef.agents.video import VIDEOS_PER_SEARCH, VideoFinder, YouTubeSource
from pantry_chef.config import get_settings
from pantry_chef.db.connection import connect
from pantry_chef.db.repository import load_steps
from pantry_chef.evaluation.e2e import load_candidate
from pantry_chef.evaluation.video_eval import (
    VARIANTS,
    CachedSource,
    RecipeRun,
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


def setup() -> tuple[sqlite3.Connection, CachedSource, dict[str, VideoFinder]]:
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
    return conn, source, finders


def save_choices(model: str, prompt: str, choices: dict, blocked: list[int]) -> None:
    saved = {"model": model, "prompt": prompt, "choices": choices, "transcript_blocked": blocked}
    CHOICES.write_text(json.dumps(saved, indent=1) + "\n")


def warn_if_blocked(blocked: int) -> None:
    if blocked:
        print(
            f"\nWARNING: YouTube blocked transcripts for {blocked} recipes; their 'transcript' "
            "choice read only title + description. Later, run: "
            "uv run python eval/video_eval.py rerun-blocked"
        )


def export(n: int) -> None:
    conn, source, finders = setup()
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
    finder = finders["transcript"]
    save_choices(
        finder.model,
        f"video_match v{finder.prompt.version}",
        {str(r.recipe.recipe_id): r.chosen for r in runs},
        [r.recipe.recipe_id for r in runs if r.blocked],
    )
    todo = write_sheet(sheet_rows(runs), SHEET)
    warn_if_blocked(sum(r.blocked for r in runs))
    print(f"\nLLM cost ${usage.priced_cost(usage.since(start)):.3f}")
    print(f"Label {todo} rows in {SHEET} (yes/no), then: uv run python eval/video_eval.py score")


def rerun_blocked() -> None:
    """The transcript choice again, only for recipes whose transcripts were blocked."""
    saved = json.loads(CHOICES.read_text())
    conn, source, finders = setup()
    start = usage.snapshot()
    still_blocked = []
    for recipe_id in saved["transcript_blocked"]:
        recipe = load_candidate(conn, recipe_id)
        outcome = finders["transcript"].find(recipe, load_steps(conn, recipe_id))
        chosen = outcome.video.video_id if outcome.video else None
        saved["choices"][str(recipe_id)]["transcript"] = chosen
        if any(r.transcript_blocked for r in outcome.checked):
            still_blocked.append(recipe_id)
        print(f"{recipe.name[:50]:50} transcript={chosen}")
    save_choices(saved["model"], saved["prompt"], saved["choices"], still_blocked)
    runs = [
        RecipeRun(
            recipe=(recipe := load_candidate(conn, int(recipe_id))),
            steps=load_steps(conn, recipe.recipe_id),
            videos=source.search(f"{recipe.name} recipe", VIDEOS_PER_SEARCH),
            chosen=chosen,
        )
        for recipe_id, chosen in saved["choices"].items()
    ]
    todo = write_sheet(sheet_rows(runs), SHEET)  # keeps every label already given
    warn_if_blocked(len(still_blocked))
    print(f"\nLLM cost ${usage.priced_cost(usage.since(start)):.3f}")
    print(f"{todo} rows still need a label in {SHEET}")


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
    sub.add_parser("rerun-blocked")
    sub.add_parser("score")
    args = parser.parse_args()
    configure_logging("WARNING")
    if args.command == "export":
        export(args.n)
    elif args.command == "rerun-blocked":
        rerun_blocked()
    else:
        score()


if __name__ == "__main__":
    main()
