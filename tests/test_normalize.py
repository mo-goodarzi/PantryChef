import pytest

from pantry_chef.ingredients.normalize import normalize, singularize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # plurals and descriptors
        ("eggs", "egg"),
        ("Eggs", "egg"),
        ("large eggs", "egg"),
        ("garlic cloves", "garlic clove"),
        ("fresh basil leaves", "basil leaf"),
        ("boneless skinless chicken breasts", "chicken breast"),
        ("tomatoes", "tomato"),
        ("fresh strawberries", "strawberry"),
        ("peaches", "peach"),
        ("chilies", "chili"),
        ("extra virgin olive oil", "olive oil"),
        ("unsalted butter", "butter"),
        ("lemon, juice of", "lemon juice"),
        ("lime, zest of", "lime zest"),
        ("orange, juice and zest of", "orange juice and zest"),
        ("of fresh mint", "mint"),
        ("salt, to taste", "salt to taste"),
        # must stay distinct
        ("egg whites", "egg white"),
        ("egg yolks", "egg yolk"),
        ("eggplant", "eggplant"),
        ("eggnog", "eggnog"),
        ("egg noodles", "egg noodle"),
        # qualifiers that change the ingredient are kept
        ("gluten-free flour", "gluten-free flour"),
        ("gluten-free oats", "gluten-free oat"),
        ("corn tortillas", "corn tortilla"),
        ("flour tortillas", "flour tortilla"),
        ("ground beef", "ground beef"),
        ("dried cranberries", "dried cranberry"),
        ("frozen yogurt", "frozen yogurt"),
        ("almond milk", "almond milk"),
        # words that look plural but are not
        ("molasses", "molasses"),
        ("couscous", "couscous"),
        ("asparagus", "asparagus"),
        ("hummus", "hummus"),
        ("swiss cheese", "swiss cheese"),
        ("tamari", "tamari"),
        ("brussels sprouts", "brussels sprout"),
        # only descriptors: keep the text rather than return nothing
        ("fresh", "fresh"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", ["large eggs", "fresh basil leaves", "cherries", "glass"])
def test_normalize_is_idempotent(raw):
    assert normalize(normalize(raw)) == normalize(raw)


@pytest.mark.parametrize(
    ("word", "expected"),
    [("berries", "berry"), ("potatoes", "potato"), ("boxes", "box"), ("peas", "pea")],
)
def test_singularize(word, expected):
    assert singularize(word) == expected


def test_short_words_are_not_singularized():
    assert singularize("gas") == "gas"
