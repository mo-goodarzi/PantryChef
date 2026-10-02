import sys

import pytest

from pantry_chef.search import cli


def run_cli(monkeypatch, capsys, db_path, *args):
    monkeypatch.setattr(sys, "argv", ["cli", "--db", str(db_path), *args])
    cli.main()
    return capsys.readouterr().out


@pytest.fixture
def db_path(enriched_conn):
    return enriched_conn.execute("PRAGMA database_list").fetchone()["file"]


def test_cli_prints_verified_recipes(monkeypatch, capsys, db_path):
    out = run_cli(monkeypatch, capsys, db_path, "--have", "flour,butter,eggs,milk", "--top", "2")
    assert "pete s scratch pancakes" in out
    assert "the best ever waffles" in out
    assert "uses: flour, eggs, butter, milk" in out


def test_cli_allergy_removes_recipes(monkeypatch, capsys, db_path):
    out = run_cli(
        monkeypatch, capsys, db_path, "--have", "flour,butter,eggs,milk", "--allergy", "egg"
    )
    assert "pancakes" not in out


def test_cli_shows_failure_reasons(monkeypatch, capsys, db_path):
    out = run_cli(monkeypatch, capsys, db_path, "--have", "milk", "--show-failed")
    assert "missing_ingredient: flour" in out


def test_cli_rejects_unknown_allergy(monkeypatch, capsys, db_path):
    with pytest.raises(SystemExit, match="unknown allergy"):
        run_cli(monkeypatch, capsys, db_path, "--have", "eggs", "--allergy", "moonbeams")


def test_build_query_parses_lists_and_aliases():
    args = cli.argparse.Namespace(
        have="eggs, milk,,bread",
        pref="sweet breakfast",
        allergy="shellfish, Peanut",
        diet="gluten-free",
        goal="high-protein",
        exclude="mushrooms",
        max_minutes=30,
        meal_type=None,
        cuisine=None,
    )
    query = cli.build_query(args)
    assert query.ingredients == ["eggs", "milk", "bread"]
    assert {a.value for a in query.required_allergen_free} == {"crustaceans", "molluscs", "peanuts"}
    assert [d.value for d in query.diets] == ["gluten_free"]
    assert [g.value for g in query.nutrition_goals] == ["high_protein"]
    assert query.preferences_text == "sweet breakfast"
