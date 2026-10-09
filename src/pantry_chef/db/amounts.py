"""Ingredient amounts from the Kaggle "Food.com Recipes with Search Terms and Tags" file.

`RAW_recipes.csv` has ingredient names only. `recipes_w_search_terms.csv` (same author, same
recipe ids) also has `ingredients_raw_str`: the original lines with amounts and units,
e.g. "4   cups    blueberries, fresh or frozen ", plus `servings`. We store the cleaned line
next to each ingredient and show it to the user; nothing is estimated.

Aligning names to lines (measured on the full data, docs/decisions.md): the line list has
blank section headers ("For the sauce:" becomes "") and repeats (salt listed twice) that the
name list drops, so positions do not always match. Each name takes the next line that
mentions it, in order; a line is never attached to a name it does not mention, so a wrong
amount is never shown (the name is shown alone instead).

Each line is also parsed into a number and a unit in code ("1 1/2 cups milk" -> 1.5 cup), so
the verifier can compare it with what the user has:
- ranges take the lower number ("3-4 lbs" -> 3 pound): a recipe is rejected less often;
- packages multiply out ("2 (8 ounce) bottles" -> 16 ounce);
- containers without a size ("1 can", "2 packets") keep their word as the unit, which the
  verifier cannot compare, so it only notes it; a bare number is a count ("6 eggs").

Servings of 1 are the site's default when the author left it empty (17% of recipes, median
784 g per serving against ~250 g otherwise), so they are stored as unknown.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from fractions import Fraction
from itertools import groupby
from pathlib import Path

import pandas as pd

from pantry_chef.db.parsing import clean_text
from pantry_chef.ingredients.quantities import (
    COUNT_WORDS,
    UNIT_ALIASES,
    Amount,
    is_count,
    to_quantity,
)
from pantry_chef.observability import get_logger

log = get_logger("db.amounts")

AMOUNT_COLUMNS = ["id", "ingredients_raw_str", "servings"]
QUANTITY_SOURCE = "recipe_line"

NUMBER = r"\d+ \d+/\d+|\d+/\d+|\d+(?:\.\d+)?"  # "1 1/2", "1/2", "2", "1.5"
LEADING = re.compile(rf"^(?P<value>{NUMBER})(?:-(?:{NUMBER}))? ?(?P<rest>.*)$")
PACKAGE = re.compile(rf"^\((?P<value>{NUMBER})(?:-(?:{NUMBER}))? (?P<unit>[a-z. ]+)\) ?", re.I)

# Units pint can convert (names after UNIT_ALIASES).
KITCHEN_UNITS = frozenset(
    {
        "cup",
        "tablespoon",
        "teaspoon",
        "ounce",
        "fluid_ounce",
        "pound",
        "gram",
        "kilogram",
        "milliliter",
        "liter",
        "pint",
        "quart",
        "gallon",
        "pinch",
        "dash",
        "stick_of_butter",
    }
)
MORE_UNIT_ALIASES = {
    "fl oz": "fluid_ounce",
    "fluid ounce": "fluid_ounce",
    "fluid ounces": "fluid_ounce",
    "litre": "liter",
    "litres": "liter",
    "millilitre": "milliliter",
    "millilitres": "milliliter",
}
# Amounts of packed or loose things without a size: kept as the unit, never compared.
CONTAINERS = frozenset(
    {
        "can",
        "cans",
        "jar",
        "jars",
        "package",
        "packages",
        "pkg",
        "packet",
        "packets",
        "envelope",
        "envelopes",
        "box",
        "boxes",
        "bottle",
        "bottles",
        "bag",
        "bags",
        "container",
        "containers",
        "carton",
        "cartons",
        "bunch",
        "bunches",
        "head",
        "heads",
        "sprig",
        "sprigs",
        "stalk",
        "stalks",
        "handful",
        "handfuls",
        "drop",
        "drops",
        "sheet",
        "sheets",
        "loaf",
        "loaves",
        "inch",
        "inches",
    }
)


@dataclass
class AmountsSummary:
    recipes_in_db: int = 0
    recipes_with_amounts: int = 0
    rows_in_db: int = 0
    rows_with_amounts: int = 0
    rows_with_quantity: int = 0  # the line has a number
    rows_comparable: int = 0  # ... in a unit the verifier can compare (or a count)


def clean_line(raw: str) -> str | None:
    """Collapse the dataset's padding: "1 -2   clove    garlic " -> "1-2 clove garlic"."""
    text = clean_text(raw)
    if text is None:
        return None
    return re.sub(r"(\d) ?- ?(\d)", r"\1-\2", text)


