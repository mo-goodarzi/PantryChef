import pytest

from pantry_chef.db.quantities import counted_quantities, parse_quantity


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2", 2.0),
        ("0.5", 0.5),
        ("1⁄2", 0.5),  # irkaal uses the fraction slash "⁄", not "/"
        ("1 1⁄2", 1.5),
        ("1/4", 0.25),
        ("1 -2", 1.0),  # range: lower bound
        ("1⁄4 - 1", 0.25),
        (None, None),
        ("", None),
        ("-", None),
        ("a few", None),
        ("1/0", None),
    ],
)
def test_parse_quantity(raw, expected):
    assert parse_quantity(raw) == expected


RECIPE = {"eggs", "flour", "milk", "garlic cloves"}


def test_counted_quantities_keeps_only_counted_ingredients():
    parts = ["eggs", "flour", "milk"]
    quantities = ["2", "1 1⁄2", "1"]
    assert counted_quantities(parts, quantities, RECIPE) == {"eggs": 2.0}


def test_counted_quantities_requires_equal_list_lengths():
    # irkaal dropped a name, so positions no longer line up: trust nothing.
    parts = ["eggs", "milk"]
    quantities = ["1", "2", "1"]
    assert counted_quantities(parts, quantities, RECIPE) == {}


def test_counted_quantities_requires_name_in_our_recipe():
    assert counted_quantities(["egg whites"], ["3"], RECIPE) == {}


def test_counted_quantities_matches_names_case_insensitively():
    assert counted_quantities(["Garlic  Cloves"], ["3"], RECIPE) == {"garlic cloves": 3.0}


@pytest.mark.parametrize("raw", [None, "0", "500", "a few"])
def test_counted_quantities_drops_missing_or_implausible_counts(raw):
    assert counted_quantities(["eggs"], [raw], RECIPE) == {}


def test_counted_quantities_keeps_first_when_ingredient_repeats():
    assert counted_quantities(["eggs", "eggs"], ["2", "1"], RECIPE) == {"eggs": 2.0}
