import pytest

from pantry_chef.ingredients.staples import is_staple


@pytest.mark.parametrize(
    "name",
    [
        "salt",
        "kosher salt",
        "salt and pepper",
        "fresh ground black pepper",
        "cold water",
        "vegetable oil",
        "canola oil",
        "extra virgin olive oil",
        "sugar",
        "granulated sugar",
    ],
)
def test_staples(name):
    assert is_staple(name)


@pytest.mark.parametrize(
    "name",
    [
        # owner decision: flour and butter are not staples
        "flour",
        "all-purpose flour",
        "butter",
        "unsalted butter",
        # similar names that are not staples
        "brown sugar",
        "powdered sugar",
        "sesame oil",
        "garlic salt",
        "cayenne pepper",
        "red bell pepper",
        "coconut water",
    ],
)
def test_not_staples(name):
    assert not is_staple(name)