def mentions(name: str, line: str) -> bool:
    """True if the line mentions one of the name's words (by its first 4 letters, so
    "tomatoes" matches "tomato" and "chilies" matches "chili")."""
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if len(w) > 2]
    if not words:
        return True
    text = line.lower()
    return any(word[:4] in text for word in words)


def align_lines(names: list[str], raw_lines: list[str]) -> list[str | None]:
    """One cleaned line per ingredient name, or None where no line fits."""
    lines = [line for line in (clean_line(raw) for raw in raw_lines) if line]
    result: list[str | None] = []
    start = 0
    for name in names:
        found = next((k for k in range(start, len(lines)) if mentions(name, lines[k])), None)
        if found is None:
            result.append(None)
        else:
            result.append(lines[found])
            start = found + 1
    return result


def to_number(text: str) -> float | None:
    """ "1 1/2" -> 1.5; None for nonsense like "1/0"."""
    try:
        return float(sum(Fraction(part) for part in text.split()))
    except (ValueError, ZeroDivisionError):
        return None


def kitchen_unit(text: str) -> str | None:
    """The pint unit name for a kitchen unit ("cups" -> "cup", "lbs" -> "pound"), or None."""
    word = text.strip().rstrip(".")
    unit = MORE_UNIT_ALIASES.get(word.lower()) or UNIT_ALIASES.get(word)
    unit = unit or UNIT_ALIASES.get(word.lower(), word.lower())
    if unit not in KITCHEN_UNITS and unit.endswith("s"):
        unit = UNIT_ALIASES.get(unit[:-1], unit[:-1])
    return unit if unit in KITCHEN_UNITS else None


def parse_amount(line: str) -> Amount | None:
    """The amount at the start of a cleaned line, or None when it has no number.

    "1 1/2 cups milk" -> 1.5 cup; "3-4 lbs beef" -> 3 pound; "2 (8 ounce) bottles" ->
    16 ounce; "8 cloves garlic" -> 8 cloves (a count); "6 eggs" -> 6 (a count);
    "2 dozen eggs" -> 24 eggs; "1 can corn" -> 1 can (not comparable); "salt" -> None.
    """
    match = LEADING.match(line)
    value = to_number(match["value"]) if match else None
    if match is None or value is None or value <= 0:
        return None
    rest = match["rest"]

    package = PACKAGE.match(rest)
    if package:
        size, unit = to_number(package["value"]), kitchen_unit(package["unit"])
        if size and unit:
            return Amount(value * size, unit)
        rest = rest[package.end() :]  # "1 (6 inch) flour tortilla": count the tortillas

    words = [word.strip(",.;:") for word in rest.lower().split()]
    if words and words[0] == "dozen":  # "2 dozen eggs" -> 24
        value, words = value * 12, words[1:]
    two_words = " ".join(words[:2])
    unit = kitchen_unit(two_words) if two_words in MORE_UNIT_ALIASES else None
    first = words[0] if words else ""
    unit = unit or kitchen_unit(first)
    if unit:
        return Amount(value, unit)
    if first in COUNT_WORDS or first in CONTAINERS:
        return Amount(value, first)
    return Amount(value, None)


