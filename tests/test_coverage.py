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


# --- pantry usage --------------------------------------------------------------------


def test_usage_term_mixes_coverage_and_pantry_usage():
    from pantry_chef.search.coverage import ingredient_score

    # a one-ingredient recipe using 1 of 4 pantry items vs a 5-ingredient dish using all 4
    tiny = ingredient_score(1, 1, usage=0.25, usage_weight=0.5)
    dish = ingredient_score(4, 5, usage=1.0, usage_weight=0.5)
    assert tiny == pytest.approx(0.625) and dish == pytest.approx(0.85)
    assert dish > tiny
    assert ingredient_score(1, 1, usage=0.25, usage_weight=0.0) == 1.0  # old behavior


def test_one_pantry_item_covering_two_names_counts_once():
    from pantry_chef.search.coverage import pantry_usage

    covered = {"pasta": "pasta", "spaghetti": "pasta", "pasta noodle": "pasta", "egg": "egg"}
    assert pantry_usage(["spaghetti", "pasta noodle"], covered) == 1
    assert pantry_usage(["spaghetti", "egg", "unknown"], covered) == 2


def test_usage_weight_prefers_recipes_that_use_more_of_the_pantry(enriched_conn):
    # Pantry egg + flour + milk (no butter): pancakes miss only butter but use all three
    # items; microwave poached eggs have full coverage but use one item.
    conditions, params = filter_conditions(RecipeQuery(ingredients=[]))
    pantry = ["egg", "flour", "milk"]

    def order(weight):
        rows, _ = rank_by_coverage(enriched_conn, pantry, conditions, params, usage_weight=weight)
        ids = [r.recipe_id for r in rows]
        return ids.index(PANCAKES), ids.index(118761), {r.recipe_id: r for r in rows}

    pancakes, poached, _ = order(0.0)
    assert poached < pancakes  # coverage alone prefers the one-ingredient recipe
    pancakes, poached, rows = order(0.5)
    assert pancakes < poached
    assert rows[PANCAKES].pantry_used == 3 and rows[118761].pantry_used == 1
    assert rows[PANCAKES].ingredient_score == pytest.approx(0.5 * 0.65 + 0.5 * 1.0)


def test_pantry_size_ignores_staples(enriched_conn):
    from pantry_chef.search.engine import search

    result = search(
        enriched_conn, RecipeQuery(ingredients=["egg", "salt", "sugar"]), usage_weight=0.5
    )
    poached = next(c for c in result.candidates if c.recipe_id == 118761)
    assert poached.ingredient_score == pytest.approx(1.0)  # uses 1 of 1 non-staple item


# --- protein weight and nutrition goals ------------------------------------------------

CALZONES, EGG_FOO_YUNG, FETTUCCINE = 402, 133513, 361


def test_weighted_pantry_usage_counts_each_item_with_its_weight():
    from pantry_chef.search.coverage import pantry_usage

    covered = {"beef": "beef", "onion": "onion", "garlic": "garlic", "ground beef": "beef"}
    weights = {"beef": 2.0, "onion": 1.0, "garlic": 1.0}
    assert pantry_usage(["onion", "garlic"], covered, weights) == 2.0
    assert pantry_usage(["beef", "ground beef", "onion"], covered, weights) == 3.0  # beef once
    assert pantry_usage(["beef"], covered) == 1.0  # no weights: every item counts 1


def test_pantry_weights_double_protein_items_and_skip_staples(enriched_conn):
    from pantry_chef.search.engine import pantry_weights

    covered = {"beef": "beef", "mushroom": "mushroom", "salt": "salt"}
    assert pantry_weights(enriched_conn, covered) == {"beef": 2.0, "mushroom": 1.0}


def test_unknown_pantry_item_takes_the_category_of_the_names_it_covers(enriched_conn):
    from pantry_chef.search.engine import pantry_weights

    # "meat steak" is not a dataset name; the matcher expanded it to "beef" (protein).
    covered = {"meat steak": "meat steak", "beef": "meat steak", "nonsense": "nonsense"}
    assert pantry_weights(enriched_conn, covered) == {"meat steak": 2.0, "nonsense": 1.0}


