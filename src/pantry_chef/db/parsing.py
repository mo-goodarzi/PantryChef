"""Pure functions that clean one raw Food.com value at a time."""

import ast
import math

NUTRITION_FIELDS = [
    "calories",
    "total_fat_pdv",
    "sugar_pdv",
    "sodium_pdv",
    "protein_pdv",
    "sat_fat_pdv",
    "carbs_pdv",
]


def parse_list(raw: str) -> list:
    """Parse a column stored as a Python list literal, e.g. "['a', 'b']".

    ast.literal_eval only accepts literals, so it is safe on untrusted text (unlike eval).
    """
    value = ast.literal_eval(raw)
    if not isinstance(value, list):
        raise ValueError(f"expected a list, got {type(value).__name__}")
    return value


def split_nutrition(raw: str) -> dict[str, float]:
    """Turn "[51.5, 0.0, 13.0, ...]" into named nutrition fields."""
    values = parse_list(raw)
    if len(values) != len(NUTRITION_FIELDS):
        raise ValueError(f"expected {len(NUTRITION_FIELDS)} nutrition values, got {len(values)}")
    return {field: float(value) for field, value in zip(NUTRITION_FIELDS, values, strict=True)}


def clean_text(raw: object) -> str | None:
    """Collapse whitespace; return None for missing or empty values."""
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return None
    text = " ".join(str(raw).split())
    return text or None


def clean_name(raw: object) -> str | None:
    """Clean a recipe name. Names made only of punctuation count as empty."""
    text = clean_text(raw)
    if text is None or not any(ch.isalnum() for ch in text):
        return None
    return text


def clean_ingredients(raw_items: list[str]) -> list[str]:
    """Lowercase and trim ingredient names, drop empties, dedupe keeping first position."""
    seen: set[str] = set()
    result = []
    for item in raw_items:
        name = " ".join(str(item).lower().split())
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def clean_tags(raw_tags: list[str]) -> list[str]:
    """Trim tags, drop empties, dedupe."""
    return list(dict.fromkeys(tag.strip() for tag in raw_tags if tag.strip()))
