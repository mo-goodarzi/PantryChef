import pytest

from pantry_chef.db.tag_mapping import (
    BROAD_CUISINES,
    MEAL_TYPE_BY_TAG,
    SPECIFIC_CUISINES,
    derive_cuisine,
    derive_meal_type,
)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["course", "breakfast", "main-dish"], "breakfast"),
        (["brunch", "main-dish"], "main-dish"),  # brunch is only a fallback
        (["brunch", "easy"], "breakfast"),
        (["lunch", "salads"], "salad"),
        (["desserts", "cookies-and-brownies"], "dessert"),
        (["easy", "30-minutes-or-less"], None),
        ([], None),
    ],
)
def test_derive_meal_type(tags, expected):
    assert derive_meal_type(tags) == expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["north-american", "mexican"], "mexican"),  # specific beats broad
        (["asian", "thai"], "thai"),
        (["european"], "european"),
        (["cuisine", "easy"], None),
        ([], None),
    ],
)
def test_derive_cuisine(tags, expected):
    assert derive_cuisine(tags) == expected


def test_mapping_lists_have_no_duplicates():
    cuisines = SPECIFIC_CUISINES + BROAD_CUISINES
    assert len(cuisines) == len(set(cuisines))
    meal_tags = [tag for tag, _ in MEAL_TYPE_BY_TAG]
    assert len(meal_tags) == len(set(meal_tags))
