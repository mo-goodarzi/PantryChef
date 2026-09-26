import json
import sqlite3
from pathlib import Path

import pytest

from pantry_chef.db.connection import connect
from pantry_chef.db.loader import build_database

FIXTURES = Path(__file__).parent / "fixtures"
RECIPES_CSV = FIXTURES / "recipes_sample.csv"
INTERACTIONS_CSV = FIXTURES / "interactions_sample.csv"

N_ROWS = 20
N_UNUSABLE = 2  # one recipe with an empty name, one with no steps


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pantry.db"
    build_database(RECIPES_CSV, path, interactions_csv=INTERACTIONS_CSV)
    return path


@pytest.fixture
def conn(db_path: Path):
    connection = connect(db_path)
    yield connection
    connection.close()


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_summary_counts_loaded_and_skipped_recipes(tmp_path):
    summary = build_database(RECIPES_CSV, tmp_path / "pantry.db")

    assert summary.n_recipes == N_ROWS - N_UNUSABLE
    assert summary.skipped == {"empty_name": 1, "no_steps": 1}


def test_unusable_recipes_are_not_loaded(conn):
    ids = {row["id"] for row in conn.execute("SELECT id FROM recipes")}
    assert 368257 not in ids  # name is "-------------"
    assert 176767 not in ids  # empty step list


def test_ingredients_are_deduped_within_a_recipe(conn):
    rows = conn.execute(
        "SELECT i.name, ri.position FROM recipe_ingredients ri "
        "JOIN ingredients i ON i.id = ri.ingredient_id "
        "WHERE ri.recipe_id = 9000001 ORDER BY ri.position"
    ).fetchall()
    assert [(r["name"], r["position"]) for r in rows] == [
        ("eggs", 0),
        ("milk", 1),
        ("butter", 2),
    ]
    recipe = conn.execute("SELECT n_ingredients FROM recipes WHERE id = 9000001").fetchone()
    assert recipe["n_ingredients"] == 3


def test_recipe_fields_are_cleaned(conn):
    recipe = conn.execute("SELECT * FROM recipes WHERE id = 9000001").fetchone()
    assert recipe["name"] == "test duplicate eggs"
    assert recipe["description"] is None
    assert json.loads(recipe["steps_json"]) == ["beat the eggs", "cook"]
    assert recipe["calories"] == 100.0
    assert recipe["carbs_pdv"] == 6.0
    assert recipe["meal_type"] == "breakfast"
    assert recipe["cuisine"] == "mexican"


def test_empty_tags_are_not_stored(conn):
    assert conn.execute("SELECT COUNT(*) FROM tags WHERE name = ''").fetchone()[0] == 0


def test_every_recipe_has_ingredients_and_valid_links(conn):
    assert count(conn, "recipes") == N_ROWS - N_UNUSABLE
    orphans = conn.execute(
        "SELECT COUNT(*) FROM recipes r WHERE NOT EXISTS "
        "(SELECT 1 FROM recipe_ingredients ri WHERE ri.recipe_id = r.id)"
    ).fetchone()[0]
    assert orphans == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_query_for_egg_recipes_returns_sensible_results(conn):
    names = {
        row["name"]
        for row in conn.execute(
            "SELECT DISTINCT r.name FROM recipes r "
            "JOIN recipe_ingredients ri ON ri.recipe_id = r.id "
            "JOIN ingredients i ON i.id = ri.ingredient_id "
            "WHERE i.name IN ('egg', 'eggs')"
        )
    }
    assert "microwave poached eggs" in names
    assert "pete s scratch pancakes" in names
    assert "best lemonade" not in names


def test_recipe_stats_ignore_zero_ratings_in_average(conn):
    stats = conn.execute("SELECT * FROM recipe_stats WHERE recipe_id = 9000001").fetchone()
    assert stats["n_reviews"] == 3
    assert stats["n_ratings"] == 2
    assert stats["avg_rating"] == 4.5


def test_recipe_stats_with_only_unrated_reviews_have_no_average(conn):
    stats = conn.execute("SELECT * FROM recipe_stats WHERE recipe_id = 9000002").fetchone()
    assert stats["n_reviews"] == 1
    assert stats["n_ratings"] == 0
    assert stats["avg_rating"] is None


def test_recipe_stats_skip_unknown_recipes(conn):
    assert conn.execute("SELECT 1 FROM recipe_stats WHERE recipe_id = 123").fetchone() is None


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO recipe_ingredients (recipe_id, ingredient_id, position) "
            "VALUES (999999, 1, 0)"
        )


def test_rebuild_is_idempotent(db_path):
    first = build_database(RECIPES_CSV, db_path, interactions_csv=INTERACTIONS_CSV)
    second = build_database(RECIPES_CSV, db_path, interactions_csv=INTERACTIONS_CSV)

    assert first == second
    conn = connect(db_path)
    assert count(conn, "recipes") == N_ROWS - N_UNUSABLE
    conn.close()
    assert not db_path.with_name(db_path.name + ".tmp").exists()


def test_limit_loads_only_first_rows(tmp_path):
    summary = build_database(RECIPES_CSV, tmp_path / "pantry.db", limit=5)
    assert summary.n_recipes + sum(summary.skipped.values()) == 5


def test_failed_build_keeps_previous_database(db_path, tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("not,the,right,columns\n1,2,3,4\n")

    with pytest.raises(KeyError):
        build_database(bad_csv, db_path)

    conn = connect(db_path)
    assert count(conn, "recipes") == N_ROWS - N_UNUSABLE
    conn.close()
