"""Helpers that measure the wish-fit check against blind human labels."""

import pytest

from pantry_chef.evaluation.cases import load_cases
from pantry_chef.evaluation.wish_fit_eval import (
    pick_positions,
    read_labels,
    summarize,
    write_sheet,
)


def test_positions_mix_the_top_and_the_bottom_of_the_shortlist():
    assert pick_positions(20, 4) == [0, 1, 18, 19]
    assert pick_positions(20, 5) == [0, 1, 2, 18, 19]
    assert pick_positions(3, 4) == [0, 1, 2]  # short list: no duplicates
    assert pick_positions(0, 4) == []


def row(row_id, label=""):
    return {
        "row_id": row_id,
        "probe_id": "w03",
        "wish": "dinner",
        "goals": "high_protein",
        "recipe_id": 100 + row_id,
        "recipe": "pepperoni pizza",
        "ingredients": "pizza dough, pepperoni",
        "description": "",
    }


def fill_labels(path, labels: dict[int, str]) -> None:
    """Fill human_label like a person editing the CSV."""
    import csv

    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["human_label"] = labels.get(int(r["row_id"]), "")
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_the_sheet_is_blind_and_only_labeled_rows_are_read(tmp_path):
    path = tmp_path / "labels.csv"
    write_sheet([row(1), row(2)], path)
    text = path.read_text()
    assert "human_label" in text.splitlines()[0]
    assert "fits" not in text and "partly" not in text  # no model verdicts in the sheet
    fill_labels(path, {1: " No "})
    [labeled] = read_labels(path)
    assert (labeled.row_id, labeled.recipe_id, labeled.label) == (1, 101, "no")


def test_unknown_labels_are_rejected(tmp_path):
    path = tmp_path / "labels.csv"
    write_sheet([row(1)], path)
    fill_labels(path, {1: "maybe"})
    with pytest.raises(ValueError, match="fits"):
        read_labels(path)


def test_summary_counts_what_the_filter_costs_and_catches():
    pairs = [
        ("no", "no"),
        ("no", "partly"),
        ("fits", "fits"),
        ("fits", "no"),  # a good recipe removed: the cost
        ("partly", "partly"),
        ("partly", "no"),
        ("fits", "none"),  # not answered: kept
    ]
    s = summarize(pairs)
    assert s["misfits_removed"] == 0.5 and s["misfits_kept"] == 1
    assert s["good_removed"] == 1 and s["partly_removed"] == 1
    assert s["agreement"] == pytest.approx(3 / 7)
    assert s["unanswered"] == 1


def test_probe_wishes_load_and_include_the_traps():
    from pathlib import Path

    probes = load_cases(Path(__file__).parents[1] / "eval" / "cases" / "wish_fit_probes.json")
    assert len(probes) == 12
    assert {"pepperoni", "bacon"} <= {item for p in probes for item in p.pantry}
