import csv
import json

from pantry_chef.evaluation.calibration import agreement, key_path, sample_judgments


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


def test_agreement_joins_the_blind_csv_with_the_key(tmp_path):
    path = tmp_path / "c.csv"
    pairs = [("5", "5"), ("4", "2"), ("2", "3"), ("1", "")]  # (judge, human)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["row", "human_score"])
        writer.writeheader()
        writer.writerows({"row": str(i), "human_score": h} for i, (_, h) in enumerate(pairs, 1))
    key = {str(i): {"judge_score": j, "judge_reason": ""} for i, (j, _) in enumerate(pairs, 1)}
    key_path(path).write_text(json.dumps(key))

    result = agreement(path)
    assert result["rated"] == 3
    assert result["good_label_agreement"] == 2 / 3
    assert result["exact_score_agreement"] == 1 / 3
    assert result["within_one_point"] == 2 / 3
    assert result["judge_more_generous"] == 1 and result["judge_stricter"] == 0


def test_export_hides_judge_scores(enriched_conn, tmp_path):
    from pantry_chef.evaluation.calibration import export_sample
    from pantry_chef.evaluation.cases import SearchCase

    results = {
        "v": [
            {
                "case_id": "c",
                "recipes": [
                    {"id": 5170, "judge_score": 5, "judge_reason": "fits"},
                    {"id": 31750, "judge_score": 2, "judge_reason": "no"},
                ],
            }
        ]
    }
    case = SearchCase(id="c", group="g", pantry=["egg"], preferences="pancakes")
    out = tmp_path / "review.csv"

    assert export_sample(enriched_conn, results, [case], out, per_score=5) == 2
    header = out.read_text().splitlines()[0]
    assert "judge" not in header
    key = json.loads(key_path(out).read_text())
    assert sorted(v["judge_score"] for v in key.values()) == [2, 5]
