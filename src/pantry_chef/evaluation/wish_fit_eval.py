"""Measure the wish-fit check against labels a person gives blind.

Rows: (probe wish + goals, recipe) with a human label fits | partly | no. The model's
verdict per row is compared with the label; the number that matters most is how many
good recipes ("fits") it would wrongly remove.
"""

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

LABELS = ("fits", "partly", "no")
FIELDS = [
    "row_id",
    "probe_id",
    "wish",
    "goals",
    "recipe_id",
    "recipe",
    "ingredients",
    "description",
    "human_label",
]


def pick_positions(shortlist_size: int, per_probe: int) -> list[int]:
    """Positions to label: half from the top of the shortlist (likely fits), half from the
    bottom (likely misfits), no duplicates."""
    top = list(range(min(per_probe - per_probe // 2, shortlist_size)))
    bottom = list(range(shortlist_size - per_probe // 2, shortlist_size))
    return sorted(set(top) | {p for p in bottom if p >= 0})


@dataclass
class LabeledRow:
    row_id: int
    probe_id: str
    recipe_id: int
    label: str


def write_sheet(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({**row, "human_label": ""} for row in rows)


def read_labels(path: Path) -> list[LabeledRow]:
    """Labeled rows; unlabeled ones are skipped, unknown labels are an error."""
    rows = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            label = (row["human_label"] or "").strip().lower()
            if not label:
                continue
            if label not in LABELS:
                raise ValueError(f"row {row['row_id']}: label must be one of {LABELS}")
            rows.append(
                LabeledRow(int(row["row_id"]), row["probe_id"], int(row["recipe_id"]), label)
            )
    return rows


def summarize(pairs: list[tuple[str, str]]) -> dict:
    """pairs = (human label, model verdict). The model removes exactly the "no" verdicts."""
    table = Counter(pairs)
    n = {label: sum(c for (h, _), c in table.items() if h == label) for label in LABELS}
    return {
        "table": {f"{h}/{m}": c for (h, m), c in sorted(table.items())},
        "rows": len(pairs),
        "agreement": sum(table[(x, x)] for x in LABELS) / len(pairs) if pairs else None,
        "misfits_removed": table[("no", "no")] / n["no"] if n["no"] else None,
        "misfits_kept": table[("no", "fits")] + table[("no", "partly")],
        # the cost of the filter: recipes the person would have accepted
        "good_removed": table[("fits", "no")],
        "partly_removed": table[("partly", "no")],
        "unanswered": sum(c for (_, m), c in table.items() if m == "none"),
    }
