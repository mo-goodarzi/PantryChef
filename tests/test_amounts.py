"""Ingredient amounts: line cleaning, name-to-line alignment and loading into pantry.db."""

import json

import pandas as pd
import pytest

from pantry_chef.db.amounts import (
    align_lines,
    clean_line,
    ensure_columns,
    is_comparable,
    load_amounts,
    mentions,
    parse_amount,
    parse_lines,
    parse_servings,
)
from pantry_chef.db.repository import load_recipe_ingredients, load_servings
from pantry_chef.ingredients.quantities import Amount

PANCAKES = 5170
PANCAKE_LINES = [
    "2   cups    flour",
    "3   tablespoons    sugar",
    "1/2  teaspoon    salt",
    "1   tablespoon    baking powder",
    "2       eggs, beat them separately before adding to mixture ",
    "1/4  cup    butter, melted  (1/8 of a pound)",
    "1 3/4  cups    milk",
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4   cups    blueberries, fresh or frozen ", "4 cups blueberries, fresh or frozen"),
        ("1 -2   clove    garlic, minced ", "1-2 clove garlic, minced"),
        ("1/2-1   cup    flour", "1/2-1 cup flour"),
        ("1 (14   ounce) can   diced tomatoes", "1 (14 ounce) can diced tomatoes"),
        ("  salt", "salt"),
        ("", None),
        ("   ", None),
    ],
)
def test_clean_line(raw, expected):
    assert clean_line(raw) == expected


def test_mentions_matches_plurals_and_any_word():
    assert mentions("tomatoes", "8 large tomato, chopped")
    assert mentions("serrano chilies", "2 chili peppers")
    assert mentions("salt & freshly ground black pepper", "salt & pepper")
    assert not mentions("almonds", "1/3 cup cashews")


def test_lines_align_in_order():
    """Biryani (id 39), including "or" alternatives that start with the name."""
    names = ["cilantro", "basmati rice", "eggs"]
    lines = [
        "1/2  cup    cilantro or 1/2  cup    mint leaf",
        "2   cups    basmati rice or 2   cups    long-grain rice, uncooked ",
        "6       eggs, hard-boiled, halved ",
    ]
    assert align_lines(names, lines) == [
        "1/2 cup cilantro or 1/2 cup mint leaf",
        "2 cups basmati rice or 2 cups long-grain rice, uncooked",
        "6 eggs, hard-boiled, halved",
    ]


def test_blank_section_headers_are_skipped():
    names = ["black pepper", "chicken breast fillets"]
    lines = ["", "1   teaspoon    black pepper", "", "2 -4   pieces    chicken breast fillets"]
    assert align_lines(names, lines) == [
        "1 teaspoon black pepper",
        "2-4 pieces chicken breast fillets",
    ]


def test_repeated_line_goes_to_the_first_mention():
    """The name list drops the second "salt"; the amount comes from the first line."""
    names = ["rice", "salt", "cumin"]
    lines = ["3 -5   cups    rice", "2   tablespoons    salt", "  salt", "  cumin"]
    assert align_lines(names, lines) == ["3-5 cups rice", "2 tablespoons salt", "cumin"]


def test_a_line_that_does_not_mention_the_name_is_never_attached():
    """Same number of lines as names, but shifted: position alone would give almonds the
    sugar line."""
    names = ["flour", "almonds", "sugar"]
    lines = ["2 cups flour", "1 cup sugar", "1 cup water"]
    assert align_lines(names, lines) == ["2 cups flour", None, "1 cup sugar"]


def test_parse_lines_and_servings():
    assert parse_lines('["2 cups flour", "1 egg"]') == ["2 cups flour", "1 egg"]
    assert parse_lines("not json") is None
    assert parse_lines('{"a": 1}') is None
    assert parse_servings(4) == 4
    assert parse_servings(1) is None  # the site's default, not a real "serves 1"
    assert parse_servings(float("nan")) is None


def amounts_frame(rows):
    return pd.DataFrame(
        [{"id": i, "ingredients_raw_str": json.dumps(lines), "servings": s} for i, lines, s in rows]
    )


