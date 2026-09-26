"""Rule-based meat detection, the code layer under the LLM's contains_meat label.

Like allergens, rules and LLM are combined with OR: either one saying "meat" is enough.
Fish and seafood are detected through the allergen rules (fish, crustaceans, molluscs).
"""

from pantry_chef.ingredients.allergens import AllergenRule, prepare, rule_matches

MEAT_RULE = AllergenRule(
    keywords=(
        "chicken",
        "beef",
        "pork",
        "bacon",
        "ham",
        "sausage",
        "turkey",
        "lamb",
        "veal",
        "venison",
        "duck",
        "goose",
        "prosciutto",
        "pancetta",
        "salami",
        "pepperoni",
        "chorizo",
        "hot dog",
        "gelatin",
        "gelatine",
        "lard",
        "hamburger",
        "meatball",
        "steak",
        "ribs",
        "brisket",
        "bratwurst",
        "kielbasa",
        "mutton",
        "rabbit",
        "quail",
        "pheasant",
        "bison",
        "elk",
        "liver",
        "suet",
        "bone marrow",
        "jello",
        # usually made with gelatin
        "marshmallow",
        "gummy",
        "gummies",
    ),
    exceptions=(
        "hamburger bun",
        "hot dog bun",
        "chicken of the sea",
        "steak sauce",
        "steak seasoning",
        "beefsteak tomato",
        # made with egg whites, not gelatin
        "marshmallow creme",
        "marshmallow cream",
        "marshmallow fluff",
    ),
    free_from=("vegetarian", "vegan", "veggie", "meatless", "meat-free", "soy", "tofu"),
)


def detect_meat(name: str) -> bool:
    return rule_matches(prepare(name), MEAT_RULE)
