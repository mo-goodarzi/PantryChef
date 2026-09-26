"""Rule-based allergen detection for the EU list of 14 allergens.

Rules run on the ORIGINAL ingredient name (not the canonical one), so qualifiers like
"gluten-free" or "corn" are never lost. Rules are the first safety layer; the LLM labeler
can only add allergens on top of them, never remove one.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class Allergen(StrEnum):
    GLUTEN = "gluten"
    CRUSTACEANS = "crustaceans"
    EGGS = "eggs"
    FISH = "fish"
    PEANUTS = "peanuts"
    SOY = "soy"
    MILK = "milk"
    TREE_NUTS = "tree_nuts"
    CELERY = "celery"
    MUSTARD = "mustard"
    SESAME = "sesame"
    SULPHITES = "sulphites"
    LUPIN = "lupin"
    MOLLUSCS = "molluscs"


@dataclass(frozen=True)
class AllergenRule:
    keywords: tuple[str, ...]
    # Phrases that contain a keyword but are not this allergen ("peanut butter" for milk).
    exceptions: tuple[str, ...] = ()
    # Labels that mean the product is made without this allergen ("gluten-free").
    free_from: tuple[str, ...] = field(default=())


RULES: dict[Allergen, AllergenRule] = {
    Allergen.MILK: AllergenRule(
        keywords=(
            "milk",
            "buttermilk",
            "cheese",
            "butter",
            "cream",
            "yogurt",
            "yoghurt",
            "whey",
            "casein",
            "ghee",
            "half-and-half",
            "parmesan",
            "parmigiano-reggiano",
            "mozzarella",
            "ricotta",
            "cheddar",
            "feta",
            "brie",
            "camembert",
            "gouda",
            "gruyere",
            "provolone",
            "mascarpone",
            "velveeta",
            "paneer",
            "kefir",
            "custard",
            "cool whip",
            "whipped topping",
            "creme fraiche",
            "monterey jack",
            "colby",
            "havarti",
            "romano",
            "asiago",
            "pecorino",
            "emmental",
            "fontina",
            "halloumi",
            "queso",
            "alfredo",
            "bechamel",
            "white chocolate",
        ),
        exceptions=(
            "peanut butter",
            "almond butter",
            "cashew butter",
            "nut butter",
            "apple butter",
            "cocoa butter",
            "shea butter",
            "sunflower butter",
            "butter bean",
            "butter lettuce",
            "coconut milk",
            "coconut cream",
            "cream of coconut",
            "almond milk",
            "soy milk",
            "rice milk",
            "oat milk",
            "cashew milk",
            "hemp milk",
            "cream of tartar",
            "cream of wheat",
            "coconut yogurt",
            "cream soda",
        ),
        free_from=("dairy-free", "dairy free", "non-dairy", "nondairy", "milk-free", "vegan"),
    ),
    Allergen.EGGS: AllergenRule(
        keywords=(
            "egg",
            "yolk",
            "mayonnaise",
            "mayo",
            "meringue",
            "eggnog",
            "egg beaters",
            "aioli",
            "hollandaise",
            "custard",
            "miracle whip",
            "tartar sauce",
            "caesar dressing",
        ),
        exceptions=("egg replacer",),
        free_from=("egg-free", "eggless", "vegan"),
    ),
    Allergen.GLUTEN: AllergenRule(
        keywords=(
            "wheat",
            "flour",
            "bread",
            "breadcrumb",
            "crumb",
            "panko",
            "pasta",
            "spaghetti",
            "macaroni",
            "noodle",
            "lasagna",
            "lasagne",
            "fettuccine",
            "linguine",
            "penne",
            "rigatoni",
            "ravioli",
            "tortellini",
            "orzo",
            "couscous",
            "bulgur",
            "semolina",
            "farina",
            "barley",
            "rye",
            "spelt",
            "kamut",
            "seitan",
            "oat",
            "oatmeal",
            "cracker",
            "graham",
            "biscuit",
            "pie crust",
            "pie shell",
            "pastry",
            "phyllo",
            "filo",
            "tortilla",
            "pita",
            "bagel",
            "bun",
            "roll",
            "wonton",
            "croissant",
            "crouton",
            "cake mix",
            "brownie mix",
            "bisquick",
            "baking mix",
            "beer",
            "malt",
            "soy sauce",
            "teriyaki",
            "cream of mushroom soup",
            "cream of chicken soup",
            "cream of celery soup",
            "stuffing",
            "pretzel",
            "cookie",
            "cake",
            "muffin",
            "pancake mix",
            "matzo",
            "gnocchi",
            "dumpling",
            "baguette",
            "brioche",
            "shortbread",
            "sourdough",
            "doughnut",
            "donut",
        ),
        exceptions=(
            "rice flour",
            "almond flour",
            "almond meal",
            "coconut flour",
            "corn flour",
            "cornflour",
            "chickpea flour",
            "garbanzo flour",
            "potato flour",
            "tapioca flour",
            "buckwheat flour",
            "rice noodle",
            "rice pasta",
            "rice vermicelli",
            "corn tortilla",
            "cream of tartar",
            "rice paper",
            "spaghetti sauce",
            "pasta sauce",
            "tortilla chip",
        ),
        free_from=("gluten-free", "gluten free", "wheat-free"),
    ),
    Allergen.PEANUTS: AllergenRule(
        keywords=("peanut", "groundnut"),
        free_from=("peanut-free",),
    ),
    Allergen.TREE_NUTS: AllergenRule(
        keywords=(
            "almond",
            "walnut",
            "pecan",
            "cashew",
            "pistachio",
            "hazelnut",
            "filbert",
            "macadamia",
            "brazil nut",
            "pine nut",
            "pignoli",
            "chestnut",
            "praline",
            "marzipan",
            "nutella",
            "nut",
            "amaretto",
            "frangelico",
            "gianduja",
        ),
        exceptions=("water chestnut",),
        free_from=("nut-free",),
    ),
    Allergen.SOY: AllergenRule(
        keywords=(
            "soy",
            "soya",
            "soybean",
            "tofu",
            "tempeh",
            "edamame",
            "miso",
            "tamari",
            "teriyaki",
            "shoyu",
            "textured vegetable protein",
            "natto",
            "hoisin",
        ),
        free_from=("soy-free",),
    ),
    Allergen.FISH: AllergenRule(
        keywords=(
            "fish",
            "salmon",
            "tuna",
            "cod",
            "tilapia",
            "halibut",
            "anchovy",
            "anchovies",
            "sardine",
            "trout",
            "mackerel",
            "haddock",
            "snapper",
            "catfish",
            "sole",
            "flounder",
            "swordfish",
            "mahi",
            "mahi-mahi",
            "perch",
            "pollock",
            "herring",
            "bass",
            "grouper",
            "pike",
            "carp",
            "orange roughy",
            "whitefish",
            "caviar",
            "roe",
            "worcestershire",
            "bonito",
            "dashi",
            "surimi",
            "imitation crab",
            "lox",
        ),
    ),
    Allergen.CRUSTACEANS: AllergenRule(
        keywords=(
            "shrimp",
            "prawn",
            "crab",
            "lobster",
            "crawfish",
            "crayfish",
            "langoustine",
            "krill",
            "crabmeat",
            "shrimpmeat",
        ),
        exceptions=("crab apple", "crabapple", "crab boil"),
    ),
    Allergen.MOLLUSCS: AllergenRule(
        keywords=(
            "clam",
            "mussel",
            "oyster",
            "scallop",
            "squid",
            "calamari",
            "octopus",
            "snail",
            "escargot",
            "abalone",
            "cockle",
            "whelk",
            "conch",
            "cuttlefish",
        ),
        exceptions=("oyster mushroom", "oyster cracker"),
    ),
    Allergen.CELERY: AllergenRule(keywords=("celery", "celeriac")),
    Allergen.MUSTARD: AllergenRule(keywords=("mustard", "dijon")),
    Allergen.SESAME: AllergenRule(
        keywords=("sesame", "tahini", "benne", "halva", "za atar", "zaatar"),
    ),
    Allergen.SULPHITES: AllergenRule(
        keywords=(
            "wine",
            "sherry",
            "vermouth",
            "champagne",
            "marsala",
            "madeira",
            "sake",
            "balsamic",
            "dried apricot",
            "maraschino",
            "sulfite",
            "sulphite",
        ),
    ),
    Allergen.LUPIN: AllergenRule(keywords=("lupin", "lupine", "lupini")),
}

PUNCTUATION = ",.;:()[]{}!?&/'\"*"


def prepare(name: str) -> str:
    """Lowercase, turn punctuation into spaces and pad with spaces for phrase matching."""
    text = name.lower()
    for ch in PUNCTUATION:
        text = text.replace(ch, " ")
    return f" {' '.join(text.split())} "


def plural_forms(phrase: str) -> tuple[str, ...]:
    """The phrase with its last word as written, +s and +es ("egg", "eggs")."""
    return (phrase, f"{phrase}s", f"{phrase}es")


def contains_phrase(text: str, phrase: str) -> bool:
    return any(f" {form} " in text for form in plural_forms(phrase))


def remove_phrases(text: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        for form in plural_forms(phrase):
            text = text.replace(f" {form} ", " ")
    return text


def rule_matches(text: str, rule: AllergenRule) -> bool:
    if any(contains_phrase(text, marker) for marker in rule.free_from):
        return False
    text = remove_phrases(text, rule.exceptions)
    return any(contains_phrase(text, keyword) for keyword in rule.keywords)


def detect_allergens(name: str) -> set[Allergen]:
    """Allergens found by keyword rules in one ingredient name."""
    text = prepare(name)
    return {allergen for allergen, rule in RULES.items() if rule_matches(text, rule)}
