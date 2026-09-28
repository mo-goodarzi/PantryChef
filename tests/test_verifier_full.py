"""Phase 5a verifier: matching, quantities, hidden allergens, substitutions, decision."""

import json
import random

import pytest

from pantry_chef.agents.feedback import build_feedback
from pantry_chef.agents.hidden_allergens import (
    HiddenAllergenBatch,
    HiddenAllergenChecker,
    HiddenAllergenItem,
    is_compound,
)
from pantry_chef.agents.verifier import (
    VerificationContext,
    Verifier,
    check_hidden_allergens,
    check_ingredients,
    check_quantities,
    check_time,
    suggest_substitutions,
    verify,
)
from pantry_chef.db.repository import IngredientOption
from pantry_chef.ingredients.allergens import Allergen
from pantry_chef.ingredients.quantities import Amount, available_ratio
from pantry_chef.models.matching import MatchLabel, MatchResult
from pantry_chef.models.query import AmountStatus, Diet, PantryItem, RecipeQuery
from pantry_chef.models.recipe import Candidate, RecipeIngredient
from pantry_chef.models.verification import FailureCode, VerificationStatus


def ing(
    name,
    canonical=None,
    key=True,
    staple=False,
    qty_matters=False,
    quantity=None,
    unit=None,
    allergens=(),
    category=None,
    meat=False,
    fish=False,
    animal=False,
):
    return RecipeIngredient(
        name=name,
        canonical_name=canonical or name,
        category=category,
        is_key=key,
        is_staple=staple,
        quantity_matters=qty_matters,
        quantity=quantity,
        unit=unit,
        allergens=list(allergens),
        contains_meat=meat,
        contains_fish=fish,
        animal_product=animal,
    )


def cand(*ingredients, minutes=20, recipe_id=1):
    return Candidate(
        recipe_id=recipe_id,
        name="t",
        minutes=minutes,
        ingredients=list(ingredients),
        have_key=0,
        total_key=0,
        coverage=0,
        ingredient_score=0,
        final_score=0,
    )


def match(recipe_term, user_term, label):
    return MatchResult(
        recipe_term=recipe_term, user_term=user_term, label=MatchLabel(label), source="test"
    )


def item(canonical, quantity=None, unit=None, status=AmountStatus.KNOWN):
    return PantryItem(
        name=canonical, canonical_name=canonical, quantity=quantity, unit=unit, amount_status=status
    )


# --- ingredients with the matcher ----------------------------------------------------


def test_toast_covers_bread_as_a_substitute_so_the_recipe_adapts():
    context = VerificationContext(
        pantry={"toast"}, matches={"bread": match("bread", "toast", "substitute")}
    )
    result = check_ingredients(cand(ing("bread")), context.pantry, context)
    assert result.passed
    assert result.adaptations == ["use your toast instead of bread"]


def test_eggs_cover_egg_whites_but_egg_whites_do_not_cover_eggs():
    have_eggs = VerificationContext(
        pantry={"egg"}, matches={"egg white": match("egg white", "egg", "contains")}
    )
    assert check_ingredients(cand(ing("egg whites", "egg white")), set(), have_eggs).passed

    have_whites = VerificationContext(
        pantry={"egg white"}, matches={"egg": match("egg", None, "different")}
    )
    result = check_ingredients(cand(ing("eggs", "egg")), set(), have_whites)
    assert [str(r) for r in result.reasons] == ["missing_ingredient: egg"]


# --- quantities ----------------------------------------------------------------------


def eggs_recipe(n):
    return cand(ing("eggs", "egg", qty_matters=True, quantity=n))


@pytest.mark.parametrize(
    ("have", "need", "passed", "adaptation"),
    [
        (4, 4, True, None),
        (6, 4, True, None),
        (2, 4, True, "make 50% of the recipe (limited by eggs)"),
        (1, 4, False, None),
    ],
)
def test_two_eggs_versus_a_recipe_with_four(have, need, passed, adaptation):
    context = VerificationContext(pantry={"egg"}, pantry_items={"egg": item("egg", have)})
    result = check_quantities(eggs_recipe(need), context)
    assert result.passed is passed
    assert result.adaptations == ([adaptation] if adaptation else [])
    if not passed:
        assert result.reasons[0].code is FailureCode.INSUFFICIENT_QUANTITY


