"""Measure the final allergy review on hand-labeled real recipes.

Each case: a recipe, an allergy set and the correct label:
- required: the steps always add the allergen -> the recipe must be REMOVED
- optional: only a garnish / variation / choice -> must be KEPT WITH A WARNING
- none: no allergen (incl. "walnut-sized balls") -> should be KEPT WITHOUT warning
"""

from collections import Counter
from dataclasses import dataclass

from pantry_chef.agents.allergy_review import ReviewOutcome

# What happened to a recipe: removed, kept with a warning, or kept silently.
REMOVED, WARNED, SILENT = "removed", "warned", "silent"


def action(outcome: ReviewOutcome) -> str:
    if not outcome.keep:
        return REMOVED
    return WARNED if outcome.warning else SILENT


@dataclass
class CaseOutcome:
    recipe_id: int
    label: str  # required | optional | none
    action: str  # removed | warned | silent
    detail: str | None


def summarize(outcomes: list[CaseOutcome]) -> dict:
    """Counts per (label, action) plus the three numbers that matter most."""
    table = Counter((o.label, o.action) for o in outcomes)
    required = sum(o.label == "required" for o in outcomes)
    optional = sum(o.label == "optional" for o in outcomes)
    none = sum(o.label == "none" for o in outcomes)
    return {
        "table": {f"{label}/{act}": n for (label, act), n in sorted(table.items())},
        # safety: a required allergen shown with no warning at all is the worst outcome
        "required_removed": table[("required", REMOVED)] / required if required else None,
        "required_shown_silently": table[("required", SILENT)],
        "optional_warned": table[("optional", WARNED)] / optional if optional else None,
        "optional_removed": table[("optional", REMOVED)],
        "optional_silent": table[("optional", SILENT)],
        "none_clean": table[("none", SILENT)] / none if none else None,
        "none_false_warnings": table[("none", WARNED)],
        "none_false_removals": table[("none", REMOVED)],
    }