def test_protein_item_counts_double_in_usage(enriched_conn):
    # Pantry beef + mushroom + onion + butter. Calzones use beef + mushroom; chicken supreme
    # uses mushroom + onion + butter. Unweighted, chicken supreme uses more of the pantry;
    # weighted, beef counts double and the two tie on usage (3 of 5).
    from pantry_chef.search.engine import pantry_weights

    pantry = ["beef", "mushroom", "onion", "butter"]
    conditions, params = filter_conditions(RecipeQuery(ingredients=pantry))
    weights = pantry_weights(enriched_conn, {n: n for n in pantry})
    rows, _ = rank_by_coverage(
        enriched_conn, pantry, conditions, params, usage_weight=0.5, pantry_weights=weights
    )
    by_id = {r.recipe_id: r for r in rows}
    assert by_id[CALZONES].pantry_used == 3.0 and by_id[174].pantry_used == 3.0
    assert by_id[CALZONES].pantry_usage == pytest.approx(0.6)


@pytest.mark.parametrize(
    ("facts", "high"),
    [
        # meat or fish: >= 20% DV, or no number (Food.com cannot count "4 steaks")
        ({"protein_pdv": 63.0, "calories": 400.0, "meat_or_fish_key": True}, True),
        ({"protein_pdv": 0.0, "calories": 0.0, "meat_or_fish_key": True}, True),  # "4 steaks"
        ({"protein_pdv": None, "meat_or_fish_key": True}, True),
        ({"protein_pdv": 20.0, "calories": 200.0, "meat_or_fish_key": True}, True),  # edge
        # meat as a topping: real, low numbers (pizza snacks 8%, bacon potatoes 13%)
        ({"protein_pdv": 8.0, "calories": 56.0, "meat_or_fish_key": True}, False),
        ({"protein_pdv": 19.9, "calories": 250.0, "meat_or_fish_key": True}, False),
        # no calorie share for meat: a fatty stroganoff with plenty of protein still counts
        ({"protein_pdv": 45.0, "calories": 900.0, "meat_or_fish_key": True}, True),
        # other protein sources (eggs, tofu, beans, lentils) need >= 20% DV and >= 20% kcal
        ({"protein_pdv": 26.0, "calories": 220.0, "protein_source_key": True}, True),  # lentils
        ({"protein_pdv": 20.0, "calories": 200.0, "protein_source_key": True}, True),  # edges
        ({"protein_pdv": 19.9, "calories": 100.0, "protein_source_key": True}, False),
        ({"protein_pdv": 30.0, "calories": 1200.0, "protein_source_key": True}, False),  # cookies
        ({"protein_pdv": 30.0, "calories": None, "protein_source_key": True}, False),
        # no protein source: the numbers alone are too noisy (pizza dough at 363%)
        ({"protein_pdv": 363.0, "calories": 1000.0}, False),
        ({}, False),  # unknown nutrition is not high protein
    ],
)
def test_high_protein_rule(facts, high):
    from pantry_chef.ingredients.protein import ProteinFacts, is_high_protein
    from pantry_chef.models.query import NutritionGoal
    from pantry_chef.search.coverage import goal_penalty

    recipe = ProteinFacts(**facts)
    assert is_high_protein(recipe) is high
    assert goal_penalty([NutritionGoal.HIGH_PROTEIN], high) == (0.0 if high else 0.25)
    assert goal_penalty([], high) == 0.0


def test_protein_calorie_share():
    from pantry_chef.ingredients.protein import protein_calorie_share

    assert protein_calorie_share(25.0, 250.0) == pytest.approx(0.2)  # 12.5 g = 50 of 250 kcal
    assert protein_calorie_share(25.0, 0.0) is None
    assert protein_calorie_share(None, 250.0) is None


