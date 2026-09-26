"""Deterministic ingredient-name normalization ("2 large Eggs" -> "egg").

Only removes words that never change what the ingredient is (size, preparation) and
turns the last word singular. Words that change the ingredient ("gluten-free", "corn",
"ground", "dried", "frozen") are always kept. Allergens are labeled on the original
name, never on the canonical one.
"""

# Words that describe size or preparation, not the ingredient itself.
DESCRIPTOR_WORDS = frozenset(
    {
        "fresh",
        "freshly",
        "large",
        "small",
        "medium",
        "extra-large",
        "jumbo",
        "chopped",
        "finely",
        "coarsely",
        "roughly",
        "thinly",
        "diced",
        "minced",
        "sliced",
        "grated",
        "shredded",
        "cubed",
        "halved",
        "quartered",
        "peeled",
        "pitted",
        "seeded",
        "trimmed",
        "rinsed",
        "drained",
        "boneless",
        "skinless",
        "ripe",
        "softened",
        "melted",
        "chilled",
        "cold",
        "warm",
        "room-temperature",
        "organic",
        "packed",
        "unsalted",
        "salted",
    }
)

# Multi-word descriptors, removed before single words.
DESCRIPTOR_PHRASES = ("extra virgin", "extra-virgin", "at room temperature")

# Last words that end in "s" but are already singular (or have no singular).
INVARIANT_WORDS = frozenset(
    {
        "molasses",
        "couscous",
        "asparagus",
        "hummus",
        "citrus",
        "swiss",
        "grits",
        "preserves",
        "bitters",
        "schnapps",
        "brussels",
        "series",
        "hibiscus",
    }
)

IRREGULAR_PLURALS = {
    "leaves": "leaf",
    "halves": "half",
    "loaves": "loaf",
    "knives": "knife",
    "chilies": "chili",
    "cookies": "cookie",
    "brownies": "brownie",
    "pies": "pie",
    "smoothies": "smoothie",
    "calories": "calorie",
}


def singularize(word: str) -> str:
    """Singular form of one word, using simple English rules plus exceptions."""
    if word in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[word]
    if len(word) <= 3 or word in INVARIANT_WORDS or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"  # cherries -> cherry
    if word.endswith("oes"):
        return word[:-2]  # tomatoes -> tomato
    if word.endswith(("ches", "shes", "xes", "zes")):
        return word[:-2]  # peaches -> peach, radishes -> radish
    if word.endswith("s"):
        return word[:-1]  # eggs -> egg, olives -> olive
    return word


# Food.com writes some ingredients as "lemon, juice of": keep the part after the comma.
PART_OF_SUFFIXES = ("juice of", "zest of", "rind of", "juice and zest of")


def reorder_part_of(text: str) -> str:
    """ "lemon, juice of" -> "lemon juice"; other names are returned unchanged."""
    if "," not in text:
        return text
    base, _, rest = text.partition(",")
    rest = rest.strip()
    if rest in PART_OF_SUFFIXES:
        return f"{base.strip()} {rest.removesuffix(' of')}"
    return text


def normalize(name: str) -> str:
    """Canonical ingredient name. Idempotent: normalize(normalize(x)) == normalize(x)."""
    text = reorder_part_of(" ".join(name.lower().split()))
    text = " ".join(text.replace(",", " ").split()).removeprefix("of ")
    for phrase in DESCRIPTOR_PHRASES:
        text = f" {text} ".replace(f" {phrase} ", " ").strip()

    words = [w for w in text.split() if w not in DESCRIPTOR_WORDS]
    if not words:  # the name was only descriptors, e.g. "fresh": keep it as it was
        return text
    words[-1] = singularize(words[-1])
    return " ".join(words)
