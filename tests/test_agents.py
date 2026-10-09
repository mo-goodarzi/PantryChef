"""Safety intake and request parsing: the LLM reads text, code decides (fake LLMs)."""

from pantry_chef.agents.finder import RequestAnswer, build_query, interpret_request
from pantry_chef.agents.safety import (
    AllergyMention,
    SafetyAnswer,
    confirmation_message,
    interpret_safety_answer,
)
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import Diet


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def generate(self, prompt, schema, **variables):
        self.calls.append((prompt, variables))
        assert isinstance(self.answer, schema)
        return self.answer


def said(word, allergen=None):
    return AllergyMention(said=word, allergen=allergen)


# --- safety intake --------------------------------------------------------------------


def test_allergy_words_are_mapped_by_code():
    llm = FakeLLM(SafetyAnswer(allergies=[said("peanuts"), said("shellfish")]))
    intake = interpret_safety_answer(llm, "peanuts and shellfish")
    assert intake.profile.allergens == [Allergen.CRUSTACEANS, Allergen.MOLLUSCS, Allergen.PEANUTS]
    assert intake.allergy_groups == {
        "peanuts": [Allergen.PEANUTS],
        "shellfish": [Allergen.CRUSTACEANS, Allergen.MOLLUSCS],
    }


def test_llm_group_is_used_for_words_the_code_does_not_know_and_can_only_add():
    # "almonds" is not in the alias table; the LLM says tree nuts.
    # "nuts" maps to tree nuts in code; an LLM guess of peanuts is added, never replacing.
    llm = FakeLLM(
        SafetyAnswer(
            allergies=[said("almonds", Allergen.TREE_NUTS), said("nuts", Allergen.PEANUTS)]
        )
    )
    intake = interpret_safety_answer(llm, "almonds, nuts")
    assert intake.allergy_groups["almonds"] == [Allergen.TREE_NUTS]
    assert set(intake.allergy_groups["nuts"]) == {Allergen.TREE_NUTS, Allergen.PEANUTS}


def test_allergies_outside_the_eu_list_are_kept_as_words():
    llm = FakeLLM(SafetyAnswer(allergies=[said("kiwi")]))
    intake = interpret_safety_answer(llm, "kiwi")
    assert intake.profile.allergens == []
    assert intake.profile.other_allergies == ["kiwi"]
    assert "kiwi" in confirmation_message(intake)


def test_health_conditions_become_confirmed_restrictions_only():
    llm = FakeLLM(SafetyAnswer(diets=[Diet.VEGETARIAN], health_diets=[Diet.LOW_SUGAR]))
    intake = interpret_safety_answer(llm, "vegetarian, and I have type 2 diabetes")
    assert intake.profile.diets == [Diet.VEGETARIAN, Diet.LOW_SUGAR]
    assert intake.profile.health_diets == [Diet.LOW_SUGAR]
    assert "diabetes" not in intake.model_dump_json()  # the condition is never kept
    message = confirmation_message(intake)
    assert "low sugar" in message and "not medical advice" in message


def test_a_disliked_allergen_word_becomes_an_allergy():
    # "dairy free please" read as a dislike: a dislike only drops recipes naming "dairy",
    # so alfredo sauce would pass; the alias table makes it milk
    llm = FakeLLM(SafetyAnswer(dislikes=["Dairy", "mushrooms", "eggs"]))
    intake = interpret_safety_answer(llm, "dairy free please, no mushrooms or eggs")
    assert intake.profile.allergens == [Allergen.EGGS, Allergen.MILK]
    assert intake.allergy_groups == {"dairy": [Allergen.MILK], "eggs": [Allergen.EGGS]}
    assert intake.profile.dislikes == ["mushrooms"]
    assert "dairy (milk)" in confirmation_message(intake)  # read back for confirmation


def test_an_allergy_and_a_dislike_of_the_same_word_are_one_allergy():
    llm = FakeLLM(SafetyAnswer(allergies=[said("dairy")], dislikes=["dairy"]))
    intake = interpret_safety_answer(llm, "dairy")
    assert intake.allergy_groups == {"dairy": [Allergen.MILK]}
    assert intake.profile.dislikes == []


