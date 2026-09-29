import json
from pathlib import Path

from pantry_chef.agents.allergy_review import ReviewOutcome
from pantry_chef.evaluation.allergy_review_eval import CaseOutcome, action, summarize
from pantry_chef.models.verification import FailureCode, FailureReason


def test_action():
    reason = FailureReason(code=FailureCode.ALLERGEN, detail="x")
    assert action(ReviewOutcome(keep=False, reason=reason)) == "removed"
    assert action(ReviewOutcome(keep=True, warning="leave out")) == "warned"
    assert action(ReviewOutcome(keep=True)) == "silent"


def test_summarize():
    outcomes = [
        CaseOutcome(1, "required", "removed", None),
        CaseOutcome(2, "required", "silent", None),
        CaseOutcome(3, "optional", "warned", None),
        CaseOutcome(4, "none", "warned", None),
    ]
    s = summarize(outcomes)
    assert s["required_removed"] == 0.5 and s["required_shown_silently"] == 1
    assert s["optional_warned"] == 1.0 and s["none_false_warnings"] == 1
    assert s["none_clean"] == 0.0


def test_labeled_cases_are_valid():
    path = Path(__file__).parents[1] / "eval" / "cases" / "allergy_review.json"
    cases = json.loads(path.read_text())
    assert len(cases) == len({c["recipe_id"] for c in cases}) >= 30
    assert {c["label"] for c in cases} == {"required", "optional", "none"}
    assert all(c["allergens"] or c["other_allergies"] for c in cases)
