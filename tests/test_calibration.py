import csv
import json
import math
import random
from collections import Counter

import pytest

from pantry_chef.evaluation.calibration import (
    COLUMNS,
    LabeledRow,
    agreement_metrics,
    allocate,
    assign_splits,
    binary_agreement,
    binary_kappa,
    bootstrap_ci,
    cohen_kappa,
    confusion,
    dev_disagreements,
    export_sample,
    key_path,
    labeled_rows,
    mean_difference,
    report_verdicts,
    sample_new_pairs,
    split_metrics,
    stratified_sample,
    suite_quotas,
    weighted_kappa,
)
from pantry_chef.evaluation.cases import SearchCase

PANCAKES, WAFFLES, POACHED_EGGS = 5170, 31750, 118761

# The textbook kappa example: 50 items, judge good 25 times, human good 30 times.
# (judge, human): 20 both good, 5 only judge good, 10 only human good, 15 both not good.
TEXTBOOK = [(5, 5)] * 20 + [(5, 1)] * 5 + [(1, 5)] * 10 + [(1, 1)] * 15


# --- metrics, with hand-computed values ---------------------------------------------------


def test_binary_agreement_compares_the_good_decision_not_the_score():
    # good = score >= 4: (5,4) agree, (4,2) disagree, (2,3) agree, (1,1) agree
    assert binary_agreement([(5, 4), (4, 2), (2, 3), (1, 1)]) == 3 / 4


def test_cohen_kappa_textbook_example():
    # p_o = 35/50 = 0.7; p_e = 0.5*0.6 + 0.5*0.4 = 0.5; kappa = (0.7-0.5)/(1-0.5) = 0.4
    assert binary_kappa(TEXTBOOK) == pytest.approx(0.4)


def test_cohen_kappa_edge_cases():
    assert cohen_kappa([True, False], [False, True]) == pytest.approx(-1.0)  # p_o 0, p_e 0.5
    assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)
    assert math.isnan(cohen_kappa([True, True], [True, True]))  # chance agreement is 1


def test_weighted_kappa_hand_computed():
    assert weighted_kappa([(1, 1), (2, 2), (3, 3)]) == pytest.approx(1.0)
    # observed squared distance (16+16)/2 = 16; expected over random pairings also
    # (0+16+16+0)/4 = 8; 1 - 16/8 = -1
    assert weighted_kappa([(1, 5), (5, 1)]) == pytest.approx(-1.0)
    # judge 1,2,3,4 vs human 1,2,3,3: observed 1/4; expected 32/16 = 2; 1 - 0.125
    assert weighted_kappa([(1, 1), (2, 2), (3, 3), (4, 3)]) == pytest.approx(0.875)


def test_weighted_kappa_counts_a_near_miss_less_than_a_far_one():
    near = [(1, 1), (3, 3), (5, 4)]
    far = [(1, 1), (3, 3), (5, 1)]
    assert weighted_kappa(near) > weighted_kappa(far)


def test_confusion_matrix_cells():
    assert confusion(TEXTBOOK) == {
        "judge_good/human_good": 20,
        "judge_good/human_not_good": 5,
        "judge_not_good/human_good": 10,
        "judge_not_good/human_not_good": 15,
    }


def test_mean_difference_sign_shows_lenient_or_strict():
    assert mean_difference([(5, 4), (3, 2)]) == pytest.approx(1.0)  # lenient judge
    assert mean_difference([(2, 4)]) == pytest.approx(-2.0)  # strict judge


def test_bootstrap_ci_is_reproducible_and_brackets_the_estimate():
    low, high = bootstrap_ci(TEXTBOOK, binary_kappa, seed=1)
    assert (low, high) == bootstrap_ci(TEXTBOOK, binary_kappa, seed=1)
    assert low < 0.4 < high
    assert low >= -1 and high <= 1


