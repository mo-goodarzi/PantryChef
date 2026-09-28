"""The HTTP API through the typed client the UI uses (fixture DB, scripted LLM)."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from pantry_chef.agents.safety import AllergyMention, SafetyAnswer
from pantry_chef.api.client import ApiError, PantryChefClient
from pantry_chef.api.main import create_app
from pantry_chef.api.schemas import TurnType
from pantry_chef.db.connection import connect
from pantry_chef.db.state import ProfileStore, open_state_db
from pantry_chef.graph.app import ChatApp
from pantry_chef.graph.builder import build_graph
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.chat import ChoiceReply, ConfirmReply, QuestionKind, SafetyReply

from .test_graph import BAKING, PANCAKES, ScriptedLLM, make_deps


@pytest.fixture
def make_client(enriched_conn, tmp_path):
    """make_client(llm, checkpointer=None) -> PantryChefClient over a running API."""
    recipes = Path(enriched_conn.execute("PRAGMA database_list").fetchone()["file"])
    started = []

    def make(llm, checkpointer=None):
        # Worker threads share these connections; the API serializes graph calls.
        conn = connect(recipes, check_same_thread=False)
        state = open_state_db(tmp_path / "state.db", check_same_thread=False)
        graph = build_graph(make_deps(conn, state, llm), checkpointer or InMemorySaver())
        chat = ChatApp(graph=graph, profiles=ProfileStore(state), connections=[conn, state])
        http = TestClient(create_app(lambda: chat))
        http.__enter__()  # runs the lifespan (startup)
        started.append(http)
        return PantryChefClient(http=http)

    yield make
    for http in started:
        http.__exit__(None, None, None)


def default_llm(**overrides):
    answers = {"safety_intake": SafetyAnswer(), "request_parsing": BAKING} | overrides
    return ScriptedLLM(**answers)


def through_safety(client, session, user_id="alice", consent=False):
    turn = client.send(session, "I have flour, butter, eggs and milk", user_id=user_id)
    assert turn.type is TurnType.QUESTION and turn.question.kind is QuestionKind.SAFETY
    turn = client.reply(session, SafetyReply(text="none"))
    assert turn.question.kind is QuestionKind.SAFETY_CONFIRM
    return client.reply(session, ConfirmReply(correct=True, consent_to_store=consent))


def test_health(make_client):
    assert make_client(default_llm()).health()


def test_a_whole_conversation_over_http(make_client):
    client = make_client(default_llm())
    session = client.new_session()

    turn = through_safety(client, session)
    assert turn.type is TurnType.CANDIDATES
    pancakes = next(o for o in turn.question.options if o.recipe_id == PANCAKES)

    turn = client.reply(session, ChoiceReply(choice=pancakes.number))
    assert turn.type is TurnType.FINAL
    assert turn.answer.recipe_id == PANCAKES and turn.answer.steps

    # No consent: closing deletes the conversation.
    client.close(session)
    assert client.session(session).profile is None


def test_an_answer_that_does_not_fit_the_question_is_rejected(make_client):
    client = make_client(default_llm())
    session = client.new_session()
    client.send(session, "I have eggs", user_id="alice")  # pending: the safety question

    with pytest.raises(ApiError) as error:
        client.reply(session, ConfirmReply(correct=True))  # no "text" field
    assert error.value.status == 422
    assert client.session(session).pending_question.kind is QuestionKind.SAFETY


def test_a_choice_that_is_not_an_option_is_rejected(make_client):
    client = make_client(default_llm())
    session = client.new_session()
    through_safety(client, session)
    with pytest.raises(ApiError, match="choose a number"):
        client.reply(session, ChoiceReply(choice=99))


def test_resume_without_a_question_is_a_conflict(make_client):
    client = make_client(default_llm())
    with pytest.raises(ApiError) as error:
        client.reply(client.new_session(), SafetyReply(text="hi"))
    assert error.value.status == 409


def test_consented_profile_is_remembered_and_can_be_deleted(make_client):
    llm = default_llm(safety_intake=SafetyAnswer(allergies=[AllergyMention(said="peanuts")]))
    client = make_client(llm)
    first = client.new_session()
    through_safety(client, first, consent=True)
    assert client.session(first).profile.allergens == [Allergen.PEANUTS]

    # A new conversation for the same user skips the safety questions.
    second = client.new_session()
    turn = client.send(second, "I have flour, butter, eggs and milk", user_id="alice")
    assert turn.type is TurnType.CANDIDATES

    # "Delete my data": the stored profile and this conversation.
    assert client.delete_profile("alice")
    client.close(second, forget=True)
    assert client.session(second).profile is None
    assert not client.delete_profile("alice")  # nothing left
    third = client.new_session()
    turn = client.send(third, "I have flour, butter, eggs and milk", user_id="alice")
    assert turn.question.kind is QuestionKind.SAFETY  # asked again


def test_conversations_survive_an_api_restart(make_client, tmp_path):
    path = tmp_path / "checkpoints.db"

    def saver():
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))

    client = make_client(default_llm(), checkpointer=saver())
    session = client.new_session()
    client.send(session, "I have flour, butter, eggs and milk", user_id="alice")

    restarted = make_client(default_llm(), checkpointer=saver())
    assert restarted.session(session).pending_question.kind is QuestionKind.SAFETY
    turn = restarted.reply(session, SafetyReply(text="none"))
    assert turn.question.kind is QuestionKind.SAFETY_CONFIRM


def test_empty_messages_are_rejected(make_client):
    client = make_client(default_llm())
    with pytest.raises(ApiError) as error:
        client.send(client.new_session(), "")
    assert error.value.status == 422
