"""The conversation graph end to end: fixture database, scripted LLM, every routing branch.

Real-engine tests use the 20-recipe fixture; routing tests use a scripted `find` so each
branch (retry, give up, nothing found, quantities) can be forced exactly.
"""

import json
import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from pantry_chef.agents.finder import RequestAnswer
from pantry_chef.agents.safety import DISCLAIMER, AllergyMention, SafetyAnswer
from pantry_chef.agents.verifier import Verifier
from pantry_chef.db.repository import load_recipe_ingredients, load_steps
from pantry_chef.db.state import ProfileStore
from pantry_chef.graph import nodes
from pantry_chef.graph.builder import build_graph
from pantry_chef.graph.nodes import NEED_PANTRY, ChatDeps
from pantry_chef.graph.runner import Conversation
from pantry_chef.graph.state import ChatState
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.chat import (
    AmountReply,
    ChoiceReply,
    ConfirmReply,
    QuantityReply,
    QuestionKind,
    SafetyReply,
)
from pantry_chef.models.query import AmountStatus, Diet, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import (
    CheckResult,
    FailureCode,
    FailureReason,
    VerificationResult,
    VerificationStatus,
    VerifiedCandidate,
)
from pantry_chef.search.engine import FindResult, SearchOptions, find_verified

PANCAKES, WAFFLES, POACHED_EGGS = 5170, 31750, 118761
BAKING = RequestAnswer(pantry=["flour", "butter", "eggs", "milk"], wish="breakfast")


class ScriptedLLM:
    """Answers by prompt name; records which prompts were called."""

    def __init__(self, **answers):
        self.answers = answers
        self.calls = []

    def generate(self, prompt, schema, **variables):
        self.calls.append(prompt.name)
        answer = self.answers[prompt.name]
        assert isinstance(answer, schema)
        return answer


def make_deps(conn, state_conn, llm, find=None, ask_quantities=False, reverify=None):
    verifier = Verifier(conn)
    return ChatDeps(
        llm=llm,
        find=find or (lambda query: find_verified(conn, query, SearchOptions())),
        reverify=reverify or (lambda c, q, items: verifier.verify_all(c, q, items)),
        steps=lambda recipe_id: load_steps(conn, recipe_id),
        profiles=ProfileStore(state_conn),
        ask_quantities=ask_quantities,
    )


def conversation(deps, user_id="alice", checkpointer=None, thread_id=None):
    graph = build_graph(deps, checkpointer or InMemorySaver())
    return Conversation(graph, thread_id=thread_id, user_id=user_id)


def through_safety(chat, message="I have flour, butter, eggs and milk", consent=False):
    turn = chat.send(message)
    assert turn.question.kind is QuestionKind.SAFETY
    turn = chat.reply(SafetyReply(text="(free text)"))
    assert turn.question.kind is QuestionKind.SAFETY_CONFIRM
    return chat.reply(ConfirmReply(correct=True, consent_to_store=consent))


# --- happy path and profile ------------------------------------------------------------


def test_first_visit_asks_safety_then_offers_recipes_then_answers(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))

    turn = chat.send("I have flour, butter, eggs and milk")
    assert turn.question.kind is QuestionKind.SAFETY
    turn = chat.reply(SafetyReply(text="no allergies"))
    assert "No allergies" in turn.question.text
    turn = chat.reply(ConfirmReply(correct=True))

    assert turn.question.kind is QuestionKind.CHOICE
    options = turn.question.options
    assert {o.recipe_id for o in options} >= {PANCAKES, WAFFLES}
    assert turn.question.note is None
    pancakes = next(o for o in options if o.recipe_id == PANCAKES)
    assert set(pancakes.uses) >= {"flour", "eggs", "butter", "milk"}

    turn = chat.reply(ChoiceReply(choice=pancakes.number))
    assert turn.done and turn.answer.recipe_id == PANCAKES
    assert turn.answer.steps == load_steps(enriched_conn, PANCAKES)
    assert turn.answer.disclaimer is None
    # Each LLM runs once, even though nodes re-run when the graph resumes.
    assert llm.calls == ["safety_intake", "request_parsing"]


