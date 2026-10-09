"""Graph nodes and routing.

Each node reads the ChatState and returns the fields it changes. Nodes that ask the user
something call interrupt() FIRST and do their work after it: on resume LangGraph re-runs
the node from the top, so any work before the interrupt would run twice. Routing
functions are pure functions of the state.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END
from langgraph.types import interrupt

from pantry_chef.agents.feedback import build_feedback
from pantry_chef.agents.finder import build_query, interpret_request
from pantry_chef.agents.safety import DISCLAIMER, confirmation_message, interpret_safety_answer
from pantry_chef.db.state import ProfileStore
from pantry_chef.graph.state import MAX_ATTEMPTS, ChatState
from pantry_chef.llm.factory import StructuredLLM
from pantry_chef.models.chat import (
    ChoiceReply,
    ConfirmReply,
    FinalAnswer,
    QuantityReply,
    Question,
    QuestionKind,
    RecipeOption,
    SafetyReply,
)
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import AmountStatus, PantryItem, RecipeQuery
from pantry_chef.models.recipe import Candidate
from pantry_chef.models.verification import (
    VerificationResult,
    VerificationStatus,
    VerifiedCandidate,
)
from pantry_chef.observability import get_logger, score, span
from pantry_chef.search.engine import FindResult

log = get_logger("graph")

SAFETY_QUESTION = (
    "Before we start: do you have any food allergies or intolerances, diets you follow "
    "(e.g. vegetarian), or health conditions I should consider when choosing recipes?"
)
NEED_PANTRY = "Tell me what you have at home, e.g. 'I have eggs, milk and bread'."
MAX_QUANTITY_ITEMS = 5  # one short question, not a form
REASON_WORDS = {
    "missing_ingredient": "needed ingredients you don't have",
    "insufficient_quantity": "needed more than you have",
    "allergen": "contained one of your allergens",
    "hidden_allergen": "may contain one of your allergens",
    "diet_violation": "did not fit your diet",
    "too_long": "took too long",
    "preference_mismatch": "did not match what you asked for",
}


@dataclass
class ChatDeps:
    """Everything the nodes need; tests pass fakes, chat_from_settings the real ones."""

    llm: StructuredLLM
    find: Callable[[RecipeQuery], FindResult]  # search + verify (+ rerank)
    reverify: Callable[[list[Candidate], RecipeQuery, list[PantryItem]], list[VerificationResult]]
    steps: Callable[[int], list[str]]
    profiles: ProfileStore | None = None
    ask_quantities: bool = False  # off until recipes have amounts (Phase 8)
    # The last allergen check before options are shown. False only for the eval's
    # safety-layer comparisons; never off in the app.
    final_allergen_check: bool = True


def ask(question: Question) -> dict:
    """Pause the graph with a question; returns the user's answer as a dict."""
    return interrupt(question.model_dump(mode="json"))


# --- helpers -------------------------------------------------------------------------


def allergen_violations(results: list[VerifiedCandidate], query: RecipeQuery) -> list[int]:
    """Recipe ids among the results that contain a user allergen (must always be empty:
    a last code check on what is about to be shown)."""
    allergens = set(query.required_allergen_free)
    return [
        vc.candidate.recipe_id
        for vc in results
        if any(set(i.allergens) & allergens for i in vc.candidate.ingredients)
    ]


def ingredients_with_status(vc: VerifiedCandidate, statuses: set[str]) -> list[str]:
    status = vc.verification.ingredient_status
    return [i.name for i in vc.candidate.ingredients if status.get(i.name) in statuses]


def recipe_option(number: int, vc: VerifiedCandidate) -> RecipeOption:
    return RecipeOption(
        number=number,
        recipe_id=vc.candidate.recipe_id,
        name=vc.candidate.name,
        minutes=vc.candidate.minutes,
        why=vc.candidate.rerank_reason,
        uses=ingredients_with_status(vc, {"available", "substitute"}),
        adaptations=vc.verification.adaptations,
        also_needs=ingredients_with_status(vc, {"missing", "extra"}),
        warnings=vc.verification.warnings,
        fit_note=vc.verification.fit_note,
    )


def quantity_items(state: ChatState) -> list[str]:
    """Key ingredients whose amount matters, that the user has, not asked about before."""
    items: dict[str, None] = {}
    for vc in state.results:
        status = vc.verification.ingredient_status
        for i in vc.candidate.ingredients:
            asked = i.canonical_name in state.asked_quantities
            relevant = i.is_key and i.quantity_matters and not i.is_staple
            if relevant and not asked and status.get(i.name) == "available":
                items[i.canonical_name] = None
    return list(items)[:MAX_QUANTITY_ITEMS]


