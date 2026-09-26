import pytest

from pantry_chef.ingredients.enrich import derive_recipe_data
from pantry_chef.models.query import RecipeQuery
from pantry_chef.search.coverage import ingredient_score, rank_by_coverage, weighted_rating
from pantry_chef.search.filters import filter_conditions

PANCAKES, WAFFLES = 5170, 31750


@pytest.mark.parametrize(
    ("have", "total", "expected"),
    [(4, 4, 1.0), (3, 4, 0.65), (0, 2, -0.2), (0, 0, 1.0)],
)
def test_ingredient_score(have, total, expected):
    assert ingredient_score(have, total) == pytest.approx(expected)


def test_weighted_rating_prefers_many_good_ratings_over_one_perfect():
    mean = 4.5
    assert weighted_rating(4.8, 500, mean) > weighted_rating(5.0, 1, mean)
    assert weighted_rating(None, 0, mean) == mean


def rank(conn, pantry, limit=20):
    conditions, params = filter_conditions(RecipeQuery(ingredients=pantry))
    return rank_by_coverage(conn, pantry, conditions, params, limit)


def test_recipes_with_everything_rank_first(enriched_conn):
    rows, _ = rank(enriched_conn, ["flour", "butter", "egg", "milk"])
    assert {rows[0].recipe_id, rows[1].recipe_id} == {PANCAKES, WAFFLES}
    assert rows[0].ingredient_score == 1.0 and rows[0].have_key == 4


def test_counts_are_per_canonical_name(enriched_conn):
    # Add "egg" next to "eggs" in the pancake recipe: still one key ingredient.
    egg_id = enriched_conn.execute("SELECT id FROM ingredients WHERE name = 'egg'").fetchone()[0]
    enriched_conn.execute(
        "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, position, is_key) "
        "VALUES (?, ?, 99, 1)",
        (PANCAKES, egg_id),
    )
    derive_recipe_data(enriched_conn)
    rows, _ = rank(enriched_conn, ["flour", "butter", "egg", "milk"])
    pancakes = next(r for r in rows if r.recipe_id == PANCAKES)
    assert (pancakes.have_key, pancakes.total_key) == (4, 4)


def test_only_key_ingredients_make_a_recipe_a_candidate(enriched_conn):
    rows, matched = rank(enriched_conn, ["salt", "baking powder"])
    assert rows == [] and matched == 0


def test_partial_coverage_ranks_lower(enriched_conn):
    rows, _ = rank(enriched_conn, ["egg"])
    scores = {r.recipe_id: r.ingredient_score for r in rows}
    assert scores[118761] == 1.0  # microwave poached eggs: egg is the only key ingredient
    assert scores[PANCAKES] < 1.0
    assert rows[0].recipe_id == 118761


def test_limit_and_matched_count(enriched_conn):
    rows, matched = rank(enriched_conn, ["egg", "milk"], limit=2)
    assert len(rows) == 2
    assert matched > 2
