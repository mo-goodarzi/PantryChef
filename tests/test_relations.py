import json
from pathlib import Path

import pytest

from pantry_chef.db.connection import connect
from pantry_chef.db.loader import build_database
from pantry_chef.ingredients.enrich import enrich_database
from pantry_chef.ingredients.relations import draft_relations, load_seed, save_seed
from pantry_chef.models.ingredient import (
    IngredientRelations,
    IngredientRelationsBatch,
    Substitute,
)

FIXTURES = Path(__file__).parent / "fixtures"
VOCABULARY = ["cheese", "mozzarella cheese", "butter", "margarine", "egg", "oil", "mayonnaise"]


class FakeLLM:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "ingredient_relations"
        assert schema is IngredientRelationsBatch
        names = json.loads(variables["ingredients"])
        self.calls.append(names)
        return IngredientRelationsBatch(items=[i for i in self.items if i.name in names])


def relations(name, parents=(), contains=(), substitutes=()):
    return IngredientRelations(
        name=name,
        parents=list(parents),
        contains=list(contains),
        substitutes=[Substitute(name=n, note=note) for n, note in substitutes],
    )


def test_draft_keeps_only_vocabulary_names():
    llm = FakeLLM(
        [
            relations("mozzarella cheese", parents=["cheese", "dairy"]),  # "dairy" not in vocab
            relations("mayonnaise", contains=["egg", "oil", "vinegar"]),
            relations("butter", substitutes=[("margarine", "1:1"), ("ghee", "same")]),
        ]
    )
    seed, summary = draft_relations(["mozzarella cheese", "mayonnaise", "butter"], VOCABULARY, llm)

    assert seed["mozzarella cheese"]["parents"] == ["cheese"]
    assert seed["mayonnaise"]["contains"] == ["egg", "oil"]
    assert seed["butter"]["substitutes"] == [{"name": "margarine", "note": "1:1"}]
    assert sorted(summary.dropped_names) == ["dairy", "ghee", "vinegar"]


def test_draft_never_relates_an_ingredient_to_itself():
    llm = FakeLLM([relations("cheese", parents=["cheese"])])
    seed, _ = draft_relations(["cheese"], VOCABULARY, llm)
    assert seed["cheese"]["parents"] == []


def test_draft_ignores_items_that_were_not_asked_for():
    llm = FakeLLM([relations("butter"), relations("egg")])
    seed, _ = draft_relations(["butter"], VOCABULARY, llm)
    assert set(seed) == {"butter"}


def test_seed_round_trip(tmp_path):
    path = tmp_path / "relations.json"
    save_seed({"b": {"parents": [], "contains": [], "substitutes": []}, "a": {}}, path)
    assert list(load_seed(path)) == ["a", "b"]


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "pantry.db"
    build_database(FIXTURES / "recipes_sample.csv", db_path)
    connection = connect(db_path)
    yield connection
    connection.close()


def names_for(conn, sql):
    return {tuple(row) for row in conn.execute(sql)}


def test_relations_are_stored_for_every_raw_name_of_a_canonical(conn):
    seed = {
        "egg": {
            "parents": [],
            "contains": [],
            "substitutes": [{"name": "butter", "note": "only in baking"}],
        },
        "butter": {"parents": [], "contains": [], "substitutes": []},
    }
    enrich_database(conn, {}, relation_seed=seed)

    rows = names_for(
        conn,
        """
        SELECT a.name, b.name, r.relation, r.note FROM ingredient_relation r
        JOIN ingredients a ON a.id = r.a_id JOIN ingredients b ON b.id = r.b_id""",
    )
    # "eggs" normalizes to "egg", so the seed entry applies to it
    assert ("eggs", "butter", "substitute", "only in baking") in rows


def test_unknown_seed_names_are_ignored(conn):
    seed = {"dragon fruit": {"parents": ["fruit"], "contains": [], "substitutes": []}}
    counts = enrich_database(conn, {}, relation_seed=seed)
    assert counts["relation_rows"] == 0
