"""Scale a recipe's amounts to the servings the user wants (code only, no model).

The factor is wanted / recipe servings: a recipe for 2 made for 8 multiplies every amount
by 4. Each line keeps its own words; only the number at its start changes:
- "1 1/2 cups milk" x 2 -> "3 cups milk"; "3-4 lbs beef" x 2 -> "6-8 lbs beef";
- packages scale by their count, not their size: "2 (8 ounce) bottles" x 1/2 ->
  "1 (8 ounce) bottle";
- lines without a leading number ("salt, to taste") stay as they are.

Numbers are shown as kitchen fractions (eighths and thirds); from 100 on, as whole numbers
("1600 g", not "1066 2/3 g"). The smallest amount shown is 1/8, so a pinch never
disappears. Cooking times and pan sizes in the steps are not changed (the answer says so).
"""

import re
from fractions import Fraction

from pantry_chef.db.amounts import NUMBER, to_number
from pantry_chef.models.recipe import Candidate, RecipeIngredient

LINE = re.compile(rf"^(?P<low>{NUMBER})(?:-(?P<high>{NUMBER}))?(?P<rest>\s.*)?$")
FRACTIONS = sorted({Fraction(n, d) for d in (2, 3, 4, 8) for n in range(d + 1)})
SMALLEST = Fraction(1, 8)
WHOLE_FROM = 100
# Unit words whose number changes with the amount ("1 cup", "2 cups").
PLURALS = {
    "cup": "cups",
    "tablespoon": "tablespoons",
    "teaspoon": "teaspoons",
    "ounce": "ounces",
    "pound": "pounds",
    "gram": "grams",
    "clove": "cloves",
    "slice": "slices",
    "egg": "eggs",
    "can": "cans",
    "package": "packages",
    "bottle": "bottles",
    "jar": "jars",
    "stick": "sticks",
}
SINGULARS = {plural: singular for singular, plural in PLURALS.items()}


def scale_factor(recipe_servings: int | None, wanted: int | None) -> float | None:
    """wanted / recipe servings, or None when there is nothing to scale."""
    if not recipe_servings or not wanted or wanted == recipe_servings:
        return None
    return wanted / recipe_servings


def format_number(value: float) -> str:
    """0.5 -> "1/2", 1.5 -> "1 1/2", 0.33 -> "1/3", 4.0 -> "4", 1066.7 -> "1067"."""
    if value >= WHOLE_FROM:
        return str(round(value))
    whole = int(value)
    part = min(FRACTIONS, key=lambda f: abs(f - (value - whole)))
    if part == 1:
        whole, part = whole + 1, Fraction(0)
    if whole == 0 and part == 0:
        part = SMALLEST
    if part == 0:
        return str(whole)
    return f"{whole} {part}" if whole else str(part)


def match_number(rest: str, value: float) -> str:
    """ "cups milk" with 1 -> "cup milk"; "cup rice" with 4 -> "cups rice". The word after
    a package size counts too: " (8 ounce) bottles"."""
    match = re.match(r"^(\s(?:\([^)]*\)\s)?)([a-z]+)\b", rest)
    if match is None:
        return rest
    word = match[2]
    table = PLURALS if value > 1 else SINGULARS
    if word not in table:
        return rest
    return rest[: match.start(2)] + table[word] + rest[match.end(2) :]


def scale_line(line: str, factor: float) -> str:
    """The same line with its leading amount multiplied by the factor."""
    match = LINE.match(line)
    low = to_number(match["low"]) if match else None
    if match is None or low is None:
        return line
    shown = format_number(low * factor)
    high = to_number(match["high"]) if match["high"] else None
    if high is not None:
        shown += "-" + format_number(high * factor)
    value = (high or low) * factor
    return shown + match_number(match["rest"] or "", value)


def scale_ingredient(ingredient: RecipeIngredient, factor: float) -> RecipeIngredient:
    quantity = ingredient.quantity * factor if ingredient.quantity is not None else None
    text = scale_line(ingredient.amount_text, factor) if ingredient.amount_text else None
    return ingredient.model_copy(update={"quantity": quantity, "amount_text": text})


def scale_recipe(candidate: Candidate, factor: float) -> Candidate:
    """The recipe with every amount scaled (line text and parsed quantity)."""
    ingredients = [scale_ingredient(i, factor) for i in candidate.ingredients]
    return candidate.model_copy(update={"ingredients": ingredients})
