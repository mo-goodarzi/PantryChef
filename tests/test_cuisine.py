"""The cuisine rule: families, the three-way SQL rule, the fallback and the boost."""

from dataclasses import replace

import pytest

from pantry_chef.db.tag_mapping import CUISINE_TAGS
from pantry_chef.models.query import RecipeQuery
from pantry_chef.search import cuisine as cuisine_rule
from pantry_chef.search.coverage import CoverageRow, rank_by_coverage
from pantry_chef.search.cuisine import (
    CUISINE_FAMILIES,
    FIT_SQL,
    CuisineFit,
    boost_matches,
    containers,
    exclude_other_cuisines,
    family,
    fit_params,
)
from pantry_chef.search.engine import search

# Fixture recipes and their cuisine tags
TUNA_BURRITOS = 422  # mexican, north-american
CALZONES = 402  # italian, european
THAI_SOUP = 282  # thai, asian
WAFFLES = 31750  # german, european
CRAB_MELT = 245  # american, north-american
CHICKEN_SOUP = 194  # no cuisine tag
PANCAKES = 5170  # no cuisine tag


def fits(conn, cuisine: str) -> dict[int, CuisineFit]:
    rows = conn.execute(f"SELECT r.id, {FIT_SQL} AS fit FROM recipes r", fit_params(cuisine))
    return {row["id"]: CuisineFit(row["fit"]) for row in rows}


def add_tag(conn, recipe_id: int, tag: str) -> None:
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
    conn.execute(
        "INSERT INTO recipe_tags (recipe_id, tag_id) SELECT ?, id FROM tags WHERE name = ?",
        (recipe_id, tag),
    )


def row(recipe_id: int, fit: CuisineFit, score: float = 0.5) -> CoverageRow:
    return CoverageRow(
        recipe_id=recipe_id,
        name=f"r{recipe_id}",
        minutes=10,
        total_key=1,
        have_key=1,
        avg_rating=None,
        n_ratings=0,
        ingredient_score=score,
        weighted_rating=4.0,
        cuisine_fit=fit,
    )


# --- the family map ---------------------------------------------------------------------


def test_tex_mex_counts_for_mexican():
    assert "tex-mex" in family("mexican")
    assert {"baja", "oaxacan"} <= family("mexican")


def test_thai_counts_for_asian_but_not_the_reverse():
    assert "thai" in family("asian")
    assert "asian" not in family("thai")
    assert family("thai") == {"thai"}
    assert containers("thai") == {"asian"}


def test_families_include_members_of_members():
    # american -> southern-united-states -> cajun; asian -> chinese -> cantonese
    assert "cajun" in family("american")
    assert "cantonese" in family("asian")
    assert "tex-mex" in family("american")  # through southwestern-united-states


def test_the_middle_east_is_not_asian():
    # Food.com tags most Middle Eastern recipes "asian" too; the family map does not
    assert "lebanese" not in family("asian")
    assert "lebanese" in family("middle-eastern")


def test_every_family_name_and_member_is_a_cuisine_tag():
    names = set(CUISINE_FAMILIES) | {m for ms in CUISINE_FAMILIES.values() for m in ms}
    assert names <= set(CUISINE_TAGS)


# --- the SQL rule on the fixture database -----------------------------------------------


def test_matching_tag_other_cuisine_and_untagged(enriched_conn):
    add_tag(enriched_conn, PANCAKES, "north-american")
    fit = fits(enriched_conn, "mexican")
    assert fit[TUNA_BURRITOS] is CuisineFit.MATCH
    assert fit[CALZONES] is CuisineFit.OTHER  # italian only
    assert fit[CRAB_MELT] is CuisineFit.OTHER  # american
    assert fit[CHICKEN_SOUP] is CuisineFit.NEUTRAL  # untagged: kept, not boosted
    # "north-american" holds "mexican": it says nothing against the recipe
    assert fit[PANCAKES] is CuisineFit.NEUTRAL


def test_a_family_member_is_a_match(enriched_conn):
    add_tag(enriched_conn, CHICKEN_SOUP, "tex-mex")
    assert fits(enriched_conn, "mexican")[CHICKEN_SOUP] is CuisineFit.MATCH
    assert fits(enriched_conn, "asian")[THAI_SOUP] is CuisineFit.MATCH


def test_a_recipe_tagged_with_the_cuisine_and_another_one_is_kept(enriched_conn):
    add_tag(enriched_conn, CALZONES, "mexican")  # now italian AND mexican
    assert fits(enriched_conn, "mexican")[CALZONES] is CuisineFit.MATCH
    assert fits(enriched_conn, "italian")[CALZONES] is CuisineFit.MATCH


def test_a_broad_tag_that_holds_the_cuisine_is_neutral(enriched_conn):
    add_tag(enriched_conn, CHICKEN_SOUP, "asian")  # only the broad tag
    assert fits(enriched_conn, "thai")[CHICKEN_SOUP] is CuisineFit.NEUTRAL
    assert fits(enriched_conn, "asian")[CHICKEN_SOUP] is CuisineFit.MATCH


def test_specific_tags_decide_over_broad_ones(enriched_conn):
    # like a Lebanese recipe that Food.com also tagged "asian"
    add_tag(enriched_conn, WAFFLES, "asian")  # german + european + asian
    assert fits(enriched_conn, "asian")[WAFFLES] is CuisineFit.OTHER
    assert fits(enriched_conn, "german")[WAFFLES] is CuisineFit.MATCH