def test_consented_profile_is_stored_and_skips_the_intake_next_time(enriched_conn, state_conn):
    llm = ScriptedLLM(
        safety_intake=SafetyAnswer(allergies=[AllergyMention(said="peanuts")]),
        request_parsing=BAKING,
    )
    deps = make_deps(enriched_conn, state_conn, llm)
    through_safety(conversation(deps), consent=True)
    assert ProfileStore(state_conn).load("alice").allergens == [Allergen.PEANUTS]

    turn = conversation(deps).send("I have flour, butter, eggs and milk")
    assert turn.question.kind is QuestionKind.CHOICE  # no safety questions this time
    assert llm.calls.count("safety_intake") == 1


def test_profile_is_not_stored_without_consent(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    through_safety(conversation(make_deps(enriched_conn, state_conn, llm)), consent=False)
    assert ProfileStore(state_conn).load("alice") is None


def test_a_wrong_profile_asks_the_safety_question_again(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    chat.send("eggs")
    chat.reply(SafetyReply(text="none"))
    turn = chat.reply(ConfirmReply(correct=False))
    assert turn.question.kind is QuestionKind.SAFETY


def test_health_restriction_is_applied_with_a_disclaimer(enriched_conn, state_conn):
    llm = ScriptedLLM(
        safety_intake=SafetyAnswer(health_diets=[Diet.LOW_SUGAR]),
        request_parsing=RequestAnswer(pantry=["eggs"]),
    )
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    turn = through_safety(chat, "I have eggs")

    # Only one low-sugar recipe exists: shown with an honest note after the retries.
    assert [o.recipe_id for o in turn.question.options] == [POACHED_EGGS]
    assert turn.question.note
    assert chat.state().query.diets == [Diet.LOW_SUGAR]
    turn = chat.reply(ChoiceReply(choice=1))
    assert turn.answer.disclaimer == DISCLAIMER


# --- safety ----------------------------------------------------------------------------


def test_allergy_user_is_never_shown_a_recipe_with_the_allergen(enriched_conn, state_conn):
    llm = ScriptedLLM(
        safety_intake=SafetyAnswer(allergies=[AllergyMention(said="egg")]),
        request_parsing=BAKING,
    )
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    turn = through_safety(chat)

    shown = [o.recipe_id for o in turn.question.options] if turn.question else []
    ingredients = load_recipe_ingredients(enriched_conn, shown)
    assert not any(Allergen.EGGS in i.allergens for r in shown for i in ingredients[r])
    if not shown:  # every fixture recipe with these items has eggs
        assert "couldn't find" in turn.reply
        assert "needed ingredients you don't have" in turn.reply  # says why, honestly
    # It retried with verifier feedback, and stopped when the query could not change.
    assert chat.state().attempts == 2


def test_allergy_in_the_request_is_added_for_that_request(enriched_conn, state_conn):
    request = BAKING.model_copy(update={"allergies": [AllergyMention(said="eggs")]})
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=request)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    through_safety(chat)
    assert chat.state().query.required_allergen_free == [Allergen.EGGS]
    assert chat.state().profile.allergens == []  # not written into the profile


# --- retries (scripted search) ----------------------------------------------------------


def verified(recipe_id, status="pass", quantity_matters=False):
    egg = RecipeIngredient(
        name="eggs",
        canonical_name="egg",
        category="protein",
        is_key=True,
        quantity_matters=quantity_matters,
        allergens=[Allergen.EGGS],
    )
    reasons = []
    if status == "fail":
        reasons = [
            FailureReason(code=FailureCode.MISSING_INGREDIENT, item="flour", detail="needs flour")
        ]
    return VerifiedCandidate(
        candidate=Candidate(
            recipe_id=recipe_id,
            name=f"recipe {recipe_id}",
            minutes=10,
            ingredients=[egg],
            have_key=1,
            total_key=1,
            coverage=1,
            ingredient_score=1,
            final_score=1,
        ),
        verification=VerificationResult(
            candidate_id=recipe_id,
            status=VerificationStatus(status),
            checks=[CheckResult(check="ingredients", passed=status != "fail", reasons=reasons)],
            ingredient_status={"eggs": "available"},
        ),
    )


def found(*candidates):
    approved = [c for c in candidates if c.verification.status is not VerificationStatus.FAIL]
    return FindResult(top=approved, checked=list(candidates), matched_recipes=len(candidates))


class ScriptedFind:
    def __init__(self, *results):
        self.results = list(results)
        self.queries: list[RecipeQuery] = []

    def __call__(self, query):
        self.queries.append(query)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def scripted_chat(enriched_conn, state_conn, find, **deps_options):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    return conversation(make_deps(enriched_conn, state_conn, llm, find=find, **deps_options))


def test_too_few_approved_recipes_retry_with_verifier_feedback(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1), verified(2, "fail")), found(verified(1), verified(3)))
    turn = through_safety(scripted_chat(enriched_conn, state_conn, find))

    assert len(find.queries) == 2
    assert find.queries[1].exclude_recipe_ids == [2]  # the failed recipe is excluded
    assert [o.recipe_id for o in turn.question.options] == [1, 3]


