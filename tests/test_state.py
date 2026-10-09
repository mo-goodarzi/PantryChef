"""Runtime state database: match cache, consented profiles (checkpoints: see test_graph)."""

import sqlite3

import pytest

from pantry_chef.db.state import ProfileStore, import_legacy_match_cache, open_state_db
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import Diet


def test_state_db_is_created_and_reopened(tmp_path):
    path = tmp_path / "nested" / "state.db"
    open_state_db(path).close()
    conn = open_state_db(path)  # CREATE TABLE IF NOT EXISTS: reopening is safe
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"match_cache", "profiles"} <= tables


def test_match_cache_keys_cannot_be_null(state_conn):
    with pytest.raises(sqlite3.IntegrityError):
        state_conn.execute("INSERT INTO match_cache VALUES (NULL, NULL, 'same', 'x', 't')")


def test_recipe_database_no_longer_holds_runtime_tables(enriched_conn):
    tables = {r["name"] for r in enriched_conn.execute("SELECT name FROM sqlite_master")}
    assert not {"match_cache", "profiles"} & tables


def test_legacy_match_cache_is_imported_once(tmp_path, state_conn):
    old_db = tmp_path / "pantry.db"
    old = sqlite3.connect(old_db)
    old.execute(
        "CREATE TABLE match_cache (user_term TEXT, recipe_term TEXT, label TEXT, "
        "source TEXT, created_at TEXT, PRIMARY KEY (user_term, recipe_term))"
    )
    old.execute("INSERT INTO match_cache VALUES ('pasta', 'spaghetti', 'substitute', 'llm', 't')")
    old.execute("INSERT INTO match_cache VALUES (NULL, NULL, 'same', 'llm', 't')")  # quirk row
    old.commit()
    old.close()

    assert import_legacy_match_cache(state_conn, old_db) == 1
    assert import_legacy_match_cache(state_conn, old_db) == 0  # already there
    row = state_conn.execute("SELECT * FROM match_cache").fetchone()
    assert (row["user_term"], row["label"]) == ("pasta", "substitute")
    assert row["source"] == "llm:gpt-5.4-mini"  # the model that made every old answer


def old_state_db(path):
    """A state.db from before match_cache was keyed by source."""
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE match_cache (user_term TEXT NOT NULL, recipe_term TEXT NOT NULL, "
        "label TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT, "
        "PRIMARY KEY (user_term, recipe_term))"
    )
    old.execute(
        "INSERT INTO match_cache VALUES ('egg', 'egg noodle', 'contains', "
        "'llm:ingredient_match:v2', 't')"
    )
    old.commit()
    old.close()


def test_an_old_match_cache_is_rekeyed_and_tagged_with_the_old_model(tmp_path):
    path = tmp_path / "state.db"
    old_state_db(path)
    conn = open_state_db(path)
    key = [r["name"] for r in conn.execute("PRAGMA table_info(match_cache)") if r["pk"]]
    assert key == ["user_term", "recipe_term", "source"]
    rows = [tuple(r) for r in conn.execute("SELECT * FROM match_cache")]
    assert rows == [("egg", "egg noodle", "contains", "llm:ingredient_match:v2:gpt-5.4-mini", "t")]
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "match_cache_old" not in tables
    conn.close()
    conn = open_state_db(path)  # migrated once: reopening keeps the tags as they are
    assert [tuple(r) for r in conn.execute("SELECT * FROM match_cache")] == rows


def test_a_failed_migration_leaves_the_old_table(tmp_path, monkeypatch):
    from pantry_chef.db import state

    path = tmp_path / "state.db"
    old_state_db(path)
    monkeypatch.setattr(state, "state_schema", lambda: "CREATE TABLE broken (")
    with pytest.raises(sqlite3.Error):
        open_state_db(path)
    old = sqlite3.connect(path)
    assert old.execute("SELECT source FROM match_cache").fetchall() == [
        ("llm:ingredient_match:v2",)
    ]
    assert not old.execute("SELECT 1 FROM sqlite_master WHERE name = 'match_cache_old'").fetchall()


def test_legacy_import_without_old_table_or_file_does_nothing(tmp_path, state_conn, enriched_conn):
    assert import_legacy_match_cache(state_conn, tmp_path / "missing.db") == 0
    recipe_db = enriched_conn.execute("PRAGMA database_list").fetchone()["file"]
    assert import_legacy_match_cache(state_conn, recipe_db) == 0


PROFILE = UserProfile(
    allergens=[Allergen.PEANUTS],
    diets=[Diet.LOW_SUGAR],
    health_diets=[Diet.LOW_SUGAR],
    consent_to_store=True,
)


def test_profile_is_saved_and_loaded_with_consent(state_conn):
    store = ProfileStore(state_conn)
    assert store.save("alice", PROFILE)
    assert store.load("alice") == PROFILE
    assert store.load("bob") is None


def test_profile_is_never_saved_without_consent(state_conn):
    store = ProfileStore(state_conn)
    assert not store.save("alice", PROFILE.model_copy(update={"consent_to_store": False}))
    assert store.load("alice") is None
    assert state_conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0


def test_profiles_are_keyed_by_a_hash_not_the_user_name(state_conn):
    ProfileStore(state_conn).save("alice", PROFILE)
    [key] = [r["user_id"] for r in state_conn.execute("SELECT user_id FROM profiles")]
    assert "alice" not in key and len(key) == 16


def test_profile_can_be_deleted(state_conn):
    store = ProfileStore(state_conn)
    store.save("alice", PROFILE)
    assert store.delete("alice")
    assert store.load("alice") is None
    assert not store.delete("alice")