@pytest.mark.parametrize("status", [AmountStatus.UNKNOWN, AmountStatus.PLENTY])
def test_unknown_or_plenty_is_fine(status):
    pantry_items = {"egg": item("egg", 1, None, status)}
    context = VerificationContext(pantry={"egg"}, pantry_items=pantry_items)
    assert check_quantities(eggs_recipe(12), context).passed


def test_a_pinch_is_tiny():
    saffron = cand(ing("saffron", qty_matters=True, quantity=1, unit="pinch"))
    context = VerificationContext(
        pantry={"saffron"}, pantry_items={"saffron": item("saffron", 0.25, "tsp")}
    )
    assert check_quantities(saffron, context).passed  # 1/4 tsp = 4 pinches
    assert available_ratio(Amount(1, "tsp"), Amount(1, "pinch")) == pytest.approx(16)


def test_units_are_converted():
    milk = cand(ing("milk", qty_matters=True, quantity=2, unit="cups"))
    context = VerificationContext(pantry={"milk"}, pantry_items={"milk": item("milk", 250, "ml")})
    result = check_quantities(milk, context)  # 250 ml ~ 53% of 2 cups
    assert result.passed and result.adaptations


def test_weight_versus_volume_is_not_guessed():
    flour = cand(ing("flour", qty_matters=True, quantity=2, unit="cups"))
    context = VerificationContext(pantry={"flour"}, pantry_items={"flour": item("flour", 100, "g")})
    result = check_quantities(flour, context)
    assert result.passed and "cannot compare" in result.notes[0]


def test_recipe_amount_unknown_is_a_note_not_a_failure():
    context = VerificationContext(pantry={"egg"}, pantry_items={"egg": item("egg", 1)})
    result = check_quantities(eggs_recipe(None), context)
    assert result.passed and result.notes == ["recipe amount of eggs unknown"]


# --- hidden allergens ----------------------------------------------------------------


def test_pesto_with_a_nut_allergy_fails_on_hidden_allergens():
    pesto = cand(ing("pesto sauce", key=False, category="condiment"))
    context = VerificationContext(
        pantry=set(), hidden={"pesto sauce": {Allergen.TREE_NUTS, Allergen.MILK}}
    )
    result = check_hidden_allergens(pesto, {Allergen.TREE_NUTS}, context)
    assert [str(r) for r in result.reasons] == ["hidden_allergen: pesto sauce"]


def test_hidden_check_only_reports_allergens_not_already_labeled():
    pesto = cand(ing("pesto sauce", key=False, allergens=[Allergen.TREE_NUTS]))
    context = VerificationContext(pantry=set(), hidden={"pesto sauce": {Allergen.TREE_NUTS}})
    assert check_hidden_allergens(pesto, {Allergen.TREE_NUTS}, context).passed


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [
        ("pesto sauce", None, True),
        ("chicken stock", None, True),
        ("ketchup", "condiment", True),
        ("non-dairy powdered coffee creamer", None, True),
        ("eggs", "protein", False),
        ("salt", "spice", False),
    ],
)
def test_is_compound(name, category, expected):
    staple = name == "salt"
    assert is_compound(ing(name, category=category, staple=staple)) is expected


class FakeHiddenLLM:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, schema, **variables):
        assert prompt.name == "hidden_allergens" and schema is HiddenAllergenBatch
        names = json.loads(variables["ingredients"])
        self.calls.append(names)
        return HiddenAllergenBatch(
            items=[
                HiddenAllergenItem(
                    ingredient=n, allergens=[Allergen.GLUTEN] if "pickle" in n else []
                )
                for n in names
                if n != "skipped"
            ]
        )


def test_hidden_checker_caches_per_ingredient(tmp_path):
    llm = FakeHiddenLLM()
    checker = HiddenAllergenChecker(llm, tmp_path / "hidden.json")
    assert checker.check(["branston pickle", "ketchup"]) == {
        "branston pickle": {Allergen.GLUTEN},
        "ketchup": set(),
    }
    checker2 = HiddenAllergenChecker(llm, tmp_path / "hidden.json")
    checker2.check(["ketchup", "skipped"])
    checker2.check(["skipped"])
    assert llm.calls == [["branston pickle", "ketchup"], ["skipped"], ["skipped"]]