def test_a_health_reason_for_gluten_free_adds_the_gluten_allergen():
    llm = FakeLLM(SafetyAnswer(health_diets=[Diet.GLUTEN_FREE]))
    intake = interpret_safety_answer(llm, "I have coeliac disease")
    assert intake.profile.allergens == [Allergen.GLUTEN]
    assert intake.profile.health_diets == [Diet.GLUTEN_FREE]
    assert intake.allergy_groups == {"gluten": [Allergen.GLUTEN]}
    assert "coeliac" not in intake.model_dump_json()  # the condition is never kept


def test_gluten_is_not_added_twice_or_for_a_chosen_diet():
    llm = FakeLLM(SafetyAnswer(allergies=[said("wheat")], health_diets=[Diet.GLUTEN_FREE]))
    intake = interpret_safety_answer(llm, "wheat allergy and coeliac")
    assert intake.allergy_groups == {"wheat": [Allergen.GLUTEN]}
    chosen = interpret_safety_answer(FakeLLM(SafetyAnswer(diets=[Diet.GLUTEN_FREE])), "gf")
    assert chosen.profile.allergens == []  # a chosen diet stays a diet


def test_uncovered_health_condition_is_flagged_without_keeping_it():
    llm = FakeLLM(SafetyAnswer(health_not_covered=True))
    message = confirmation_message(interpret_safety_answer(llm, "I have gout"))
    assert "gout" not in message and "cannot adjust" in message


def test_nothing_to_avoid():
    intake = interpret_safety_answer(FakeLLM(SafetyAnswer()), "no allergies")
    assert intake.profile == UserProfile()
    assert "no allergies" in confirmation_message(intake).lower()


def test_safety_prompt_is_sensitive_and_lists_the_supported_diets():
    llm = FakeLLM(SafetyAnswer())
    interpret_safety_answer(llm, "I am allergic to sesame")
    prompt, variables = llm.calls[0]
    assert prompt.sensitive
    assert variables["answer"] == "I am allergic to sesame"
    text = prompt.render(**variables)
    for diet in ("vegetarian", "vegan", "gluten_free", "low_sugar", "low_salt"):
        assert diet in text


# --- request parsing and query building ------------------------------------------------


def test_request_becomes_a_query_with_the_profile_restrictions():
    llm = FakeLLM(
        RequestAnswer(pantry=["eggs", "milk", "toast"], wish="something sweet for breakfast")
    )
    request = interpret_request(llm, "I have eggs, milk, toast. Something sweet for breakfast?")
    profile = UserProfile(
        allergens=[Allergen.PEANUTS],
        diets=[Diet.LOW_SUGAR],
        dislikes=["mushrooms"],
        other_allergies=["kiwi"],
    )
    query = build_query(profile, request)
    assert query.ingredients == ["eggs", "milk", "toast"]
    assert query.preferences_text == "something sweet for breakfast"
    assert query.required_allergen_free == [Allergen.PEANUTS]
    assert query.diets == [Diet.LOW_SUGAR]
    assert query.exclude_ingredients == ["mushrooms"]
    assert query.other_allergies == ["kiwi"]  # word match, not an exact exclusion


def test_allergies_mentioned_in_a_request_only_make_the_query_stricter():
    llm = FakeLLM(
        RequestAnswer(
            pantry=["rice"],
            max_minutes=20,
            avoid=["cilantro"],
            allergies=[said("sesame"), said("lychee")],
        )
    )
    request = interpret_request(llm, "rice, 20 minutes, no cilantro, I'm allergic to sesame")
    query = build_query(UserProfile(allergens=[Allergen.PEANUTS]), request)
    assert query.required_allergen_free == [Allergen.PEANUTS, Allergen.SESAME]
    assert query.max_minutes == 20
    assert query.exclude_ingredients == ["cilantro"]
    assert query.other_allergies == ["lychee"]


def test_nutrition_goals_from_the_request_reach_the_query():
    from pantry_chef.models.query import NutritionGoal

    llm = FakeLLM(RequestAnswer(pantry=["steak"], wish="dinner", goals=["high_protein"]))
    query = build_query(UserProfile(), interpret_request(llm, "steak, high protein dinner"))
    assert query.nutrition_goals == [NutritionGoal.HIGH_PROTEIN]
    assert query.preferences_text == "dinner"
    assert build_query(UserProfile(), RequestAnswer(pantry=["egg"])).nutrition_goals == []


def test_request_prompt_is_sensitive():
    llm = FakeLLM(RequestAnswer())
    interpret_request(llm, "eggs")
    assert llm.calls[0][0].sensitive
