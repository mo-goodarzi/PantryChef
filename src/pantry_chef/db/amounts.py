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

Servings of 1 are the site's default when the author left it empty (17% of recipes, median
784 g per serving against ~250 g otherwise), so they are stored as unknown.
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import pandas as pd

from pantry_chef.db.parsing import clean_text
from pantry_chef.observability import get_logger

log = get_logger("db.amounts")

AMOUNT_COLUMNS = ["id", "ingredients_raw_str", "servings"]


@dataclass
class AmountsSummary:
    recipes_in_db: int = 0
    recipes_with_amounts: int = 0
    rows_in_db: int = 0
    rows_with_amounts: int = 0


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
    """Fill recipes.servings and recipe_ingredients.amount_text. Safe to run again."""
    ensure_columns(conn)
    conn.execute("UPDATE recipes SET servings = NULL")
    conn.execute("UPDATE recipe_ingredients SET amount_text = NULL")

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
        found = [
            (text, recipe_id, iid) for (iid, _), text in zip(items, aligned, strict=True) if text
        ]
        if found:
            summary.recipes_with_amounts += 1
            summary.rows_with_amounts += len(found)
            amount_rows += found
        servings = parse_servings(record["servings"])
        if servings is not None:
            servings_rows.append((servings, recipe_id))

    conn.executemany("UPDATE recipes SET servings = ? WHERE id = ?", servings_rows)
    conn.executemany(
        "UPDATE recipe_ingredients SET amount_text = ? WHERE recipe_id = ? AND ingredient_id = ?",
        amount_rows,
    )
    log.info(
        "amounts.done",
        recipes=summary.recipes_in_db,
        recipes_with_amounts=summary.recipes_with_amounts,
        rows_with_amounts=summary.rows_with_amounts,
    )
    return summary


def read_amounts_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, usecols=AMOUNT_COLUMNS)