def test_bootstrap_ci_of_perfect_agreement_is_a_point():
    assert bootstrap_ci([(5, 5), (1, 1)] * 5, binary_agreement) == (1.0, 1.0)


def test_bootstrap_skips_resamples_where_kappa_is_undefined():
    # a resample of only one kind of pair has no kappa; the rest still give an interval
    low, high = bootstrap_ci([(5, 5), (1, 1)], binary_kappa, resamples=200)
    assert (low, high) == (1.0, 1.0)


def test_agreement_metrics_bundle():
    m = agreement_metrics(TEXTBOOK)
    assert m["n"] == 50
    assert m["binary_agreement"] == pytest.approx(0.7)
    assert m["kappa"] == pytest.approx(0.4)
    assert m["confusion"]["judge_not_good/human_good"] == 10
    assert agreement_metrics([]) == {"n": 0}


# --- sampling ---------------------------------------------------------------------------


def test_allocate_spreads_evenly_and_moves_the_share_of_small_strata():
    assert allocate({1: 2, 2: 10, 3: 10}, 12) == {1: 2, 2: 5, 3: 5}
    assert allocate({1: 1, 2: 1}, 10) == {1: 1, 2: 1}  # not enough items: take all


def verdict_report(suite_file, scores):
    """An e2e report whose chat variant ended each case on recipe i with the given score."""
    results = [
        {"case_id": f"c{i}", "recipe_id": 100 + i, "judge_score": s, "judge_reason": "r"}
        for i, s in enumerate(scores)
    ]
    results.append(
        {"case_id": "none", "recipe_id": None, "judge_score": None, "judge_reason": None}
    )
    return {"meta": {"cases_file": suite_file}, "results": {"chat": results}}


def test_report_verdicts_reads_search_and_e2e_reports():
    search = {
        "meta": {},
        "results": {
            "v": [{"case_id": "b1", "recipes": [{"id": 1, "judge_score": 4, "judge_reason": "ok"}]}]
        },
    }
    assert [(v.suite, v.case_id, v.recipe_id) for v in report_verdicts(search)] == [
        ("search", "b1", 1)
    ]
    e2e = report_verdicts(verdict_report("eval/cases/safety.json", [5, 2]))
    assert [(v.suite, v.judge_score) for v in e2e] == [("safety", 5), ("safety", 2)]  # None skipped


def test_stratified_sample_balances_judge_scores():
    verdicts = report_verdicts(verdict_report("e2e.json", [5] * 20 + [4] * 20 + [2] * 3))
    sample = stratified_sample(verdicts, 12, random.Random(0))
    assert Counter(v.judge_score for v in sample) == {2: 3, 4: 5, 5: 4}  # leftover to low scores


def test_new_pairs_skip_pairs_already_in_the_sheet_and_respect_quotas():
    verdicts = report_verdicts(verdict_report("e2e.json", [5, 4, 3, 2, 1]))
    sample = sample_new_pairs(verdicts, {"e2e": 3}, exclude={("e2e", "c0", 100)})
    assert len(sample) == 3 and all(v.case_id != "c0" for v in sample)
    assert sample == sample_new_pairs(verdicts, {"e2e": 3}, exclude={("e2e", "c0", 100)})


def test_suite_quotas_fill_each_suite_to_a_third():
    assert suite_quotas(150, Counter(search=40)) == {"search": 10, "e2e": 50, "safety": 50}
    assert suite_quotas(150, Counter(search=60)) == {"search": 0, "e2e": 50, "safety": 50}


def test_splits_are_half_and_half_within_each_suite_and_fixed():
    rows = {i: ("search" if i <= 10 else "e2e") for i in range(1, 21)}
    splits = assign_splits(rows)
    assert splits == assign_splits(rows)
    for suite in ("search", "e2e"):
        assert Counter(splits[r] for r, s in rows.items() if s == suite) == {"dev": 5, "test": 5}


# --- the blind sheet --------------------------------------------------------------------


