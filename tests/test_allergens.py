import pytest

from pantry_chef.ingredients.allergens import Allergen, detect_allergens

G, E, M = Allergen.GLUTEN, Allergen.EGGS, Allergen.MILK

# The 50 most common allergen-bearing ingredients in Food.com, labeled by hand.
# Acceptance criterion (Phase 2): rules must not miss any of these allergens.
MOST_COMMON_ALLERGEN_INGREDIENTS = {
    "butter": {M},
    "eggs": {E},
    "flour": {G},
    "milk": {M},
    "all-purpose flour": {G},
    "egg": {E},
    "parmesan cheese": {M},
    "sour cream": {M},
    "cream cheese": {M},
    "celery": {Allergen.CELERY},
    "cheddar cheese": {M},
    "unsalted butter": {M},
    "soy sauce": {Allergen.SOY, G},
    "mayonnaise": {E},
    "worcestershire sauce": {Allergen.FISH},
    "walnuts": {Allergen.TREE_NUTS},
    "pecans": {Allergen.TREE_NUTS},
    "dijon mustard": {Allergen.MUSTARD},
    "heavy cream": {M},
    "mozzarella cheese": {M},
    "balsamic vinegar": {Allergen.SULPHITES},
    "buttermilk": {M},
    "egg whites": {E},
    "red wine vinegar": {Allergen.SULPHITES},
    "whipping cream": {M},
    "dry white wine": {Allergen.SULPHITES},
    "whole wheat flour": {G},
    "sesame oil": {Allergen.SESAME},
    "white wine": {Allergen.SULPHITES},
    "dry mustard": {Allergen.MUSTARD},
    "breadcrumbs": {G},
    "cheese": {M},
    "egg yolks": {E},
    "feta cheese": {M},
    "nuts": {Allergen.TREE_NUTS},
    "monterey jack cheese": {M},
    "peanut butter": {Allergen.PEANUTS},
    "half-and-half": {M},
    "cream of mushroom soup": {M, G},
    "sesame seeds": {Allergen.SESAME},
    "evaporated milk": {M},
    "almond extract": {Allergen.TREE_NUTS},
    "swiss cheese": {M},
    "bread": {G},
    "shrimp": {Allergen.CRUSTACEANS},
    "plain yogurt": {M},
    "sweetened condensed milk": {M},
    "flour tortillas": {G},
    "pine nuts": {Allergen.TREE_NUTS},
    "fish sauce": {Allergen.FISH},
}


def test_gold_list_has_fifty_ingredients():
    assert len(MOST_COMMON_ALLERGEN_INGREDIENTS) == 50


@pytest.mark.parametrize(("name", "expected"), MOST_COMMON_ALLERGEN_INGREDIENTS.items())
def test_no_missed_allergens_in_most_common_ingredients(name, expected):
    assert expected <= detect_allergens(name)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # look like an allergen but are not
        ("eggplant", set()),
        ("coconut milk", set()),
        ("cream of tartar", set()),
        ("nutmeg", set()),
        ("butternut squash", set()),
        ("water chestnuts", set()),
        ("oyster mushrooms", set()),
        ("crab apple", set()),
        ("butter beans", set()),
        ("cocoa butter", set()),
        ("doughnut", {G}),  # "nut" inside a word is not a nut
        ("spaghetti sauce", set()),
        ("tortilla chips", set()),
        # the exception only removes that allergen
        ("peanut butter", {Allergen.PEANUTS}),
        ("almond milk", {Allergen.TREE_NUTS}),
        # free-from labels
        ("gluten-free flour", set()),
        ("gluten free all-purpose flour", set()),
        ("dairy-free margarine", set()),
        # "non-dairy" creamers and toppings usually contain casein
        ("non-dairy powdered coffee creamer", {M}),
        ("non-dairy whipped topping", {M}),
        ("french vanilla coffee creamer", {M}),
        ("vegan mayonnaise", set()),
        ("egg replacer", set()),
        # qualifiers kept on the original name
        ("corn tortillas", set()),
        ("rice noodles", set()),
        ("rice flour", set()),
        # compound ingredients with several allergens
        ("egg noodles", {E, G}),
        ("oyster sauce", {Allergen.MOLLUSCS}),
        ("imitation crab", {Allergen.FISH, Allergen.CRUSTACEANS}),
        ("lump crabmeat", {Allergen.CRUSTACEANS}),  # compound word, found by the LLM first
        ("lactose-free milk", {M}),
        # punctuation and case
        ("Parmesan Cheese, grated", {M}),
        ("salt & freshly ground black pepper", set()),
    ],
)
def test_tricky_cases(name, expected):
    assert detect_allergens(name) == expected


def test_there_are_fourteen_eu_allergens():
    assert len(Allergen) == 14


# --- allergies outside the EU list, matched as whole words ---------------------------


@pytest.mark.parametrize(
    ("text", "word", "expected"),
    [
        ("kiwi", "kiwi", True),
        ("kiwi fruit", "kiwi", True),
        ("kiwis", "kiwi", True),
        ("strawberry kiwi gelatin powder", "kiwi", True),
        ("Kiwi-Lime Sorbet", "kiwi", True),
        ("kiwis", "kiwis", True),  # the user may write the plural
        ("kiwifruit", "kiwi", False),  # one word: not a whole-word match
        ("lychees in syrup", "lychee", True),
        ("pine nuts", "pine nut", True),
        ("peanut butter", "pine nut", False),
    ],
)
def test_mentions_word(text, word, expected):
    from pantry_chef.ingredients.allergens import mentions_word

    assert mentions_word(text, word) is expected