def merge_amounts(pantry: list[PantryItem], reply: QuantityReply) -> list[PantryItem]:
    by_name = {item.canonical_name: item for item in pantry}
    for name, amount in reply.amounts.items():
        status = amount.status
        if status is AmountStatus.KNOWN and amount.quantity is None:
            status = AmountStatus.UNKNOWN  # "known" without a number means we don't know
        known = status is AmountStatus.KNOWN
        by_name[name] = PantryItem(
            name=name,
            canonical_name=name,
            quantity=amount.quantity if known else None,
            unit=amount.unit if known else None,
            amount_status=status,
        )
    return list(by_name.values())


def no_recipe_message(reason_counts: dict[str, int]) -> str:
    text = "I couldn't find a recipe that passes all checks for you."
    common = [REASON_WORDS.get(code, code) for code, _ in Counter(reason_counts).most_common(2)]
    if common:
        text += f" The closest ones {' or '.join(common)}."
    return text + " Try adding a few more ingredients."


def health_disclaimer(profile: UserProfile | None, query: RecipeQuery | None) -> str | None:
    if profile and query and set(profile.health_diets) & set(query.diets):
        return DISCLAIMER
    return None


# --- nodes ---------------------------------------------------------------------------


class ChatNodes:
    def __init__(self, deps: ChatDeps):
        self.deps = deps

    def load_profile(self, state: ChatState) -> dict:
        if state.profile is not None or self.deps.profiles is None or not state.user_id:
            return {}
        with span("node.load_profile"):
            profile = self.deps.profiles.load(state.user_id)
        return {"profile": profile} if profile else {}

    def safety_question(self, state: ChatState) -> dict:
        reply = SafetyReply.model_validate(
            ask(Question(kind=QuestionKind.SAFETY, text=SAFETY_QUESTION))
        )
        with span("node.safety_question"):
            return {"proposed": interpret_safety_answer(self.deps.llm, reply.text)}

    def safety_confirm(self, state: ChatState) -> dict:
        if state.proposed is None:  # nothing to confirm: ask again
            return {}
        text = (
            f"{confirmation_message(state.proposed)}\n"
            "Is this right? And may I remember it for next time?"
        )
        reply = ConfirmReply.model_validate(
            ask(Question(kind=QuestionKind.SAFETY_CONFIRM, text=text))
        )
        with span("node.safety_confirm", correct=reply.correct, consent=reply.consent_to_store):
            if not reply.correct:
                return {"proposed": None}
            profile = state.proposed.profile.model_copy(
                update={"consent_to_store": reply.consent_to_store}
            )
            if self.deps.profiles is not None and state.user_id:
                self.deps.profiles.save(state.user_id, profile)  # no-op without consent
            return {"profile": profile, "proposed": None}

    def parse_request(self, state: ChatState) -> dict:
        with span("node.parse_request"):
            request = interpret_request(self.deps.llm, state.message)
            reset = {
                "request": request,
                "attempts": 0,
                "retry": False,
                "results": [],
                "reason_counts": {},
                "pantry_items": [],
                "asked_quantities": [],
                "questions_asked": 0,
                "shown_ids": [],
                "chosen": None,
                "answer": None,
                "reply": None,
            }
            if not request.pantry:
                return {**reset, "query": None, "reply": NEED_PANTRY}
            profile = state.profile or UserProfile()
            return {**reset, "query": build_query(profile, request)}

    def search(self, state: ChatState) -> dict:
        assert state.query is not None
        attempt = state.attempts + 1
        with span("node.search", attempt=attempt):
            result = self.deps.find(state.query)
            unsafe = (
                allergen_violations(result.top, state.query)
                if self.deps.final_allergen_check
                else []
            )
            if unsafe:  # never expected: the filters and the verifier both check allergens
                log.error("graph.allergen_violation", recipe_ids=unsafe)
            approved = [vc for vc in result.top if vc.candidate.recipe_id not in unsafe]
            checked = len(result.checked)
            score("allergen_violation", len(unsafe))
            score("verifier_pass_rate", len(result.approved) / checked if checked else 0.0)
            reasons = Counter(r.code.value for v in result.verifications for r in v.reasons)
            update: dict = {
                "attempts": attempt,
                "results": approved,
                # Over the whole request, so "nothing found" can still say why.
                "reason_counts": dict(Counter(state.reason_counts) + reasons),
                "retry": False,
            }
            if len(approved) < 2 and attempt < MAX_ATTEMPTS:
                feedback = build_feedback(state.query, result.verifications)
                if feedback.query != state.query:  # a retry only helps if the query changed
                    update |= {"query": feedback.query, "retry": True}
            if not update["retry"]:
                score("retries", attempt - 1)
            log.info(
                "graph.search",
                attempt=attempt,
                candidates=checked,
                approved=len(approved),
                reasons=dict(reasons),
            )
            return update

    def quantity_check(self, state: ChatState) -> dict:
        items = quantity_items(state)
        if not items:
            return {}
        text = "How much do you have of: " + ", ".join(items) + "?"
        reply = QuantityReply.model_validate(
            ask(Question(kind=QuestionKind.QUANTITIES, text=text, items=items))
        )
        assert state.query is not None
        with span("node.quantity_check", items=len(items)):
            pantry_items = merge_amounts(state.pantry_items, reply)
            candidates = [vc.candidate for vc in state.results]
            verifications = self.deps.reverify(candidates, state.query, pantry_items)
            results = [
                VerifiedCandidate(candidate=c, verification=v)
                for c, v in zip(candidates, verifications, strict=True)
                if v.status is not VerificationStatus.FAIL
            ]
            score("questions_asked", state.questions_asked + len(items))
            return {
                "pantry_items": pantry_items,
                "asked_quantities": state.asked_quantities + items,
                "questions_asked": state.questions_asked + len(items),
                "results": results,
            }

    def present(self, state: ChatState) -> dict:
        if not state.results:
            return {"reply": no_recipe_message(state.reason_counts)}
        options = [recipe_option(n, vc) for n, vc in enumerate(state.results, start=1)]
        note = None
        if len(options) < 2:
            note = "Only this recipe passed all checks; I searched a few times."
        reply = ChoiceReply.model_validate(
            ask(
                Question(
                    kind=QuestionKind.CHOICE,
                    text="Which one would you like to make?",
                    options=options,
                    note=note,
                )
            )
        )
        assert state.query is not None
        shown = state.shown_ids + [o.recipe_id for o in options]
        with span("node.present", options=len(options), more=reply.more):
            if reply.more or reply.choice is None:
                score("user_accepted_recipe", 0)
                query = state.query.model_copy(
                    update={
                        "exclude_recipe_ids": sorted(
                            set(state.query.exclude_recipe_ids) | set(shown)
                        )
                    }
                )
                return {"query": query, "attempts": 0, "results": [], "shown_ids": shown}
            if not 1 <= reply.choice <= len(options):
                raise ValueError(f"choice must be 1..{len(options)}, got {reply.choice}")
            score("user_accepted_recipe", 1)
            return {"chosen": state.results[reply.choice - 1], "shown_ids": shown}

    def respond(self, state: ChatState) -> dict:
        assert state.chosen is not None
        vc = state.chosen
        with span("node.respond", recipe_id=vc.candidate.recipe_id):
            uses = ingredients_with_status(vc, {"available", "substitute"})
            why = vc.candidate.rerank_reason or (
                f"Uses your {', '.join(uses)}; ready in {vc.candidate.minutes} minutes."
                if uses
                else f"Ready in {vc.candidate.minutes} minutes."
            )
            notes = []
            if state.profile and state.profile.other_allergies:
                notes.append(
                    f"I left out recipes listing {', '.join(state.profile.other_allergies)}, "
                    "but please check the ingredients yourself."
                )
            answer = FinalAnswer(
                recipe_id=vc.candidate.recipe_id,
                name=vc.candidate.name,
                minutes=vc.candidate.minutes,
                why_it_fits=why,
                ingredients=[i.name for i in vc.candidate.ingredients],
                steps=self.deps.steps(vc.candidate.recipe_id),
                adaptations=vc.verification.adaptations,
                also_needs=ingredients_with_status(vc, {"missing", "extra"}),
                warnings=vc.verification.warnings,
                notes=notes,
                disclaimer=health_disclaimer(state.profile, state.query),
            )
            return {"answer": answer}


# --- routing -------------------------------------------------------------------------


def after_load_profile(state: ChatState) -> str:
    return "parse_request" if state.profile is not None else "safety_question"


def after_safety_confirm(state: ChatState) -> str:
    return "parse_request" if state.profile is not None else "safety_question"


def after_parse_request(state: ChatState) -> str:
    return "search" if state.query is not None else END


def after_search(state: ChatState, ask_quantities: bool) -> str:
    if state.retry:
        return "search"
    return "quantity_check" if ask_quantities else "present"


def after_present(state: ChatState) -> str:
    if state.reply is not None:
        return END
    return "respond" if state.chosen is not None else "search"