def test_gives_up_after_three_searches_with_an_honest_note(enriched_conn, state_conn):
    failing = iter(range(100, 200))
    find = ScriptedFind(*[found(verified(1), verified(next(failing), "fail")) for _ in range(5)])
    turn = through_safety(scripted_chat(enriched_conn, state_conn, find))

    assert len(find.queries) == 3
    assert [o.recipe_id for o in turn.question.options] == [1]
    assert "Only this recipe" in turn.question.note


def test_no_retry_when_feedback_cannot_change_the_query(enriched_conn, state_conn):
    find = ScriptedFind(found())
    turn = through_safety(scripted_chat(enriched_conn, state_conn, find))
    assert len(find.queries) == 1
    assert turn.done and "couldn't find" in turn.reply


def test_show_me_more_never_repeats_a_recipe(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1), verified(2)), found(verified(3), verified(4)))
    chat = scripted_chat(enriched_conn, state_conn, find)
    through_safety(chat)
    turn = chat.reply(ChoiceReply(more=True))
    assert find.queries[1].exclude_recipe_ids == [1, 2]
    assert [o.recipe_id for o in turn.question.options] == [3, 4]


def test_empty_pantry_asks_for_ingredients_without_searching(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=RequestAnswer())
    find = ScriptedFind(found())
    chat = conversation(make_deps(enriched_conn, state_conn, llm, find=find))
    turn = through_safety(chat, "hello")
    assert turn.reply == NEED_PANTRY and find.queries == []


def test_invalid_choice_is_rejected_before_resuming(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1), verified(2)))
    chat = scripted_chat(enriched_conn, state_conn, find)
    through_safety(chat)
    with pytest.raises(ValueError, match="1 to 2"):
        chat.reply(ChoiceReply(choice=7))
    assert chat.pending_question().kind is QuestionKind.CHOICE


# --- quantities ------------------------------------------------------------------------


def test_quantities_are_not_asked_while_the_step_is_off(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1, quantity_matters=True), verified(2)))
    turn = through_safety(scripted_chat(enriched_conn, state_conn, find))
    assert turn.question.kind is QuestionKind.CHOICE


def test_quantity_question_accepts_i_dont_know(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1, quantity_matters=True), verified(2)))
    seen = []

    def reverify(candidates, query, items):
        seen.append(items)
        return [verified(c.recipe_id).verification for c in candidates]

    chat = scripted_chat(enriched_conn, state_conn, find, ask_quantities=True, reverify=reverify)
    turn = through_safety(chat)
    assert turn.question.kind is QuestionKind.QUANTITIES
    assert turn.question.items == ["egg"]

    turn = chat.reply(QuantityReply(amounts={"egg": AmountReply(status=AmountStatus.UNKNOWN)}))
    assert turn.question.kind is QuestionKind.CHOICE
    [items] = seen
    assert items[0].amount_status is AmountStatus.UNKNOWN and items[0].quantity is None
    # Asked once per request: "show me more" does not ask again about eggs.
    turn = chat.reply(ChoiceReply(more=True))
    assert turn.question.kind is not QuestionKind.QUANTITIES


def test_quantity_answers_are_used_to_re_verify(enriched_conn, state_conn):
    find = ScriptedFind(found(verified(1, quantity_matters=True), verified(2)))

    def reverify(candidates, query, items):  # recipe 1 needs more eggs than the user has
        return [
            verified(c.recipe_id, "fail" if c.recipe_id == 1 else "pass").verification
            for c in candidates
        ]

    chat = scripted_chat(enriched_conn, state_conn, find, ask_quantities=True, reverify=reverify)
    through_safety(chat)
    turn = chat.reply(QuantityReply(amounts={"egg": AmountReply(quantity=1)}))
    assert [o.recipe_id for o in turn.question.options] == [2]


