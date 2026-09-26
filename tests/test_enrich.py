from pathlib import Path

import pytest

from pantry_chef.db.connection import connect
from pantry_chef.db.loader import build_database
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.enrich import combine, enrich_database
from pantry_chef.models.ingredient import Category, IngredientLabel, QuantityLabel

FIXTURES = Path(__file__).parent / "fixtures"


def label(name, category=Category.PRODUCE, allergens=(), meat=False, fish=False, animal=False):
    return IngredientLabel(
        name=name,
        category=category,
        allergens=list(allergens),
        contains_meat=meat,
        contains_fish=fish,
        animal_product=animal,
    )


def qty(name, matters=True):
    return QuantityLabel(name=name, quantity_matters=matters)


# --- combine(): one ingredient -------------------------------------------------------


def test_rule_and_llm_allergens_are_combined_with_sources():
    facts = combine(
        "chicken broth",
        label("chicken broth", Category.CONDIMENT, [Allergen.CELERY], meat=True, animal=True),
    )
    assert facts.allergen_sources == {Allergen.CELERY: "llm"}
    assert facts.contains_meat


def test_llm_cannot_remove_a_rule_allergen():
    facts = combine("butter", label("butter", Category.DAIRY, allergens=[]))
    assert facts.allergen_sources == {Allergen.MILK: "rule"}
    assert facts.animal_product  # milk allergen implies animal product


def test_pesto_hidden_nuts_come_from_llm():
    facts = combine(
        "pesto sauce", label("pesto sauce", Category.CONDIMENT, [Allergen.TREE_NUTS, Allergen.MILK])
    )
    assert set(facts.allergen_sources) == {Allergen.TREE_NUTS, Allergen.MILK}


def test_meat_rule_catches_what_llm_missed():
    facts = combine("chicken stock", label("chicken stock", Category.CONDIMENT, meat=False))
    assert facts.contains_meat
    assert facts.animal_product


def test_seafood_allergen_means_contains_fish():
    assert combine("worcestershire sauce", label("worcestershire sauce")).contains_fish


def test_staples_are_never_key_and_quantity_never_matters():
    facts = combine("salt", label("salt", Category.SPICE), qty("salt", True))
    assert facts.is_staple and not facts.is_key and not facts.quantity_matters


def test_quantity_matters_comes_from_the_quantity_label():
    assert combine("eggs", label("eggs"), qty("eggs", True)).quantity_matters
    assert not combine("flour", label("flour"), qty("flour", False)).quantity_matters


def test_quantity_does_not_matter_without_a_quantity_label():
    assert not combine("eggs", label("eggs"), None).quantity_matters


def test_flour_is_key_not_staple():
    facts = combine("all-purpose flour", label("all-purpose flour", Category.GRAIN))
    assert not facts.is_staple and facts.is_key


@pytest.mark.parametrize("category", [Category.SPICE, Category.CONDIMENT, Category.FAT])
def test_spices_condiments_and_fats_are_not_key(category):
    assert not combine("paprika", label("paprika", category)).is_key


def test_unlabeled_ingredient_is_key_and_keeps_rule_allergens():
    facts = combine("egg noodles", None)
    assert facts.category is None
    assert facts.is_key
    assert set(facts.allergen_sources) == {Allergen.EGGS, Allergen.GLUTEN}


def test_canonical_name_uses_normalizer():
    assert combine("large eggs", None).canonical_name == "egg"


# --- enrich_database(): fixture DB ---------------------------------------------------

MEAT_FIXTURE_NAMES = {
    "chicken",
    "boneless skinless chicken breasts",
    "chicken broth",
    "chicken breasts",
    "ham",
    "bacon",
}


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "pantry.db"
    build_database(FIXTURES / "recipes_sample.csv", db_path)
    connection = connect(db_path)
    yield connection
    connection.close()


def all_labels(conn, skip=()):
    names = [r["name"] for r in conn.execute("SELECT name FROM ingredients")]
    return {n: label(n, meat=n in MEAT_FIXTURE_NAMES) for n in names if n not in skip}


def recipe(conn, recipe_id):
    return conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()


def test_recipe_allergens_are_materialized(conn):
    enrich_database(conn, all_labels(conn))
    allergens = {
        r["allergen"]
        for r in conn.execute("SELECT allergen FROM recipe_allergens WHERE recipe_id = 9000001")
    }
    assert allergens == {"eggs", "milk"}  # eggs, milk, butter


def test_diet_flags(conn):
    enrich_database(conn, all_labels(conn))
    eggs_milk_butter = recipe(conn, 9000001)
    assert (
        eggs_milk_butter["is_vegetarian"],
        eggs_milk_butter["is_vegan"],
        eggs_milk_butter["is_gluten_free"],
    ) == (1, 0, 1)
    bread_only = recipe(conn, 9000002)
    assert (bread_only["is_vegan"], bread_only["is_gluten_free"]) == (1, 0)


def test_diet_flags_are_unknown_when_an_ingredient_is_unlabeled(conn):
    enrich_database(conn, all_labels(conn, skip={"bread"}))
    bread_only = recipe(conn, 9000002)
    assert bread_only["is_vegan"] is None
    assert bread_only["is_gluten_free"] == 0  # a known allergen still decides


def test_chicken_recipe_is_not_vegetarian(conn):
    enrich_database(conn, all_labels(conn))
    soups = conn.execute(
        "SELECT is_vegetarian FROM recipes WHERE name = 'thai coconut chicken soup'"
    ).fetchone()
    assert soups["is_vegetarian"] == 0


def test_quantity_labels_are_written_to_ingredients(conn):
    counts = enrich_database(conn, all_labels(conn), {"eggs": qty("eggs"), "salt": qty("salt")})
    rows = dict(
        conn.execute(
            "SELECT name, quantity_matters FROM ingredients WHERE name IN ('eggs', 'salt', 'milk')"
        ).fetchall()
    )
    assert rows == {"eggs": 1, "salt": 0, "milk": 0}  # salt is a staple
    assert counts["quantity_matters"] == 1


def test_key_ingredients_and_staples(conn):
    enrich_database(conn, all_labels(conn))
    rows = {
        r["name"]: r
        for r in conn.execute(
            "SELECT i.name, i.is_staple, ri.is_key FROM recipe_ingredients ri "
            "JOIN ingredients i ON i.id = ri.ingredient_id"
        )
    }
    assert rows["salt"]["is_staple"] == 1 and rows["salt"]["is_key"] == 0
    assert rows["eggs"]["is_key"] == 1


def test_enrichment_is_idempotent(conn):
    labels = all_labels(conn)
    first = enrich_database(conn, labels)
    second = enrich_database(conn, labels)
    assert first == second
    total = conn.execute("SELECT COUNT(*) FROM ingredient_allergens").fetchone()[0]
    enrich_database(conn, labels)
    assert conn.execute("SELECT COUNT(*) FROM ingredient_allergens").fetchone()[0] == total
