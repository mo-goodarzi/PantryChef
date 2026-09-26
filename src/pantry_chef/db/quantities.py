"""Ingredient counts from the irkaal Food.com dataset.

irkaal quantities have no units ("4" blueberries is really 4 cups), so a number is only
meaningful for ingredients that are counted: 2 eggs, 3 garlic cloves. We keep only those,
and only when the ingredient name confirms which quantity belongs to it.
See docs/decisions.md ("Quantity source").
"""

from fractions import Fraction

# Ingredients whose quantities in irkaal are almost always counts. Chosen from the value
# distribution per ingredient: "onion", "carrot", "tomatoes" and "potatoes" are excluded
# because many values are really cups, cans or pounds.
COUNTED_INGREDIENTS = frozenset(
    {
        "egg",
        "eggs",
        "large eggs",
        "egg yolk",
        "egg yolks",
        "egg white",
        "egg whites",
        "hard-boiled eggs",
        "garlic clove",
        "garlic cloves",
        "lemon",
        "lemons",
        "lime",
        "limes",
        "avocado",
        "avocados",
        "banana",
        "bananas",
        "bay leaf",
        "bay leaves",
        "green onions",
        "scallions",
        "celery ribs",
        "flour tortillas",
        "corn tortillas",
        "english muffins",
        "chicken thighs",
    }
)

MAX_COUNT = 24  # larger numbers are more likely grams or a data error than a count


def parse_quantity(raw: str | None) -> float | None:
    """Parse "2", "1 1⁄2", "0.5", "1⁄4" or a range like "1 -2" into a number.

    For a range the lower bound is used: it is the least the recipe needs.
    Returns None when the text is missing or not a number.
    """
    if raw is None:
        return None
    text = raw.replace("⁄", "/").strip()  # "1⁄2" uses the fraction slash U+2044
    lower_bound = text.split("-")[0].strip()
    if not lower_bound:
        return None
    try:
        # "1 1/2" -> 1 + 1/2
        return float(sum(Fraction(part) for part in lower_bound.split()))
    except (ValueError, ZeroDivisionError):
        return None


def counted_quantities(
    parts: list[str], quantities: list[str | None], recipe_ingredients: set[str]
) -> dict[str, float]:
    """Return {ingredient name: count} for counted ingredients of one recipe.

    Quantities are trusted only when the irkaal name list and quantity list have the same
    length (so position i in one is position i in the other) and the name also appears
    in our recipe's ingredient list.
    """
    if len(parts) != len(quantities):
        return {}

    result: dict[str, float] = {}
    for part, raw_quantity in zip(parts, quantities, strict=True):
        name = " ".join(part.lower().split())
        if name not in COUNTED_INGREDIENTS or name not in recipe_ingredients:
            continue
        count = parse_quantity(raw_quantity)
        if count is not None and 0 < count <= MAX_COUNT:
            result.setdefault(name, count)
    return result
