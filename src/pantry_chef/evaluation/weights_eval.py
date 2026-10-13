"""Measure the ingredient weights (ingredients/weights.py) two ways.

1. Accuracy: the labels against hand-written reference weights (eval/cases/weights.json),
   as the error in percent; within 25% counts as close enough for a "do you have at least
   half?" check.
2. Coverage: of the recipe lines the quantity question can be about (key, amount matters,
   not a staple, with an amount), how many an answer in grams, or as a number of items,
   can be compared with: before (same kind of unit only) and with the weights.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from pantry_chef.ingredients.quantities import Amount, Weights, approximate_ratio

CLOSE_ENOUGH = 0.25  # relative error


@dataclass(frozen=True)
class WeightCheck:
    name: str
    measure: str  # "piece_g" | "cup_g"
    reference: float
    label: float | None

    @property
    def error(self) -> float | None:
        """Relative error of the label; None when there is no label."""
        if self.label is None:
            return None
        return abs(self.label - self.reference) / self.reference


def weight_checks(reference: dict, weights: dict[str, Weights]) -> list[WeightCheck]:
    return [
        WeightCheck(name, measure, value, getattr(weights.get(name, Weights()), measure))
        for measure in ("piece_g", "cup_g")
        for name, value in reference[measure].items()
    ]


@dataclass(frozen=True)
class Accuracy:
    checked: int
    missing: int  # reference items without a label
    median_error: float
    close: int  # within CLOSE_ENOUGH


def accuracy(checks: list[WeightCheck]) -> Accuracy:
    errors = [c.error for c in checks if c.error is not None]
    return Accuracy(
        checked=len(errors),
        missing=len(checks) - len(errors),
        median_error=median(errors) if errors else 0.0,
        close=sum(e <= CLOSE_ENOUGH for e in errors),
    )


# What the user answers: 1 item, or 100 g. Only whether a ratio exists matters here.
ANSWERS = {"grams": Amount(100, "g"), "number": Amount(1, None)}

LINES_SQL = """
SELECT i.canonical_name AS name, ri.quantity, ri.unit, COUNT(*) AS n
FROM recipe_ingredients ri JOIN ingredients i ON i.id = ri.ingredient_id
WHERE ri.is_key = 1 AND i.quantity_matters = 1 AND i.is_staple = 0
  AND ri.quantity > 0
GROUP BY 1, 2, 3
"""


@dataclass(frozen=True)
class Coverage:
    lines: int
    before: dict[str, int]  # answer kind -> comparable lines, same kind of unit only
    after: dict[str, int]  # ... with the weights


def coverage(rows: Iterable[sqlite3.Row], weights: dict[str, Weights]) -> Coverage:
    lines = 0
    before = dict.fromkeys(ANSWERS, 0)
    after = dict.fromkeys(ANSWERS, 0)
    for row in rows:
        need = Amount(row["quantity"], row["unit"])
        lines += row["n"]
        for kind, have in ANSWERS.items():
            if approximate_ratio(have, need, None) is not None:
                before[kind] += row["n"]
            if approximate_ratio(have, need, weights.get(row["name"])) is not None:
                after[kind] += row["n"]
    return Coverage(lines=lines, before=before, after=after)


def recipe_lines(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(LINES_SQL).fetchall()