# --- time and substitutions ----------------------------------------------------------


def test_time_limit():
    assert check_time(cand(minutes=30), 30).passed
    [reason] = check_time(cand(minutes=45), 30).reasons
    assert str(reason) == "too_long: 45"


def sub(name, allergens=(), meat=False, animal=False):
    return IngredientOption(
        name=name,
        note="1:1",
        allergens=list(allergens),
        contains_meat=meat,
        contains_fish=False,
        animal_product=animal,
    )


def test_substitute_the_user_has_is_suggested():
    recipe = cand(ing("margarine", key=False))
    context = VerificationContext(pantry={"butter"}, substitutes={"margarine": [sub("butter")]})
    result = suggest_substitutions(recipe, RecipeQuery(ingredients=["butter"]), context)
    assert result.adaptations == ["use your butter instead of margarine (1:1)"]


def test_substitute_with_a_user_allergen_is_not_suggested():
    recipe = cand(ing("margarine", key=False))
    context = VerificationContext(
        pantry={"butter"}, substitutes={"margarine": [sub("butter", [Allergen.MILK])]}
    )
    query = RecipeQuery(ingredients=["butter"], required_allergen_free=[Allergen.MILK])
    result = suggest_substitutions(recipe, query, context)
    assert result.adaptations == [] and result.notes == ["also needs margarine"]


def test_substitute_that_breaks_the_diet_is_not_suggested():
    recipe = cand(ing("vegetable broth", key=False))
    context = VerificationContext(
        pantry={"chicken broth"},
        substitutes={"vegetable broth": [sub("chicken broth", meat=True, animal=True)]},
    )
    query = RecipeQuery(ingredients=["chicken broth"], diets=[Diet.VEGETARIAN])
    assert suggest_substitutions(recipe, query, context).adaptations == []


# --- decision ------------------------------------------------------------------------


def test_decision_pass_adapt_fail():
    query = RecipeQuery(ingredients=["egg"])
    ok = verify(cand(ing("eggs", "egg")), query)
    assert ok.status is VerificationStatus.PASS
    assert ok.ingredient_status == {"eggs": "available"}

    adapt_ctx = VerificationContext(pantry={"egg"}, pantry_items={"egg": item("egg", 2)})
    adapted = verify(eggs_recipe(4), query, adapt_ctx)
    assert adapted.status is VerificationStatus.ADAPT
    assert adapted.adaptations == ["make 50% of the recipe (limited by eggs)"]

    failed = verify(cand(ing("eggs", "egg"), ing("flour")), query)
    assert failed.status is VerificationStatus.FAIL


def test_verifier_never_passes_or_adapts_a_recipe_with_a_user_allergen():
    """Property-style, now including hidden allergens and adaptations."""
    rng = random.Random(11)
    allergens = list(Allergen)
    for _ in range(500):
        ingredients = [
            ing(
                f"item {i}",
                allergens=rng.sample(allergens, rng.randint(0, 2)),
                key=rng.random() < 0.5,
                qty_matters=True,
                quantity=4,
            )
            for i in range(rng.randint(1, 6))
        ]
        hidden = {i.name: set(rng.sample(allergens, rng.randint(0, 1))) for i in ingredients}
        user = set(rng.sample(allergens, rng.randint(1, 3)))
        pantry = {i.canonical_name for i in ingredients}
        context = VerificationContext(
            pantry=pantry,
            hidden=hidden,
            pantry_items={p: item(p, rng.choice([2, 4, 8])) for p in pantry},
        )
        query = RecipeQuery(ingredients=sorted(pantry), required_allergen_free=sorted(user))
        result = verify(cand(*ingredients), query, context)
        present = {a for i in ingredients for a in i.allergens} | set().union(*hidden.values())
        if present & user:
            assert result.status is VerificationStatus.FAIL


# --- Verifier (batched preparation) --------------------------------------------------


class CountingMatcher:
    def __init__(self):
        self.calls = []

    def match(self, user_terms, recipe_terms):
        self.calls.append((user_terms, recipe_terms))
        return [
            match(r, r if r in user_terms else None, "same" if r in user_terms else "different")
            for r in recipe_terms
        ]