def write_old_sheet(path):
    """A sheet from before suites: no suite column, judge scores in the key."""
    old_columns = [c for c in COLUMNS if c != "suite"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=old_columns)
        writer.writeheader()
        row = dict.fromkeys(old_columns, "")
        row |= {"row": "1", "case_id": "b", "wish": "pancakes", "pantry": "egg, milk"}
        row |= {"recipe_id": str(PANCAKES), "recipe_name": "pancakes", "human_score": "5"}
        writer.writerow(row)
    key_path(path).write_text(json.dumps({"1": {"judge_score": 5, "judge_reason": "fits"}}))


def test_export_keeps_existing_labels_and_hides_judge_scores(enriched_conn, tmp_path):
    out = tmp_path / "review.csv"
    write_old_sheet(out)
    report = {
        "meta": {"cases_file": "eval/cases/e2e.json"},
        "results": {
            "chat": [
                {"case_id": "e1", "recipe_id": WAFFLES, "judge_score": 2, "judge_reason": "no"},
                {
                    "case_id": "e2",
                    "recipe_id": POACHED_EGGS,
                    "judge_score": 4,
                    "judge_reason": "ok",
                },
            ]
        },
    }
    cases = {
        "e2e": {
            "e1": SearchCase(id="e1", group="e2e", pantry=["flour"], preferences="waffles"),
            "e2": SearchCase(id="e2", group="e2e", pantry=["egg"], preferences="eggs"),
        }
    }

    assert export_sample(enriched_conn, [report], cases, out, total=150) == 2
    rows = list(csv.DictReader(out.open()))
    assert "judge" not in out.read_text().splitlines()[0]
    assert rows[0]["suite"] == "search" and rows[0]["human_score"] == "5"  # kept
    assert {r["row"] for r in rows} == {"1", "2", "3"}
    key = json.loads(key_path(out).read_text())
    assert key["1"]["judge_score"] == 5 and key["1"]["suite"] == "search"
    assert sorted(key[r]["judge_score"] for r in ("2", "3")) == [2, 4]
    assert all(key[r]["split"] in ("dev", "test") for r in key)
    splits = {r: key[r]["split"] for r in key}

    assert export_sample(enriched_conn, [report], cases, out, total=150) == 0  # nothing new
    assert {r: v["split"] for r, v in json.loads(key_path(out).read_text()).items()} == splits


# --- scoring judges ---------------------------------------------------------------------


def labeled(row, split, human, wish="pancakes"):
    case = SearchCase(id="c", group="search", pantry=["egg"], preferences=wish)
    return LabeledRow(row, "search", split, case, row, f"r{row}", human)


def test_labeled_rows_read_the_wish_and_split_as_shown(tmp_path):
    out = tmp_path / "review.csv"
    write_old_sheet(out)
    key = json.loads(key_path(out).read_text())
    key["1"]["split"] = "test"
    key_path(out).write_text(json.dumps(key))

    (row,) = labeled_rows(out)
    assert (row.row, row.suite, row.split, row.human_score) == (1, "search", "test", 5)
    assert row.case.judged_wish == "pancakes" and row.case.pantry == ["egg", "milk"]


def test_split_metrics_and_dev_disagreements_never_show_test_rows():
    rows = [
        labeled(1, "dev", 5),
        labeled(2, "dev", 2),
        labeled(3, "test", 5),
        labeled(4, "test", 1),
    ]
    scores = {1: (5, "fits"), 2: (4, "too kind"), 3: (2, "too strict"), 4: (1, "no")}

    metrics = split_metrics(rows, scores)
    assert (metrics["dev"]["n"], metrics["test"]["n"], metrics["all"]["n"]) == (2, 2, 4)
    assert metrics["dev"]["binary_agreement"] == 0.5
    assert metrics["test"]["confusion"]["judge_not_good/human_good"] == 1

    assert [d["row"] for d in dev_disagreements(rows, scores)] == [2]  # row 3 is test
