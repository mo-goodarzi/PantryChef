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


def unit_kind(unit: str | None) -> str | None:
    """ "count", "weight" or "volume"; None for units we cannot read ("can", "bunch")."""
    if is_count(unit):
        return "count"
    quantity = to_quantity(Amount(1, unit))
    if quantity is None:
        return None
    if quantity.check("[mass]"):
        return "weight"
    if quantity.check("[volume]"):
        return "volume"
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
        return None  # e.g. grams vs cups: needs the ingredient's weights (approximate_ratio)
    return float((have_q / need_q).to_base_units().magnitude)


@dataclass(frozen=True)
class Weights:
    """Approximate grams of one ingredient (LLM labels, ingredients/weights.py)."""

    piece_g: float | None = None  # one item: one egg, one chicken breast
    cup_g: float | None = None  # one US cup, as recipes measure it (shredded, chopped)


def to_grams(amount: Amount, weights: Weights) -> float | None:
    """The amount in grams: weights directly, counts and volumes through the ingredient's
    typical weights; None when that is not known."""
    if is_count(amount.unit):
        return amount.value * weights.piece_g if weights.piece_g else None
    quantity = to_quantity(amount)
    if quantity is None:
        return None
    if quantity.check("[mass]"):
        return float(quantity.to("gram").magnitude)
    if quantity.check("[volume]") and weights.cup_g:
        return float(quantity.to("cup").magnitude) * weights.cup_g
    return None


def approximate_ratio(have: Amount, need: Amount, weights: Weights | None) -> float | None:
    """have / need like available_ratio, which is used whenever it can compare the two
    (same kind of unit: exact). Otherwise both are converted to grams with the
    ingredient's typical weights (300 g of chicken vs "2 chicken breasts")."""
    exact = available_ratio(have, need)
    if exact is not None or weights is None or need.value <= 0:
        return exact
    have_g, need_g = to_grams(have, weights), to_grams(need, weights)
    if have_g is None or not need_g:
        return None
    return have_g / need_g
