"""Shared fixtures: the 20-recipe fixture database, enriched with hand-written labels."""

from pathlib import Path

import pytest

from pantry_chef.db.connection import connect
from pantry_chef.db.loader import build_database
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.enrich import enrich_database
from pantry_chef.models.ingredient import Category, IngredientLabel

FIXTURES = Path(__file__).parent / "fixtures"

# Key categories for fixture ingredients; everything else is a non-key "spice".
FIXTURE_CATEGORIES = {
    Category.PROTEIN: [
        "eggs",
        "egg",
        "beef",
        "tuna",
        "crabmeat",
        "chicken breast",
        "boneless chicken breasts",
        "boneless skinless chicken breast halves",
        "walnuts",
    ],
    Category.DAIRY: [
        "butter",
        "milk",
        "vanilla yogurt",
        "evaporated skim milk",
        "fat free cream cheese",
        "pizza cheese",
        "mild cheddar cheese",
        "swiss cheese",
    ],
    Category.GRAIN: [
        "flour",
        "whole wheat flour",
        "fettuccine",
        "frozen bread dough",
        "flour tortillas",
        "english muffins",
        "ramen noodles",
        "bread",
    ],
    Category.PRODUCE: [
        "blueberries",
        "oranges",
        "seedless grapes",
        "apple",
        "onions",
        "mushrooms",
        "mushroom",
        "tomatoes",
        "celery",
        "green onions",
        "scallions",
        "onion",
        "lettuce",
        "chopped tomato",
        "black olives",
        "fresh lemongrass",
        "lemongrass",
        "lime",
        "jalapeno chile",
        "serrano chilies",
    ],
}
# Hidden allergens only an LLM would know.
HIDDEN_ALLERGENS = {
    "nam pla": [Allergen.FISH],
    "chicken stock": [Allergen.CELERY],
    "chicken broth": [Allergen.CELERY],
    "salad dressing": [Allergen.EGGS],
}
ANIMAL = {"honey", "vanilla yogurt"}
FISH = {"nam pla"}
# The verifier also sees allergens of contained ingredients (not in recipe_allergens).
RELATION_SEED = {
    "salad dressing": {"parents": [], "contains": ["swiss cheese"], "substitutes": []},
}


def fixture_label(name: str) -> IngredientLabel:
    category = next(
        (cat for cat, names in FIXTURE_CATEGORIES.items() if name in names), Category.SPICE
    )
    return IngredientLabel(
        name=name,
        category=category,
        allergens=HIDDEN_ALLERGENS.get(name, []),
        contains_meat=False,  # meat comes from the rules ("chicken", "beef")
        contains_fish=name in FISH,
        animal_product=name in ANIMAL,
    )


@pytest.fixture
def enriched_conn(tmp_path):
    db_path = tmp_path / "pantry.db"
    build_database(FIXTURES / "recipes_sample.csv", db_path)
    conn = connect(db_path)
    names = [row["name"] for row in conn.execute("SELECT name FROM ingredients")]
    enrich_database(conn, {n: fixture_label(n) for n in names}, relation_seed=RELATION_SEED)
    yield conn
    conn.close()


@pytest.fixture
def state_conn(tmp_path):
    from pantry_chef.db.state import open_state_db

    conn = open_state_db(tmp_path / "state.db")
    yield conn
    conn.close()