def test_coverage_rows_carry_the_fit_only_when_a_cuisine_is_asked(enriched_conn):
    pantry = ["tuna", "beef", "mushroom"]
    asked, _ = rank_by_coverage(enriched_conn, pantry, ["1"], {}, cuisine="mexican")
    by_id = {r.recipe_id: r.cuisine_fit for r in asked}
    assert by_id[TUNA_BURRITOS] is CuisineFit.MATCH
    assert by_id[CALZONES] is CuisineFit.OTHER
    plain, _ = rank_by_coverage(enriched_conn, pantry, ["1"], {})
    assert {r.cuisine_fit for r in plain} == {CuisineFit.NEUTRAL}


# --- exclusion and fallback -------------------------------------------------------------


def test_other_cuisines_are_excluded_while_enough_are_left():
    rows = [row(i, CuisineFit.NEUTRAL) for i in range(5)] + [
        row(10, CuisineFit.OTHER),
        row(11, CuisineFit.MATCH),
    ]
    kept, outcome = exclude_other_cuisines(rows, "mexican")
    assert [r.recipe_id for r in kept] == [0, 1, 2, 3, 4, 11]
    assert (outcome.matched, outcome.excluded, outcome.fallback) == (1, 1, False)


def test_fallback_keeps_other_cuisines_below_the_threshold():
    rows = [row(i, CuisineFit.NEUTRAL) for i in range(4)] + [row(10, CuisineFit.OTHER)]
    kept, outcome = exclude_other_cuisines(rows, "mexican")
    assert kept == rows
    assert outcome.fallback and outcome.excluded == 0


def test_only_other_cuisines_never_leaves_nothing():
    rows = [row(i, CuisineFit.OTHER) for i in range(3)]
    kept, outcome = exclude_other_cuisines(rows, "thai")
    assert kept == rows and outcome.fallback


# --- the boost ----------------------------------------------------------------------------


def test_boost_lifts_a_match_over_a_close_neutral_recipe():
    rows = [row(1, CuisineFit.NEUTRAL, 0.60), row(2, CuisineFit.MATCH, 0.55)]
    boosted = boost_matches(rows, 0.10)
    assert [r.recipe_id for r in boosted] == [2, 1]
    assert boosted[0].final == pytest.approx(0.65)
    assert boosted[1].final == pytest.approx(0.60)  # unchanged


def test_boost_does_not_beat_a_much_better_recipe():
    rows = [row(1, CuisineFit.NEUTRAL, 0.90), row(2, CuisineFit.MATCH, 0.55)]
    assert [r.recipe_id for r in boost_matches(rows, 0.10)] == [1, 2]


def test_boost_is_added_to_the_semantic_final_score():
    semantic = replace(row(2, CuisineFit.MATCH, 0.2), final_score=0.5)
    assert boost_matches([semantic], 0.10)[0].final == pytest.approx(0.6)


# --- in search() ----------------------------------------------------------------------------

PANTRY = ["tuna", "beef", "mushroom", "onion", "eggs", "flour", "chicken breast"]


def test_search_without_cuisine_has_no_cuisine_outcome(enriched_conn):
    assert search(enriched_conn, RecipeQuery(ingredients=PANTRY)).cuisine is None


def test_search_excludes_other_cuisines_and_boosts_matches(enriched_conn, monkeypatch):
    monkeypatch.setattr(cuisine_rule, "FALLBACK_MIN", 1)
    plain = search(enriched_conn, RecipeQuery(ingredients=PANTRY))
    result = search(enriched_conn, RecipeQuery(ingredients=PANTRY, cuisine="mexican"))
    ids = [c.recipe_id for c in result.candidates]
    assert CALZONES in [c.recipe_id for c in plain.candidates]
    assert CALZONES not in ids and TUNA_BURRITOS in ids
    assert result.cuisine is not None and not result.cuisine.fallback
    assert result.cuisine.excluded >= 1
    assert result.matched_recipes == plain.matched_recipes - result.cuisine.excluded
    tuna = next(c for c in result.candidates if c.recipe_id == TUNA_BURRITOS)
    tuna_before = next(c for c in plain.candidates if c.recipe_id == TUNA_BURRITOS)
    assert tuna.final_score == pytest.approx(tuna_before.final_score + 0.10)


def test_search_fallback_never_leaves_the_user_without_recipes(enriched_conn, monkeypatch):
    monkeypatch.setattr(cuisine_rule, "FALLBACK_MIN", 1000)  # every search falls back
    plain = search(enriched_conn, RecipeQuery(ingredients=PANTRY))
    result = search(enriched_conn, RecipeQuery(ingredients=PANTRY, cuisine="korean"))
    assert result.cuisine is not None and result.cuisine.fallback
    assert {c.recipe_id for c in result.candidates} == {c.recipe_id for c in plain.candidates}


def test_too_few_recipes_of_the_cuisine_fall_back_on_their_own(enriched_conn):
    # onion + beef: calzones (italian), tuna burritos (mexican), chicken supreme (no tag).
    # Only one is not another cuisine, fewer than FALLBACK_MIN: all three are kept.
    query = RecipeQuery(ingredients=["onion", "beef"], cuisine="korean")
    result = search(enriched_conn, query)
    assert result.cuisine is not None and result.cuisine.fallback
    assert {c.recipe_id for c in result.candidates} == {CALZONES, TUNA_BURRITOS, 174}