def test_load_amounts_fills_lines_and_servings(enriched_conn):
    frame = amounts_frame(
        [
            (PANCAKES, PANCAKE_LINES, 4),
            (999_999_999, ["1 cup ghost"], 2),  # not in our database: ignored
        ]
    )
    summary = load_amounts(enriched_conn, frame)

    assert summary.recipes_with_amounts == 1 and summary.rows_with_amounts == 7
    ingredients = load_recipe_ingredients(enriched_conn, [PANCAKES])[PANCAKES]
    lines = {i.name: i.amount_text for i in ingredients}
    assert lines["flour"] == "2 cups flour"
    assert lines["milk"] == "1 3/4 cups milk"
    assert load_servings(enriched_conn, PANCAKES) == 4


def test_load_amounts_is_safe_to_run_again(enriched_conn):
    load_amounts(enriched_conn, amounts_frame([(PANCAKES, PANCAKE_LINES, 4)]))
    summary = load_amounts(enriched_conn, amounts_frame([]))

    assert summary.rows_with_amounts == 0
    ingredients = load_recipe_ingredients(enriched_conn, [PANCAKES])[PANCAKES]
    assert all(i.amount_text is None and i.quantity is None for i in ingredients)
    assert load_servings(enriched_conn, PANCAKES) is None


def test_ensure_columns_upgrades_an_old_database(enriched_conn):
    enriched_conn.execute("ALTER TABLE recipe_ingredients DROP COLUMN amount_text")
    enriched_conn.execute("ALTER TABLE recipes DROP COLUMN servings")
    ensure_columns(enriched_conn)
    ensure_columns(enriched_conn)  # no error the second time

    load_amounts(enriched_conn, amounts_frame([(PANCAKES, PANCAKE_LINES, 4)]))
    assert load_servings(enriched_conn, PANCAKES) == 4


def test_an_alternative_that_does_not_name_the_ingredient_is_left_out():
    """RAW lists "oil" but the line offers butter or applesauce: show the name alone."""
    names = ["oil", "sugar"]
    lines = ["1/3  cup   melted butter or 1/3  cup    applesauce", "1 cup sugar"]
    assert align_lines(names, lines) == [None, "1 cup sugar"]


@pytest.mark.parametrize(
    ("line", "value", "unit"),
    [
        ("2 cups flour", 2, "cup"),
        ("1 1/2 cups milk", 1.5, "cup"),
        ("1/4 cup granulated sugar", 0.25, "cup"),
        ("1.5 kg potatoes", 1.5, "kilogram"),
        ("3 lbs boneless chicken, cut into 1 inch pieces", 3, "pound"),
        ("1 tablespoon saffron", 1, "tablespoon"),
        ("4 teaspoons milk, warm", 4, "teaspoon"),
        ("1 c. flour", 1, "cup"),
        ("1 stick butter", 1, "stick_of_butter"),
        ("2 pints strawberries", 2, "pint"),
        ("8 fl oz cream", 8, "fluid_ounce"),
        # ranges take the lower number
        ("3-4 lbs chuck roast (2-1/2 to 3 inches thick)", 3, "pound"),
        ("1/2-1 cup flour", 0.5, "cup"),
        ("1 1/2-2 cups rice", 1.5, "cup"),
        # packages multiply out
        ("1 (14 ounce) can diced tomatoes, briefly drained", 14, "ounce"),
        ("2 (8 ounce) bottles clam juice", 16, "ounce"),
        ("1 (2-3 lb) roasting chickens", 2, "pound"),
        ("1 (6 inch) flour tortilla", 1, None),  # a size, not an amount: count the tortillas
        # counts
        ("6 eggs, hard-boiled, halved", 6, "eggs"),  # still a count
        ("2 hot green chili peppers", 2, None),
        ("8 cloves garlic, peeled", 8, "cloves"),
        ("1 egg, beaten", 1, "egg"),
        ("2 dozen eggs", 24, "eggs"),
        ("1 dozen large hard-boiled egg", 12, "large"),
        ("2 large onions, chopped", 2, "large"),
        # containers without a size: kept, never compared
        ("1 can corn", 1, "can"),
        ("2 packets brown gravy mix", 2, "packets"),
        ("1 inch fresh ginger", 1, "inch"),
    ],
)
def test_parse_amount(line, value, unit):
    assert parse_amount(line) == Amount(value, unit)


