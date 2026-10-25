"""Scaling a recipe's amounts to other servings: factor, kitchen fractions, line rewriting."""

import pytest

from pantry_chef.ingredients.scaling import (
    format_number,
    scale_factor,
    scale_ingredient,
    scale_line,
    scale_recipe,
)
from pantry_chef.models.recipe import Candidate, RecipeIngredient


@pytest.mark.parametrize(
    ("recipe_servings", "wanted", "factor"),
    [
        (2, 1, 0.5),
        (2, 8, 4.0),
        (4, 6, 1.5),
        (4, 4, None),  # same servings: nothing to scale
        (None, 4, None),  # the recipe does not say
        (4, None, None),  # the user kept the recipe as written
    ],
)
def test_scale_factor(recipe_servings, wanted, factor):
    assert scale_factor(recipe_servings, wanted) == factor


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        (4.0, "4"),
        (0.5, "1/2"),
        (1.5, "1 1/2"),
        (1 / 3, "1/3"),
        (2 / 3, "2/3"),
        (0.875, "7/8"),
        (1.33, "1 1/3"),
        (2.98, "3"),  # rounds up to the next whole number
        (0.97, "1"),
        (0.01, "1/8"),  # never rounded away to nothing
        (13.4, "13 3/8"),
        (1600.0, "1600"),
        (1066.67, "1067"),  # whole numbers from 100 on
    ],
)
def test_format_number(value, shown):
    assert format_number(value) == shown


@pytest.mark.parametrize(
    ("line", "factor", "scaled"),
    [
        # A recipe for 2 made for 1, then for 8.
        ("4 teaspoons salt", 0.5, "2 teaspoons salt"),
        ("2 cups rice", 0.5, "1 cup rice"),
        ("400 g chicken", 0.5, "200 g chicken"),
        ("4 teaspoons salt", 4, "16 teaspoons salt"),
        ("2 cups rice", 4, "8 cups rice"),
        ("400 g chicken", 4, "1600 g chicken"),
        # Fractions and decimals.
        ("1 1/2 cups milk", 2, "3 cups milk"),
        ("1/2 cup milk", 2, "1 cup milk"),
        ("1 cup rice", 3, "3 cups rice"),
        ("1.5 cups flour", 0.5, "3/4 cup flour"),
        ("1 3/4 cups milk", 0.5, "7/8 cup milk"),
        ("1/4 teaspoon pepper", 1 / 3, "1/8 teaspoon pepper"),
        # Ranges scale both ends.
        ("3-4 lbs beef", 2, "6-8 lbs beef"),
        ("1/2-1 cup sugar", 2, "1-2 cups sugar"),
        # Packages scale by their count, never their size.
        ("2 (8 ounce) bottles salsa", 0.5, "1 (8 ounce) bottle salsa"),
        ("1 (14 ounce) can tomatoes", 2, "2 (14 ounce) cans tomatoes"),
        # Counts.
        ("3 eggs", 1 / 3, "1 egg"),
        ("2 eggs", 0.75, "1 1/2 eggs"),
        ("2 dozen eggs", 0.5, "1 dozen eggs"),
        # Nothing to scale: the line is kept as it is.
        ("salt, to taste", 4, "salt, to taste"),
        ("2% milk", 2, "2% milk"),  # a percentage, not an amount
        ("about 2 cups broth", 2, "about 2 cups broth"),
    ],
)
def test_scale_line(line, factor, scaled):
    assert scale_line(line, factor) == scaled


def ingredient(**fields) -> RecipeIngredient:
    return RecipeIngredient(
        name="rice", canonical_name="rice", category=None, is_key=True, **fields
    )


def test_scale_ingredient_scales_the_line_and_the_parsed_quantity():
    rice = ingredient(quantity=2.0, unit="cup", amount_text="2 cups rice")
    scaled = scale_ingredient(rice, 4)
    assert (scaled.quantity, scaled.unit, scaled.amount_text) == (8.0, "cup", "8 cups rice")
    assert rice.quantity == 2.0  # the original is not changed


def test_scale_ingredient_without_amounts_keeps_the_name():
    scaled = scale_ingredient(ingredient(), 4)
    assert scaled.quantity is None and scaled.amount_text is None


def test_scale_recipe_scales_every_ingredient():
    recipe = Candidate(
        recipe_id=1,
        name="rice",
        minutes=20,
        ingredients=[ingredient(quantity=2.0, unit="cup", amount_text="2 cups rice")],
        have_key=1,
        total_key=1,
        coverage=1.0,
        ingredient_score=1.0,
        final_score=1.0,
    )
    assert scale_recipe(recipe, 0.5).ingredients[0].amount_text == "1 cup rice"
