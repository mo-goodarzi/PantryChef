"""PantryChef web UI: a chat that renders the conversation's questions as forms.

It only talks to the API (PANTRY_CHEF_API_URL, default http://localhost:8000):
    uv run uvicorn pantry_chef.api.main:app        # terminal 1
    uv run streamlit run ui/streamlit_app.py       # terminal 2
"""

import contextlib
import os

import streamlit as st

from pantry_chef.api.client import ApiError, PantryChefClient
from pantry_chef.api.schemas import TurnOut, TurnType
from pantry_chef.models.chat import (
    AmountReply,
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
from pantry_chef.models.query import AmountStatus

API_URL = os.environ.get("PANTRY_CHEF_API_URL", "http://localhost:8000")
AMOUNT_CHOICES = {"I have": AmountStatus.KNOWN, "Don't know": AmountStatus.UNKNOWN,
                  "Plenty": AmountStatus.PLENTY}  # fmt: skip


# --- text for the chat ---------------------------------------------------------------


def option_markdown(option: RecipeOption) -> str:
    lines = [f"**{option.number}. {option.name}** · {option.minutes} min"]
    if option.why:
        lines.append(f"_{option.why}_")
    if option.uses:
        lines.append(f"Uses: {', '.join(option.uses)}")
    lines += [f":warning: **Allergy:** {warning}" for warning in option.warnings]
    if option.adaptations:
        lines.append(f"Adapt: {'; '.join(option.adaptations)}")
    if option.also_needs:
        lines.append(f"Also needs: {', '.join(option.also_needs)}")
    return "  \n".join(lines)


def answer_markdown(answer: FinalAnswer) -> str:
    parts = [
        f"### {answer.name} · {answer.minutes} min",
        f"**Why it fits:** {answer.why_it_fits}",
        "**Ingredients:** " + ", ".join(answer.ingredients),
    ]
    parts += [f":warning: **Allergy:** {warning}" for warning in answer.warnings]
    if answer.adaptations:
        parts.append("**Adapt:** " + "; ".join(answer.adaptations))
    if answer.also_needs:
        parts.append("**You also need:** " + ", ".join(answer.also_needs))
    parts.append("\n".join(f"{n}. {step}" for n, step in enumerate(answer.steps, start=1)))
    parts += [f"_{note}_" for note in answer.notes]
    if answer.disclaimer:
        parts.append(f"⚠️ {answer.disclaimer}")
    return "\n\n".join(parts)


def turn_markdown(turn: TurnOut) -> str:
    if turn.type is TurnType.FINAL and turn.answer is not None:
        return answer_markdown(turn.answer)
    if turn.type is TurnType.CANDIDATES and turn.question is not None:
        note = f"\n\n_{turn.question.note}_" if turn.question.note else ""
        return f"I found {len(turn.question.options)} recipe(s) you can make.{note}"
    if turn.question is not None:
        return turn.question.text
    return turn.message or ""


def profile_markdown(profile: UserProfile) -> str:
    lines = []
    if profile.allergens:
        lines.append(
            "Allergies: " + ", ".join(a.value.replace("_", " ") for a in profile.allergens)
        )
    if profile.other_allergies:
        lines.append("Also avoiding: " + ", ".join(profile.other_allergies))
    if profile.diets:
        lines.append("Diets: " + ", ".join(d.value.replace("_", " ") for d in profile.diets))
    if profile.dislikes:
        lines.append("Leaving out: " + ", ".join(profile.dislikes))
    lines.append("Saved for next time" if profile.consent_to_store else "Not saved")
    return "  \n".join(lines)


# --- state and API calls ---------------------------------------------------------------


def init_state() -> None:
    ss = st.session_state
    if "client" not in ss:
        ss.client = PantryChefClient(API_URL)
    ss.setdefault("session_id", None)
    ss.setdefault("history", [])  # (role, markdown)
    ss.setdefault("turn", None)  # the latest TurnOut


def client() -> PantryChefClient:
    return st.session_state.client


def record(turn: TurnOut) -> None:
    st.session_state.turn = turn
    st.session_state.history.append(("assistant", turn_markdown(turn)))


def send(text: str) -> None:
    ss = st.session_state
    ss.history.append(("user", text))
    try:
        if ss.session_id is None:
            ss.session_id = client().new_session()
        record(client().send(ss.session_id, text, user_id=ss.get("user_name") or None))
    except ApiError as error:
        ss.history.append(("assistant", f"Sorry, that did not work: {error.message}"))


def answer(reply, shown: str) -> None:
    ss = st.session_state
    ss.history.append(("user", shown))
    try:
        record(client().reply(ss.session_id, reply))
    except ApiError as error:
        ss.history.pop()
        st.session_state.error = error.message


def reset_conversation(forget: bool = False) -> None:
    ss = st.session_state
    if ss.session_id is not None:
        with contextlib.suppress(ApiError):  # already gone
            client().close(ss.session_id, forget=forget)
    ss.session_id, ss.turn, ss.history = None, None, []


# --- forms for the pending question -----------------------------------------------------


def safety_form(question: Question) -> None:
    with st.form("safety"):
        text = st.text_area(
            "Allergies, diets, health", key="safety_text", placeholder="e.g. peanuts; vegetarian"
        )
        if st.form_submit_button("Send", key="safety_send"):
            answer(SafetyReply(text=text or "none"), text or "none")
            st.rerun()


def confirm_form(question: Question) -> None:
    with st.form("confirm"):
        correct = st.radio("Is this right?", ["Yes", "No"], key="confirm_correct", horizontal=True)
        consent = st.checkbox("Remember this for next time", key="confirm_consent")
        if st.form_submit_button("Continue", key="confirm_send"):
            ok = correct == "Yes"
            reply = ConfirmReply(correct=ok, consent_to_store=ok and consent)
            shown = "Yes" + (", remember it" if reply.consent_to_store else "") if ok else "No"
            answer(reply, shown)
            st.rerun()


def quantity_form(question: Question) -> None:
    with st.form("quantities"):
        amounts = {}
        for item in question.items:
            left, middle, right = st.columns([2, 1, 1])
            status = left.radio(
                item, list(AMOUNT_CHOICES), key=f"qty_status_{item}", horizontal=True
            )
            quantity = middle.number_input("Amount", min_value=0.0, key=f"qty_value_{item}")
            unit = right.text_input("Unit (empty = count)", key=f"qty_unit_{item}")
            known = AMOUNT_CHOICES[status] is AmountStatus.KNOWN
            amounts[item] = AmountReply(
                quantity=quantity if known else None,
                unit=(unit.strip() or None) if known else None,
                status=AMOUNT_CHOICES[status],
            )
        if st.form_submit_button("Continue", key="qty_send"):
            answer(QuantityReply(amounts=amounts), "Here is what I have.")
            st.rerun()


def choice_cards(question: Question) -> None:
    for option in question.options:
        with st.container(border=True):
            st.markdown(option_markdown(option))
            if st.button("Cook this", key=f"choose_{option.number}"):
                answer(ChoiceReply(choice=option.number), f"I'll make {option.name}.")
                st.rerun()
    if st.button("Show me other recipes", key="choose_more"):
        answer(ChoiceReply(more=True), "Show me other recipes.")
        st.rerun()


FORMS = {
    QuestionKind.SAFETY: safety_form,
    QuestionKind.SAFETY_CONFIRM: confirm_form,
    QuestionKind.QUANTITIES: quantity_form,
    QuestionKind.CHOICE: choice_cards,
}


# --- page ------------------------------------------------------------------------------


def sidebar() -> None:
    ss = st.session_state
    with st.sidebar:
        st.header("PantryChef")
        st.text_input("Your name (to remember your profile)", key="user_name")
        profile = None
        if ss.session_id is not None:
            try:
                profile = client().session(ss.session_id).profile
            except ApiError:
                profile = None
        if profile is not None:
            st.subheader("Your profile")
            st.markdown(profile_markdown(profile))
        if st.button("New conversation", key="new_conversation"):
            reset_conversation()
            st.rerun()
        if st.button("Delete my data", key="delete_data"):
            name = ss.get("user_name")
            deleted = client().delete_profile(name) if name else False
            reset_conversation(forget=True)
            ss.notice = "Your saved profile was deleted." if deleted else "Nothing was saved."
            st.rerun()
        st.caption(
            "Not medical advice. Allergens are checked by code twice, but always check labels."
        )


def main() -> None:
    st.set_page_config(page_title="PantryChef", page_icon="🍳")
    init_state()
    sidebar()
    ss = st.session_state
    if notice := ss.pop("notice", None):
        st.info(notice)

    if not ss.history:
        st.chat_message("assistant").markdown(
            "Tell me what you have at home and what you feel like, "
            "e.g. *I have eggs, milk and toast. Something sweet for breakfast?*"
        )
    for role, text in ss.history:
        st.chat_message(role).markdown(text)
    if error := ss.pop("error", None):
        st.error(error)

    turn = ss.turn
    if turn is not None and turn.question is not None:
        FORMS[turn.question.kind](turn.question)
    elif text := st.chat_input("What do you have at home?", key="request"):
        send(text)
        st.rerun()


main()