def is_comparable(amount: Amount) -> bool:
    """A count or a unit pint knows; containers ("1 can") are not."""
    return is_count(amount.unit) or to_quantity(amount) is not None


def parse_servings(raw: float | None) -> int | None:
    """Servings, or None when missing or the default of 1 (see the module docstring)."""
    if raw is None or pd.isna(raw):
        return None
    servings = int(raw)
    return servings if servings > 1 else None


def parse_lines(raw: object) -> list[str] | None:
    """`ingredients_raw_str` is a JSON array of strings; None if it is not."""
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Add the amount columns to a database built before they were in schema.sql."""
    for table, column, kind in [
        ("recipes", "servings", "INTEGER"),
        ("recipe_ingredients", "amount_text", "TEXT"),
    ]:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")


def recipe_names(conn: sqlite3.Connection) -> dict[int, list[tuple[int, str]]]:
    """(ingredient_id, name) per recipe, in recipe order."""
    rows = conn.execute(
        "SELECT ri.recipe_id, ri.ingredient_id, i.name FROM recipe_ingredients ri "
        "JOIN ingredients i ON i.id = ri.ingredient_id ORDER BY ri.recipe_id, ri.position"
    )
    return {
        recipe_id: [(row["ingredient_id"], row["name"]) for row in group]
        for recipe_id, group in groupby(rows, key=lambda row: row["recipe_id"])
    }


def load_amounts(conn: sqlite3.Connection, amounts: pd.DataFrame) -> AmountsSummary:
    """Fill recipes.servings and, per recipe ingredient, amount_text plus the parsed
    quantity and unit. Safe to run again."""
    ensure_columns(conn)
    conn.execute("UPDATE recipes SET servings = NULL")
    conn.execute(
        "UPDATE recipe_ingredients SET amount_text = NULL, quantity = NULL, unit = NULL, "
        "quantity_source = NULL WHERE amount_text IS NOT NULL OR quantity_source = ?",
        (QUANTITY_SOURCE,),
    )

    names = recipe_names(conn)
    summary = AmountsSummary(
        recipes_in_db=len(names), rows_in_db=sum(len(items) for items in names.values())
    )
    servings_rows = []
    amount_rows = []
    for record in amounts.to_dict("records"):
        recipe_id = int(record["id"])
        items = names.get(recipe_id)
        lines = parse_lines(record["ingredients_raw_str"]) if items else None
        if items is None or lines is None:
            continue
        aligned = align_lines([name for _, name in items], lines)
        found = [(iid, text) for (iid, _), text in zip(items, aligned, strict=True) if text]
        if found:
            summary.recipes_with_amounts += 1
            summary.rows_with_amounts += len(found)
        for ingredient_id, text in found:
            amount = parse_amount(text)
            if amount is not None:
                summary.rows_with_quantity += 1
                summary.rows_comparable += is_comparable(amount)
            amount_rows.append(
                (
                    text,
                    amount.value if amount else None,
                    amount.unit if amount else None,
                    QUANTITY_SOURCE if amount else None,
                    recipe_id,
                    ingredient_id,
                )
            )
        servings = parse_servings(record["servings"])
        if servings is not None:
            servings_rows.append((servings, recipe_id))

    conn.executemany("UPDATE recipes SET servings = ? WHERE id = ?", servings_rows)
    conn.executemany(
        "UPDATE recipe_ingredients SET amount_text = ?, quantity = ?, unit = ?, "
        "quantity_source = ? WHERE recipe_id = ? AND ingredient_id = ?",
        amount_rows,
    )
    log.info(
        "amounts.done",
        recipes=summary.recipes_in_db,
        recipes_with_amounts=summary.recipes_with_amounts,
        rows_with_amounts=summary.rows_with_amounts,
        rows_with_quantity=summary.rows_with_quantity,
        rows_comparable=summary.rows_comparable,
    )
    return summary


def read_amounts_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, usecols=AMOUNT_COLUMNS)