def test_verifier_prepares_everything_in_one_matcher_call(enriched_conn, tmp_path):
    from pantry_chef.search.engine import search

    query = RecipeQuery(ingredients=["flour", "butter", "eggs", "milk"])
    candidates = search(enriched_conn, query, limit=10).candidates
    matcher = CountingMatcher()
    results = Verifier(enriched_conn, matcher).verify_all(candidates, query)
    assert len(matcher.calls) == 1
    assert len(results) == len(candidates)
    assert results[0].status is VerificationStatus.PASS


def test_verifier_only_asks_about_hidden_allergens_when_the_user_has_allergies(
    enriched_conn, tmp_path
):
    from pantry_chef.search.engine import search

    llm = FakeHiddenLLM()
    verifier = Verifier(enriched_conn, None, HiddenAllergenChecker(llm, tmp_path / "h.json"))
    query = RecipeQuery(ingredients=["tuna", "onion", "lettuce"])
    candidates = search(enriched_conn, query, limit=10).candidates
    verifier.verify_all(candidates, query)
    assert llm.calls == []
    verifier.verify_all(
        candidates, query.model_copy(update={"required_allergen_free": [Allergen.GLUTEN]})
    )
    assert llm.calls and "salad dressing" in llm.calls[0]


# --- feedback ------------------------------------------------------------------------


def test_feedback_excludes_failed_recipes_and_problem_ingredients():
    query = RecipeQuery(ingredients=["egg"], required_allergen_free=[Allergen.TREE_NUTS])
    results = [
        verify(cand(ing("flour"), recipe_id=1), query),
        verify(
            cand(
                ing("flour"), ing("walnuts", "walnut", allergens=[Allergen.TREE_NUTS]), recipe_id=2
            ),
            query,
        ),
        verify(cand(ing("eggs", "egg"), recipe_id=3), query),
    ]
    feedback = build_feedback(query, results)
    assert feedback.query.exclude_recipe_ids == [1, 2]
    assert feedback.query.exclude_ingredients == ["flour", "walnut"]  # flour missing twice
    # flour twice, walnut once (not in the pantry either)
    assert feedback.reason_counts == {"missing_ingredient": 3, "allergen": 1}
    assert feedback.query.required_allergen_free == [Allergen.TREE_NUTS]


# --- pantry items standing in for another ingredient ---------------------------------


def test_milk_standing_in_for_oat_milk_fails_for_a_milk_allergy():
    recipe = cand(ing("oat milk"))
    context = VerificationContext(
        pantry={"milk"},
        matches={"oat milk": match("oat milk", "milk", "substitute")},
        pantry_facts={"milk": sub("milk", [Allergen.MILK], animal=True)},
    )
    query = RecipeQuery(ingredients=["milk"], required_allergen_free=[Allergen.MILK])
    result = verify(recipe, query, context)
    assert result.status is VerificationStatus.FAIL
    assert "your 'milk' (for oat milk) contains milk" in result.reasons[0].detail


def test_chicken_standing_in_for_tofu_breaks_vegetarian():
    recipe = cand(ing("tofu"))
    context = VerificationContext(
        pantry={"chicken"},
        matches={"tofu": match("tofu", "chicken", "substitute")},
        pantry_facts={"chicken": sub("chicken", meat=True, animal=True)},
    )
    query = RecipeQuery(ingredients=["chicken"], diets=[Diet.VEGETARIAN])
    assert verify(recipe, query, context).reasons[0].code is FailureCode.DIET_VIOLATION


class FixedMatcher:
    """Returns the given matches; everything else is exact-or-different."""

    def __init__(self, *matches):
        self.matches = {m.recipe_term: m for m in matches}

    def match(self, user_terms, recipe_terms):
        return [
            self.matches.get(r)
            or match(r, r if r in user_terms else None, "same" if r in user_terms else "different")
            for r in recipe_terms
        ]


def verify_with_stand_in(conn, recipe_ingredient, pantry_item, **query_fields):
    matcher = FixedMatcher(match(recipe_ingredient.canonical_name, pantry_item, "substitute"))
    query = RecipeQuery(ingredients=[pantry_item], **query_fields)
    [result] = Verifier(conn, matcher).verify_all([cand(recipe_ingredient)], query)
    return result