@pytest.mark.parametrize(
    "line", ["salt", "salt and pepper, to taste", "one 14 1/2 ounce can chopped tomato", "0 cups"]
)
def test_parse_amount_without_a_number(line):
    assert parse_amount(line) is None


def test_comparable_means_count_or_kitchen_unit():
    assert is_comparable(Amount(6, None)) and is_comparable(Amount(8, "cloves"))
    assert is_comparable(Amount(2, "cup"))
    assert not is_comparable(Amount(1, "can"))


def test_load_amounts_fills_parsed_quantities(enriched_conn):
    load_amounts(enriched_conn, amounts_frame([(PANCAKES, PANCAKE_LINES + ["  salt"], 4)]))
    ingredients = load_recipe_ingredients(enriched_conn, [PANCAKES])[PANCAKES]
    by_name = {i.name: i for i in ingredients}

    assert (by_name["milk"].quantity, by_name["milk"].unit) == (1.75, "cup")
    assert (by_name["eggs"].quantity, by_name["eggs"].unit) == (2, "eggs")
    source = enriched_conn.execute(
        "SELECT quantity_source FROM recipe_ingredients WHERE recipe_id = ? AND quantity = 1.75",
        (PANCAKES,),
    ).fetchone()
    assert source["quantity_source"] == "recipe_line"


def test_verifier_compares_parsed_recipe_amounts_with_the_pantry(enriched_conn):
    """End to end on the fixture: lines -> numbers -> the verifier's quantity check."""
    from pantry_chef.agents.verifier import Verifier
    from pantry_chef.ingredients.normalize import normalize
    from pantry_chef.models.query import AmountStatus, PantryItem, RecipeQuery
    from pantry_chef.models.verification import FailureCode
    from pantry_chef.search.engine import SearchOptions, find_verified

    load_amounts(enriched_conn, amounts_frame([(PANCAKES, PANCAKE_LINES, 4)]))
    enriched_conn.execute(
        "UPDATE ingredients SET quantity_matters = 1 WHERE name IN ('eggs', 'milk')"
    )
    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    [pancakes] = [
        vc.candidate
        for vc in find_verified(enriched_conn, query, SearchOptions()).checked
        if vc.candidate.recipe_id == PANCAKES
    ]

    def have(eggs, milk_ml):
        known = AmountStatus.KNOWN
        items = [
            PantryItem(
                name="eggs", canonical_name=normalize("eggs"), quantity=eggs, amount_status=known
            ),
            PantryItem(
                name="milk",
                canonical_name=normalize("milk"),
                quantity=milk_ml,
                unit="ml",
                amount_status=known,
            ),
        ]
        [result] = Verifier(enriched_conn).verify_all([pancakes], query, items)
        return result

    # Recipe: 2 eggs, 1 3/4 cups (~414 ml) milk.
    enough = have(eggs=3, milk_ml=500)
    assert not [r for r in enough.reasons if r.code is FailureCode.INSUFFICIENT_QUANTITY]

    half = have(eggs=1, milk_ml=500)
    assert "make 50% of the recipe (limited by eggs)" in half.adaptations

    too_little = have(eggs=3, milk_ml=100)
    assert [r.item for r in too_little.reasons if r.code is FailureCode.INSUFFICIENT_QUANTITY] == [
        normalize("milk")
    ]


def test_the_search_filters_recipes_by_the_amounts_the_user_gave(enriched_conn):
    """The search's own verifier sees the amounts: too little milk removes the pancakes."""
    from pantry_chef.ingredients.normalize import normalize
    from pantry_chef.models.query import AmountStatus, PantryItem, RecipeQuery
    from pantry_chef.search.engine import find_verified

    load_amounts(enriched_conn, amounts_frame([(PANCAKES, PANCAKE_LINES, 4)]))
    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    milk = PantryItem(
        name="milk",
        canonical_name=normalize("milk"),
        quantity=100,
        unit="ml",
        amount_status=AmountStatus.KNOWN,
    )

    def approved(pantry_items):
        result = find_verified(enriched_conn, query, pantry_items=pantry_items)
        return {vc.candidate.recipe_id for vc in result.approved}

    assert PANCAKES in approved(None)
    assert PANCAKES not in approved([milk])  # 100 ml of 1 3/4 cups
