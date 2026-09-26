"""Staples: ingredients assumed to be in every kitchen, so they never count as missing.

Default list from the plan: salt, black pepper, water, vegetable oil, olive oil, sugar.
Flour and butter are deliberately NOT staples (owner decision, see docs/decisions.md).
Names are canonical (output of normalize), including common variants of each staple.
"""

from pantry_chef.ingredients.normalize import normalize

STAPLES = frozenset(
    {
        # salt
        "salt",
        "kosher salt",
        "sea salt",
        "coarse salt",
        "table salt",
        "salt to taste",
        # black pepper
        "pepper",
        "black pepper",
        "ground black pepper",
        "ground pepper",
        "cracked black pepper",
        "coarse ground black pepper",
        # salt and pepper together
        "salt and pepper",
        "salt & pepper",
        "salt and black pepper",
        "salt & ground black pepper",
        "salt and ground black pepper",
        # water
        "water",
        "boiling water",
        "hot water",
        "ice water",
        # vegetable oil (generic cooking oils count as vegetable oil)
        "vegetable oil",
        "oil",
        "cooking oil",
        "canola oil",
        # olive oil
        "olive oil",
        "light olive oil",
        # sugar
        "sugar",
        "granulated sugar",
        "white sugar",
    }
)


def is_staple(name: str) -> bool:
    return normalize(name) in STAPLES