def test_pantry_item_missing_from_the_database_is_checked_with_the_name_rules(enriched_conn):
    # "homemade cashew milk" is not in the database; the rules still see the cashew.
    result = verify_with_stand_in(
        enriched_conn,
        ing("milk", allergens=[Allergen.MILK]),
        "homemade cashew milk",
        required_allergen_free=[Allergen.TREE_NUTS],
    )
    assert result.status is VerificationStatus.FAIL
    assert [str(r) for r in result.reasons] == ["allergen: homemade cashew milk"]
    assert "contains tree_nuts" in result.reasons[0].detail


def test_unknown_pantry_item_fails_closed_when_the_user_has_allergies(enriched_conn):
    # No rule fires on "barista drink", but it could be dairy: it cannot stand in.
    result = verify_with_stand_in(
        enriched_conn, ing("oat milk"), "barista drink", required_allergen_free=[Allergen.MILK]
    )
    assert result.status is VerificationStatus.FAIL
    assert result.reasons[0].code is FailureCode.ALLERGEN
    assert "cannot be checked" in result.reasons[0].detail


def test_unknown_pantry_item_fails_closed_for_a_diet(enriched_conn):
    result = verify_with_stand_in(
        enriched_conn, ing("tofu"), "mystery protein", diets=[Diet.VEGETARIAN]
    )
    assert result.status is VerificationStatus.FAIL
    assert result.reasons[0].code is FailureCode.DIET_VIOLATION


def test_unknown_pantry_item_is_fine_without_restrictions(enriched_conn):
    result = verify_with_stand_in(enriched_conn, ing("oat milk"), "barista drink")
    assert result.status is VerificationStatus.ADAPT
    assert result.adaptations == ["use your barista drink instead of oat milk"]


def test_pantry_facts_combine_database_and_rules(enriched_conn):
    from pantry_chef.agents.verifier import load_pantry_facts

    facts = load_pantry_facts(enriched_conn, {"salad dressing", "homemade cashew milk"})
    assert facts["salad dressing"].known
    assert facts["salad dressing"].allergens == [Allergen.EGGS, Allergen.MILK]
    assert not facts["homemade cashew milk"].known
    assert facts["homemade cashew milk"].allergens == [Allergen.TREE_NUTS]


def test_same_ingredient_is_not_rechecked_twice():
    recipe = cand(ing("milk", allergens=[Allergen.MILK]))
    context = VerificationContext(
        pantry={"milk"}, pantry_facts={"milk": sub("milk", [Allergen.MILK])}
    )
    query = RecipeQuery(ingredients=["milk"], required_allergen_free=[Allergen.MILK])
    reasons = verify(recipe, query, context).reasons
    assert [str(r) for r in reasons] == ["allergen: milk"]  # only the recipe's own milk


def test_load_canonical_facts_merges_raw_names(enriched_conn):
    from pantry_chef.db.repository import load_canonical_facts

    facts = load_canonical_facts(enriched_conn, ["egg", "chicken breast", "nothing"])
    assert facts["egg"].allergens == [Allergen.EGGS]
    assert facts["chicken breast"].contains_meat
    assert "nothing" not in facts


def test_load_canonical_facts_includes_allergens_of_contained_ingredients(enriched_conn):
    from pantry_chef.db.repository import load_canonical_facts

    # Fixture seed: salad dressing contains swiss cheese (milk); its own label is eggs.
    facts = load_canonical_facts(enriched_conn, ["salad dressing"])
    assert facts["salad dressing"].allergens == [Allergen.EGGS, Allergen.MILK]


def test_substitutes_include_allergens_of_contained_ingredients(enriched_conn):
    from pantry_chef.db.repository import load_substitutes

    ids = {r["name"]: r["id"] for r in enriched_conn.execute("SELECT id, name FROM ingredients")}
    enriched_conn.execute(
        "INSERT INTO ingredient_relation (a_id, b_id, relation) VALUES (?, ?, 'substitute')",
        (ids["butter"], ids["salad dressing"]),
    )
    [option] = load_substitutes(enriched_conn, ["butter"])["butter"]
    assert option.name == "salad dressing"
    assert option.allergens == [Allergen.EGGS, Allergen.MILK]