def test_high_protein_goal_lowers_only_recipes_that_miss_it(enriched_conn):
    from pantry_chef.ingredients.enrich import derive_high_protein
    from pantry_chef.models.query import NutritionGoal

    # Poached eggs: 2 eggs instead of 1 per serving (24% DV, 143 kcal -> 34% from protein).
    enriched_conn.execute("UPDATE recipes SET protein_pdv = 24, calories = 143 WHERE id = 118761")
    derive_high_protein(enriched_conn)  # the flag is computed at enrich time
    pantry = ["beef", "egg", "butter", "mushroom", "scallion"]
    conditions, params = filter_conditions(RecipeQuery(ingredients=pantry))

    def scores(goals):
        rows, _ = rank_by_coverage(enriched_conn, pantry, conditions, params, goals=goals)
        return {r.recipe_id: r.ingredient_score for r in rows}

    plain, goal = scores([]), scores([NutritionGoal.HIGH_PROTEIN])
    assert goal[CALZONES] == pytest.approx(plain[CALZONES] - 0.25)  # beef filling, 6% DV
    assert goal[118761] == plain[118761]  # eggs with the numbers to show it
    assert goal[PANCAKES] == pytest.approx(plain[PANCAKES] - 0.25)  # eggs, 11% DV
    # eggs and 45% DV, but 852 kcal of ramen and butter: 11% of calories from protein
    assert goal[EGG_FOO_YUNG] == pytest.approx(plain[EGG_FOO_YUNG] - 0.25)
    # 38% DV and 21% of calories, but from dairy only: no protein-source ingredient
    assert goal[FETTUCCINE] == pytest.approx(plain[FETTUCCINE] - 0.25)


def test_search_passes_the_query_goals_to_the_ranking(enriched_conn):
    from pantry_chef.ingredients.enrich import derive_high_protein
    from pantry_chef.models.query import NutritionGoal
    from pantry_chef.search.engine import search

    # Food.com could not count the beef (0%): the meat decides.
    enriched_conn.execute(f"UPDATE recipes SET protein_pdv = 0 WHERE id = {CALZONES}")
    derive_high_protein(enriched_conn)
    query = RecipeQuery(ingredients=["egg", "beef"])
    plain = {c.recipe_id: c.ingredient_score for c in search(enriched_conn, query).candidates}
    query = query.model_copy(update={"nutrition_goals": [NutritionGoal.HIGH_PROTEIN]})
    goal = {c.recipe_id: c.ingredient_score for c in search(enriched_conn, query).candidates}
    assert goal[118761] == pytest.approx(plain[118761] - 0.25)  # one egg: 12% DV
    assert goal[CALZONES] == plain[CALZONES]


def test_high_protein_is_computed_once_at_enrich_time(enriched_conn):
    flags = dict(enriched_conn.execute("SELECT id, is_high_protein FROM recipes").fetchall())
    assert flags[CALZONES] == 0  # beef, but only 6% DV per serving
    assert flags[PANCAKES] == 0  # eggs, but 11% DV
    assert set(flags.values()) <= {0, 1}  # never unknown after enrichment


def test_enrich_adds_the_column_to_an_older_database(enriched_conn):
    from pantry_chef.ingredients.enrich import derive_high_protein

    enriched_conn.execute("ALTER TABLE recipes DROP COLUMN is_high_protein")
    enriched_conn.execute(f"UPDATE recipes SET protein_pdv = 0 WHERE id = {CALZONES}")
    assert derive_high_protein(enriched_conn) >= 1
    assert (
        enriched_conn.execute(
            "SELECT is_high_protein FROM recipes WHERE id = ?", (CALZONES,)
        ).fetchone()[0]
        == 1
    )


def test_a_goal_on_a_database_without_the_flag_explains_what_to_do(enriched_conn):
    from pantry_chef.models.query import NutritionGoal

    enriched_conn.execute("ALTER TABLE recipes DROP COLUMN is_high_protein")
    pantry = ["beef", "egg"]
    conditions, params = filter_conditions(RecipeQuery(ingredients=pantry))
    rank_by_coverage(enriched_conn, pantry, conditions, params)  # no goal: still works
    with pytest.raises(RuntimeError, match="enrich_db.py"):
        rank_by_coverage(
            enriched_conn, pantry, conditions, params, goals=[NutritionGoal.HIGH_PROTEIN]
        )
