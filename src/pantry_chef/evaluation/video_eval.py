"""Video agent eval: how often is a shown video really the recipe? (Phase 8)

Three ways to pick a video for the same recipes, from the same YouTube results:
- youtube_top1: YouTube's first result, no check (the baseline)
- description: the agent reading only title + description (transcripts blocked)
- transcript: the agent as it runs in the app

Every video any of them would show goes into one blind sheet (no variant names, shuffled)
for the owner to label yes/no. Precision = labeled "yes" among the videos a variant shows;
shown rate = recipes that get a video at all; right of recipes = recipes that get a right
one.
"""

import csv
import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pantry_chef.agents.video import VIDEOS_PER_SEARCH, VideoFinder, VideoSource, key_terms
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.video import VideoCandidate

VARIANTS = ["youtube_top1", "description", "transcript"]
SHEET_FIELDS = [
    "recipe_id",
    "recipe_name",
    "key_ingredients",
    "first_steps",
    "video_url",
    "video_title",
    "channel",
    "label",  # yes | no (filled in by hand)
    "note",
]


def pick_recipes(conn: sqlite3.Connection, n: int, seed: int = 8) -> list[int]:
    """n random recipes with at least 5 ratings (the kind the app recommends)."""
    ids = [
        row["recipe_id"]
        for row in conn.execute(
            "SELECT recipe_id FROM recipe_stats WHERE n_ratings >= 5 ORDER BY recipe_id"
        )
    ]
    return sorted(random.Random(seed).sample(ids, n))


class CachedSource:
    """A VideoSource that keeps search results and transcripts in a JSON file, so the
    eval can be re-run (another model, another prompt) without spending quota."""

    def __init__(self, source: VideoSource, path: Path):
        self.source = source
        self.path = path
        self.data: dict[str, dict] = (
            json.loads(path.read_text()) if path.exists() else {"search": {}, "transcript": {}}
        )

    def search(self, query: str, limit: int) -> list[VideoCandidate]:
        if query not in self.data["search"]:
            found = self.source.search(query, limit)
            self.data["search"][query] = [v.model_dump() for v in found]
            self.save()
        return [VideoCandidate.model_validate(v) for v in self.data["search"][query]][:limit]

    def transcript(self, video_id: str) -> str | None:
        if video_id not in self.data["transcript"]:
            self.data["transcript"][video_id] = self.source.transcript(video_id)
            self.save()
        return self.data["transcript"][video_id]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))


@dataclass
class RecipeRun:
    recipe: Candidate
    steps: list[str]
    videos: list[VideoCandidate]  # YouTube's results, in order
    chosen: dict[str, str | None]  # variant -> video_id shown (None = only a search link)
    blocked: bool = False  # the transcript variant could not read some transcripts


def run_recipe(
    recipe: Candidate,
    steps: list[str],
    source: VideoSource,
    finders: dict[str, VideoFinder],
) -> RecipeRun:
    """The video each variant would show for one recipe."""
    videos = source.search(f"{recipe.name} recipe", VIDEOS_PER_SEARCH)
    chosen: dict[str, str | None] = {"youtube_top1": videos[0].video_id if videos else None}
    blocked = False
    for name, finder in finders.items():
        outcome = finder.find(recipe, steps)
        chosen[name] = outcome.video.video_id if outcome.video else None
        blocked = blocked or any(r.transcript_blocked for r in outcome.checked)
    return RecipeRun(recipe=recipe, steps=steps, videos=videos, chosen=chosen, blocked=blocked)


def sheet_rows(runs: list[RecipeRun], seed: int = 8) -> list[dict]:
    """One row per (recipe, video) any variant shows; shuffled, no variant names."""
    rows = []
    for run in runs:
        by_id = {v.video_id: v for v in run.videos}
        for video_id in dict.fromkeys(v for v in run.chosen.values() if v):
            video = by_id[video_id]
            rows.append(
                {
                    "recipe_id": run.recipe.recipe_id,
                    "recipe_name": run.recipe.name,
                    "key_ingredients": ", ".join(key_terms(run.recipe)),
                    "first_steps": " ".join(run.steps[:3])[:300],
                    "video_url": f"https://www.youtube.com/watch?v={video_id}",
                    "video_title": video.title,
                    "channel": video.channel,
                    "label": "",
                    "note": "",
                }
            )
    random.Random(seed).shuffle(rows)
    return rows


def video_id_of(row: dict) -> str:
    return row["video_url"].rsplit("=", 1)[-1]


def write_sheet(rows: list[dict], path: Path) -> int:
    """Write the sheet, keeping labels already given for the same recipe and video.
    Returns how many rows still need a label."""
    old = {(r["recipe_id"], video_id_of(r)): r for r in read_sheet(path)} if path.exists() else {}
    for row in rows:
        kept = old.get((str(row["recipe_id"]), video_id_of(row)))
        if kept:
            row["label"], row["note"] = kept["label"], kept["note"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return sum(not r["label"] for r in rows)


def read_sheet(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def read_labels(path: Path) -> dict[tuple[int, str], bool]:
    """(recipe_id, video_id) -> True (yes) / False (no); unlabeled rows are left out."""
    labels = {}
    for row in read_sheet(path):
        label = row["label"].strip().lower()
        if label in {"yes", "no"}:
            labels[(int(row["recipe_id"]), video_id_of(row))] = label == "yes"
    return labels


def summarize(choices: dict[int, dict[str, str | None]], labels: dict) -> dict[str, dict]:
    """Per variant: recipes, videos shown, how many are right, precision, unlabeled."""
    result = {}
    for variant in VARIANTS:
        shown = [(rid, c[variant]) for rid, c in choices.items() if c.get(variant)]
        labeled = [labels[key] for key in shown if key in labels]
        right = sum(labeled)
        result[variant] = {
            "recipes": len(choices),
            "shown": len(shown),
            "shown_rate": len(shown) / len(choices) if choices else 0.0,
            "right": right,
            "wrong": len(labeled) - right,
            "precision": right / len(labeled) if labeled else None,
            "right_of_recipes": right / len(choices) if choices else 0.0,  # useful videos
            "unlabeled": len(shown) - len(labeled),
        }
    return result
