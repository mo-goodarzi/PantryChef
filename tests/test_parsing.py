import math

import pytest

from pantry_chef.db.parsing import (
    clean_ingredients,
    clean_name,
    clean_tags,
    clean_text,
    parse_list,
    split_nutrition,
)


def test_parse_list_reads_python_list_literal():
    assert parse_list("['a', \"b's\", 'c']") == ["a", "b's", "c"]


def test_parse_list_rejects_non_lists():
    with pytest.raises(ValueError):
        parse_list("{'a': 1}")


def test_parse_list_never_executes_code():
    with pytest.raises(ValueError):
        parse_list("__import__('os').system('echo hacked')")


def test_split_nutrition_names_all_seven_values():
    result = split_nutrition("[51.5, 0.0, 13.0, 0.0, 2.0, 0.0, 4.0]")
    assert result == {
        "calories": 51.5,
        "total_fat_pdv": 0.0,
        "sugar_pdv": 13.0,
        "sodium_pdv": 0.0,
        "protein_pdv": 2.0,
        "sat_fat_pdv": 0.0,
        "carbs_pdv": 4.0,
    }


def test_split_nutrition_rejects_wrong_length():
    with pytest.raises(ValueError, match="expected 7"):
        split_nutrition("[1.0, 2.0]")


def test_clean_ingredients_lowercases_trims_and_dedupes_keeping_first_position():
    raw = ["Eggs", "  milk", "eggs ", "MILK", "all-purpose   flour"]
    assert clean_ingredients(raw) == ["eggs", "milk", "all-purpose flour"]


def test_clean_ingredients_keeps_egg_and_eggs_distinct():
    # Merging singular/plural is normalization (Phase 2), not ingestion.
    assert clean_ingredients(["egg", "eggs"]) == ["egg", "eggs"]


def test_clean_ingredients_drops_empty_names():
    assert clean_ingredients(["", "   ", "salt"]) == ["salt"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("arriba   baked winter squash", "arriba baked winter squash"),
        ("  pancakes ", "pancakes"),
        ("-------------", None),
        ("", None),
        (None, None),
        (math.nan, None),
    ],
)
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_clean_text_turns_nan_and_blank_into_none():
    assert clean_text(math.nan) is None
    assert clean_text("   ") is None
    assert clean_text(" a  b ") == "a b"


def test_clean_tags_drops_empty_and_duplicate_tags():
    assert clean_tags(["easy", "", " easy", "  "]) == ["easy"]
