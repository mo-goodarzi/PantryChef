import csv

from pantry_chef.evaluation.calibration import agreement, sample_judgments


def results_with_scores(scores):
    recipes = [{"id": i, "judge_score": s, "judge_reason": "r"} for i, s in enumerate(scores)]
    return {
        "v1": [{"case_id": "c", "recipes": recipes}],
        "v2": [{"case_id": "c", "recipes": recipes[:2]}],
    }  # duplicates across variants


def test_sample_is_stratified_and_unique():
    sample = sample_judgments(results_with_scores([5, 5, 5, 4, 1, 1, 0]), per_score=2)
    scores = sorted(s["judge_score"] for s in sample)
    assert scores == [1, 1, 4, 5, 5]  # max 2 per score, unjudged (0) skipped
    assert len({(s["case_id"], s["recipe_id"]) for s in sample}) == len(sample)


def test_agreement(tmp_path):
    path = tmp_path / "c.csv"
    rows = [("5", "5"), ("4", "2"), ("2", "3"), ("1", "")]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["judge_score", "human_score"])
        w.writeheader()
        w.writerows({"judge_score": j, "human_score": h} for j, h in rows)
    result = agreement(path)
    assert result["rated"] == 3
    assert result["good_label_agreement"] == 2 / 3
    assert result["exact_score_agreement"] == 1 / 3
    assert result["within_one_point"] == 2 / 3
    assert result["judge_more_generous"] == 1 and result["judge_stricter"] == 0
