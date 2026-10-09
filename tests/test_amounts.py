"""Ingredient amounts: line cleaning, name-to-line alignment and loading into pantry.db."""

import json

import pandas as pd
import pytest

from pantry_chef.db.amounts import (
    align_lines,
    clean_line,
    ensure_columns,
    load_amounts,
    mentions,
    parse_lines,
    parse_servings,
)
from pantry_chef.db.repository import load_recipe_ingredients, load_servings

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
    assert all(i.amount_text is None for i in ingredients)
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
