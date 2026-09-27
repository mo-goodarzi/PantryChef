"""Compare amounts with unit conversion (pint).

Counts ("3 eggs", "2 large", "1 clove") have no physical unit; kitchen units that pint
does not know ("pinch", "dash") are defined here.
"""

from dataclasses import dataclass

import pint

UREG = pint.UnitRegistry()
UREG.define("pinch = teaspoon / 16")
UREG.define("dash = teaspoon / 8")
UREG.define("stick_of_butter = 0.5 * cup")

# Words that mean "a number of items", not a physical unit.
COUNT_WORDS = frozenset(
    {
        "count",
        "piece",
        "pieces",
        "whole",
        "large",
        "medium",
        "small",
        "clove",
        "cloves",
        "slice",
        "slices",
        "item",
        "items",
        "each",
        "egg",
        "eggs",
    }
)
UNIT_ALIASES = {
    "tbsp": "tablespoon",
    "tbs": "tablespoon",
    "tablespoons": "tablespoon",
    "T": "tablespoon",
    "tsp": "teaspoon",
    "teaspoons": "teaspoon",
    "t": "teaspoon",
    "c": "cup",
    "cups": "cup",
    "oz": "ounce",
    "ounces": "ounce",
    "fl oz": "fluid_ounce",
    "lb": "pound",
    "lbs": "pound",
    "pounds": "pound",
    "g": "gram",
    "grams": "gram",
    "kg": "kilogram",
    "ml": "milliliter",
    "l": "liter",
    "liters": "liter",
    "pinches": "pinch",
    "dashes": "dash",
    "stick": "stick_of_butter",
    "sticks": "stick_of_butter",
}


@dataclass(frozen=True)
class Amount:
    value: float
    unit: str | None  # None = count


def is_count(unit: str | None) -> bool:
    return unit is None or unit.strip().lower() in COUNT_WORDS


def to_quantity(amount: Amount) -> pint.Quantity | None:
    """A pint quantity, or None for counts and unknown units."""
    if is_count(amount.unit):
        return None
    unit = (amount.unit or "").strip()
    unit = UNIT_ALIASES.get(unit, UNIT_ALIASES.get(unit.lower(), unit.lower()))
    try:
        return amount.value * UREG(unit)
    except (pint.errors.UndefinedUnitError, AttributeError, ValueError):
        return None


def available_ratio(have: Amount, need: Amount) -> float | None:
    """have / need, or None when the amounts cannot be compared
    (count vs weight, volume vs weight, unknown unit)."""
    if need.value <= 0:
        return None
    if is_count(have.unit) and is_count(need.unit):
        return have.value / need.value
    have_q, need_q = to_quantity(have), to_quantity(need)
    if have_q is None or need_q is None:
        return None
    if have_q.dimensionality != need_q.dimensionality:
        return None  # e.g. grams vs cups: would need a density, so we do not guess
    return float((have_q / need_q).to_base_units().magnitude)