# --- persistence and privacy --------------------------------------------------------------


def test_conversation_survives_a_restart_and_is_deleted_without_consent(
    enriched_conn, state_conn, tmp_path
):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    deps = make_deps(enriched_conn, state_conn, llm)
    path = tmp_path / "state.db"

    def saver():
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))

    first = conversation(deps, checkpointer=saver())
    first.send("I have flour, butter, eggs and milk")

    # "Restart": a new graph and connection, same thread id.
    again = conversation(deps, checkpointer=saver(), thread_id=first.thread_id)
    assert again.pending_question().kind is QuestionKind.SAFETY
    again.reply(SafetyReply(text="none"))
    turn = again.reply(ConfirmReply(correct=True, consent_to_store=False))
    assert turn.question.kind is QuestionKind.CHOICE

    again.close()
    assert again.graph.get_state(again.config).values == {}


def test_conversation_is_kept_with_consent(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    through_safety(chat, consent=True)
    chat.close()
    assert chat.state().profile.consent_to_store


def test_health_text_is_not_kept_in_the_conversation_state(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    chat.send("eggs")
    chat.reply(SafetyReply(text="I have type 2 diabetes"))
    assert "diabetes" not in chat.state().model_dump_json()


# --- traces ----------------------------------------------------------------------------


def test_each_turn_is_a_trace_with_safety_scores(enriched_conn, state_conn, capsys):
    from pantry_chef.observability import configure_logging

    configure_logging("INFO", json_output=True)
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    through_safety(chat)

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    scores = {line["score"]: line["value"] for line in lines if line.get("event") == "score"}
    assert scores["allergen_violation"] == 0
    assert scores["retries"] == 0
    assert {line.get("session_id") for line in lines} == {chat.thread_id}
    turns = [line for line in lines if line.get("span") == "chat_turn"]
    assert len(turns) == 3  # send + two replies


# --- routing functions -------------------------------------------------------------------


def test_routing():
    state = ChatState()
    assert nodes.after_load_profile(state) == "safety_question"
    assert nodes.after_parse_request(state) == nodes.END
    assert nodes.after_search(state.model_copy(update={"retry": True}), False) == "search"
    assert nodes.after_search(state, ask_quantities=True) == "quantity_check"
    assert nodes.after_search(state, ask_quantities=False) == "present"
    assert nodes.after_present(state.model_copy(update={"reply": "x"})) == nodes.END
    assert nodes.after_present(state) == "search"  # "show me more"


def test_close_refuses_to_skip_deletion_silently(enriched_conn, state_conn):
    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    through_safety(chat, consent=False)

    class NoDelete:
        """The real checkpointer, except that it cannot delete threads."""

        def __init__(self, real):
            self.real = real

        def __getattr__(self, name):
            if name == "delete_thread":
                raise AttributeError(name)
            return getattr(self.real, name)

    real = chat.graph.checkpointer
    chat.graph.checkpointer = NoDelete(real)
    try:
        with pytest.raises(RuntimeError, match="cannot delete"):
            chat.close()
    finally:
        chat.graph.checkpointer = real


def test_the_last_check_hides_allergens_even_with_every_search_layer_off(enriched_conn, state_conn):
    from dataclasses import replace

    llm = ScriptedLLM(
        safety_intake=SafetyAnswer(allergies=[AllergyMention(said="egg")]),
        request_parsing=BAKING,
    )
    unsafe_search = SearchOptions(use_allergen_filter=False, use_verifier=False)
    deps = make_deps(
        enriched_conn,
        state_conn,
        llm,
        find=lambda q: find_verified(enriched_conn, q, unsafe_search),
    )

    def shown_with_eggs(deps):
        turn = through_safety(conversation(deps))
        shown = [o.recipe_id for o in turn.question.options] if turn.question else []
        ingredients = load_recipe_ingredients(enriched_conn, shown)
        return any(Allergen.EGGS in i.allergens for r in shown for i in ingredients[r])

    assert not shown_with_eggs(deps)  # the last check in the graph still removes them
    assert shown_with_eggs(replace(deps, final_allergen_check=False))  # eval-only switch
    assert ChatDeps.__dataclass_fields__["final_allergen_check"].default is True
