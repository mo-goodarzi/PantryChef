"""Check the LLM judge against a human: export a blind sample, then score agreement.

The reviewer's CSV does not show the judge's score or reason (so they cannot anchor on
it); those are kept in a separate key file and joined back by row number.
"""

import csv
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

from pantry_chef.db.repository import recipe_summaries
from pantry_chef.evaluation.cases import SearchCase
from pantry_chef.evaluation.metrics import GOOD_SCORE

COLUMNS = [
    "row",
    "case_id",
    "wish",
    "pantry",
    "recipe_id",
    "recipe_name",
    "meal_type",
    "cuisine",
    "description",
    "ingredients",
    "human_score",
    "notes",
]


def key_path(csv_path: Path) -> Path:
    """Where the hidden judge scores for a review CSV are stored."""
    return csv_path.with_name(csv_path.stem + "_key.json")


def sample_judgments(results: dict, per_score: int, seed: int = 42) -> list[dict]:
    """Stratified sample of unique (case, recipe) judgments: up to `per_score` per score."""
    seen: dict[tuple[str, int], dict] = {}
    for variant_results in results.values():
        for case_result in variant_results:
            for recipe in case_result["recipes"]:
                if recipe["judge_score"]:
                    seen[(case_result["case_id"], recipe["id"])] = recipe
    by_score: dict[int, list] = defaultdict(list)
    for (case_id, recipe_id), recipe in sorted(seen.items()):
        by_score[recipe["judge_score"]].append((case_id, recipe_id, recipe))
    rng = random.Random(seed)
    sample = []
    for score in sorted(by_score):
        items = by_score[score]
        sample += rng.sample(items, min(per_score, len(items)))
    return [{"case_id": c, "recipe_id": r, **recipe} for c, r, recipe in sample]


def export_sample(
    conn: sqlite3.Connection, results: dict, cases: list[SearchCase], out: Path, per_score: int
) -> int:
    """Write the blind review CSV and its key file. Returns the number of rows."""
    by_id = {case.id: case for case in cases}
    sample = sample_judgments(results, per_score)
    random.Random(0).shuffle(sample)  # rows must not be grouped by judge score
    summaries = recipe_summaries(conn, [s["recipe_id"] for s in sample])

    rows, key = [], {}
    for number, item in enumerate(sample, start=1):
        case, summary = by_id[item["case_id"]], summaries[item["recipe_id"]]
        rows.append(
            {
                "row": number,
                "case_id": case.id,
                "wish": case.preferences,
                "pantry": ", ".join(case.pantry),
                "recipe_id": item["recipe_id"],
                "recipe_name": summary["name"],
                "meal_type": summary["meal_type"],
                "cuisine": summary["cuisine"],
                "description": summary["description"][:200],
                "ingredients": ", ".join(summary["ingredients"]),
                "human_score": "",
                "notes": "",
            }
        )
        key[str(number)] = {
            "judge_score": item["judge_score"],
            "judge_reason": item["judge_reason"],
        }

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    key_path(out).write_text(json.dumps(key, indent=1) + "\n")
    return len(rows)


def agreement(path: Path) -> dict:
    """Agreement between judge and human on rows where the human filled in a score."""
    key = json.loads(key_path(path).read_text())
    rows = [r for r in csv.DictReader(path.open()) if r["human_score"].strip()]
    if not rows:
        return {"rated": 0}
    pairs = [(int(key[r["row"]]["judge_score"]), int(r["human_score"])) for r in rows]
    good = [(j >= GOOD_SCORE) == (h >= GOOD_SCORE) for j, h in pairs]
    return {
        "rated": len(pairs),
        "good_label_agreement": sum(good) / len(pairs),
        "exact_score_agreement": sum(j == h for j, h in pairs) / len(pairs),
        "within_one_point": sum(abs(j - h) <= 1 for j, h in pairs) / len(pairs),
        "judge_more_generous": sum(j >= GOOD_SCORE > h for j, h in pairs),
        "judge_stricter": sum(h >= GOOD_SCORE > j for j, h in pairs),
    }
