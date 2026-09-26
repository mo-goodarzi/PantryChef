import pytest

from pantry_chef.ingredients.diet import detect_meat


@pytest.mark.parametrize(
    "name",
    [
        "chicken broth",
        "ground beef",
        "bacon",
        "cooked ham",
        "unflavored gelatin",
        "lard",
        "boneless skinless chicken breasts",
        "italian sausage",
        "beef bouillon cubes",
        "miniature marshmallows",
        "gummy bears",
    ],
)
def test_meat(name):
    assert detect_meat(name)


@pytest.mark.parametrize(
    "name",
    [
        "vegetarian ground beef",
        "veggie burger",
        "vegan sausage",
        "hamburger buns",
        "chickpeas",
        "graham crackers",
        "eggplant",
        "beefsteak tomatoes",
        "steak sauce",
        "soy chorizo",
        "vegetable broth",
        "marshmallow creme",
        "vegan marshmallows",
    ],
)
def test_not_meat(name):
    assert not detect_meat(name)
