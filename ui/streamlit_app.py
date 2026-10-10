"""PantryChef web UI: a chat that renders the conversation's questions as forms.

It only talks to the API (PANTRY_CHEF_API_URL, default http://localhost:8000):
    uv run uvicorn pantry_chef.api.main:app        # terminal 1
    uv run streamlit run ui/streamlit_app.py       # terminal 2

The look (colours, fonts, light/dark) lives in .streamlit/config.toml; this file only
uses native Streamlit elements, badges and Material icons, so no custom CSS is needed.
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
    VideoReply,
)
from pantry_chef.models.profile import UserProfile
from pantry_chef.models.query import AmountStatus

API_URL = os.environ.get("PANTRY_CHEF_API_URL", "http://localhost:8000")
AMOUNT_CHOICES = {"I have": AmountStatus.KNOWN, "Don't know": AmountStatus.UNKNOWN,
                  "Plenty": AmountStatus.PLENTY}  # fmt: skip
AVATARS = {"assistant": ":material/skillet:", "user": ":material/person:"}
PHOTO_CREDIT = "Photo: Food.com"
EXAMPLES = [
    "I have eggs, milk and toast. Something sweet for breakfast?",
    "Chicken, rice and an onion. Quick dinner under 30 minutes.",
    "Pasta, tomatoes and garlic, vegetarian please.",
]
# The steps of one request, shown as a progress row above the chat.
STEPS = ["Pantry", "Safety", "Amounts", "Choose", "Cook"]
STEP_OF_KIND = {
    QuestionKind.SAFETY: 1,
    QuestionKind.SAFETY_CONFIRM: 1,
    QuestionKind.QUANTITIES: 2,
    QuestionKind.CHOICE: 3,
    QuestionKind.VIDEO: 4,
}


# --- text for the chat ---------------------------------------------------------------


def badges(items: list[str], color: str) -> str:
    """Markdown badges; square brackets would end the badge early, so swap them out."""
    clean = (item.replace("[", "(").replace("]", ")") for item in items)
    return " ".join(f":{color}-badge[{item}]" for item in clean)


def title(name: str) -> str:
    """Food.com names are lower case ("french toast"); capitalise the first letter."""
    return name[:1].upper() + name[1:]


def minutes_badge(minutes: int) -> str:
    return f":gray-badge[:material/schedule: {minutes} min]"


def answer_markdown(answer: FinalAnswer) -> str:
    """The headline of the final recipe; the rest is laid out by show_answer."""
    return (
        f"### {title(answer.name)}\n\n{minutes_badge(answer.minutes)}\n\n"
        f"**Why it fits:** {answer.why_it_fits}"
    )


def turn_markdown(turn: TurnOut) -> str:
    if turn.type is TurnType.CANDIDATES and turn.question is not None:
        note = f"\n\n_{turn.question.note}_" if turn.question.note else ""
        return f"I found {len(turn.question.options)} recipe(s) you can make.{note}"
    if turn.question is not None:
        return turn.question.text
    return turn.message or ""


def profile_markdown(profile: UserProfile) -> str:
    lines = []
    if profile.allergens:
        names = ", ".join(a.value.replace("_", " ") for a in profile.allergens)
        lines.append(f":material/no_food: Allergies: {names}")
    if profile.other_allergies:
        lines.append(":material/block: Also avoiding: " + ", ".join(profile.other_allergies))
    if profile.diets:
        names = ", ".join(d.value.replace("_", " ") for d in profile.diets)
        lines.append(f":material/eco: Diets: {names}")
    if profile.dislikes:
        lines.append(":material/thumb_down: Leaving out: " + ", ".join(profile.dislikes))
    if profile.consent_to_store:
        lines.append(":material/bookmark_added: Saved for next time")
    else:
        lines.append(":material/visibility_off: Not saved")
    return "  \n".join(lines)


# --- state and API calls ---------------------------------------------------------------


def init_state() -> None:
    ss = st.session_state
    if "client" not in ss:
        ss.client = PantryChefClient(API_URL)
    ss.setdefault("session_id", None)
    ss.setdefault("history", [])  # (role, markdown or FinalAnswer)
    ss.setdefault("turn", None)  # the latest TurnOut


def client() -> PantryChefClient:
    return st.session_state.client


def record(turn: TurnOut) -> None:
    ss = st.session_state
    ss.turn = turn
    if turn.type is TurnType.FINAL and turn.answer is not None:
        ss.history.append(("assistant", turn.answer))
    else:
        ss.history.append(("assistant", turn_markdown(turn)))


def send(text: str) -> None:
    ss = st.session_state
    ss.history.append(("user", text))
    try:
        with st.spinner("Looking through your pantry…"):
            if ss.session_id is None:
                ss.session_id = client().new_session()
            record(client().send(ss.session_id, text, user_id=ss.get("user_name") or None))
    except ApiError as error:
        ss.history.append(("assistant", f"Sorry, that did not work: {error.message}"))


def answer(reply, shown: str) -> None:
    ss = st.session_state
    ss.history.append(("user", shown))
    try:
        with st.spinner("Checking recipes…"):
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
    with st.form("safety", border=True):
        text = st.text_area(
            "Allergies, diets or health conditions",
            key="safety_text",
            placeholder="e.g. peanuts and shellfish; vegetarian",
            help="Leave empty if there is nothing to avoid.",
        )
        st.caption(
            ":material/lock: Used only to filter recipes. "
            "Nothing is stored unless you agree in the next step."
        )
        if st.form_submit_button("Send", key="safety_send", type="primary"):
            answer(SafetyReply(text=text or "none"), text or "none")
            st.rerun()


def confirm_form(question: Question) -> None:
    with st.form("confirm", border=True):
        correct = st.radio("Is this right?", ["Yes", "No"], key="confirm_correct", horizontal=True)
        consent = st.checkbox(
            "Remember this for next time",
            key="confirm_consent",
            help="Saved under your name in the sidebar. You can delete it at any time.",
        )
        if st.form_submit_button("Continue", key="confirm_send", type="primary"):
            ok = correct == "Yes"
            reply = ConfirmReply(correct=ok, consent_to_store=ok and consent)
            shown = "Yes" + (", remember it" if reply.consent_to_store else "") if ok else "No"
            answer(reply, shown)
            st.rerun()


def quantity_form(question: Question) -> None:
    with st.form("quantities", border=True):
        st.caption("Only the amounts that change which recipes work.")
        amounts = {}
        for item in question.items:
            st.markdown(f"**{item}**")
            status_col, amount_col, unit_col = st.columns([2, 1, 1], vertical_alignment="bottom")
            status = status_col.radio(
                item,
                list(AMOUNT_CHOICES),
                key=f"qty_status_{item}",
                horizontal=True,
                label_visibility="collapsed",
            )
            quantity = amount_col.number_input(
                "Amount", min_value=0.0, key=f"qty_value_{item}", label_visibility="collapsed"
            )
            unit = unit_col.text_input(
                "Unit",
                key=f"qty_unit_{item}",
                placeholder="unit, e.g. g",
                help="Leave empty for a count, e.g. 2 eggs.",
                label_visibility="collapsed",
            )
            known = AMOUNT_CHOICES[status] is AmountStatus.KNOWN
            amounts[item] = AmountReply(
                quantity=quantity if known else None,
                unit=(unit.strip() or None) if known else None,
                status=AMOUNT_CHOICES[status],
            )
        st.caption("Amount and unit are used only when you pick **I have**.")
        if st.form_submit_button("Continue", key="qty_send", type="primary"):
            answer(QuantityReply(amounts=amounts), "Here is what I have.")
            st.rerun()


def option_card(option: RecipeOption) -> None:
    with st.container(border=True):
        if option.image_url:  # linked from Food.com, never copied
            st.image(option.image_url, caption=PHOTO_CREDIT, width="stretch")
        st.markdown(f"**{option.number}. {title(option.name)}**  \n{minutes_badge(option.minutes)}")
        if option.why:
            st.caption(option.why)
        for warning in option.warnings:
            st.warning(f"**Allergy:** {warning}", icon=":material/warning:")
        if option.fit_note:
            st.caption(f":material/info: {option.fit_note}")
        if option.uses:
            st.markdown(":gray[:small[Uses]]  \n" + badges(option.uses, "green"))
        if option.also_needs:
            st.markdown(":gray[:small[Also needs]]  \n" + badges(option.also_needs, "orange"))
        if option.adaptations:
            st.caption(":material/swap_horiz: " + "; ".join(option.adaptations))
        if st.button(
            "Cook this",
            key=f"choose_{option.number}",
            type="primary",
            icon=":material/restaurant:",
            width="stretch",
        ):
            answer(ChoiceReply(choice=option.number), f"I'll make {option.name}.")
            st.rerun()


def choice_cards(question: Question) -> None:
    # One row of two cards at a time, so on a phone they stack in order 1, 2, 3.
    for start in range(0, len(question.options), 2):
        for column, option in zip(st.columns(2), question.options[start : start + 2], strict=False):
            with column:
                option_card(option)
    if st.button("Show me other recipes", key="choose_more", icon=":material/refresh:"):
        answer(ChoiceReply(more=True), "Show me other recipes.")
        st.rerun()


def video_form(question: Question) -> None:
    yes, no = st.columns(2)
    if yes.button(
        "Yes, find a video", key="video_yes", type="primary", icon=":material/smart_display:"
    ):
        answer(VideoReply(want=True), "Yes, find a video.")
        st.rerun()
    if no.button("No, just the recipe", key="video_no"):
        answer(VideoReply(want=False), "No video, thanks.")
        st.rerun()


FORMS = {
    QuestionKind.SAFETY: safety_form,
    QuestionKind.SAFETY_CONFIRM: confirm_form,
    QuestionKind.QUANTITIES: quantity_form,
    QuestionKind.CHOICE: choice_cards,
    QuestionKind.VIDEO: video_form,
}


# --- chat messages ---------------------------------------------------------------------


def show_answer(answer: FinalAnswer) -> None:
    if answer.image_url:
        st.image(answer.image_url, caption=PHOTO_CREDIT, width="content")
    st.markdown(answer_markdown(answer))
    for warning in answer.warnings:
        st.warning(f"**Allergy:** {warning}", icon=":material/warning:")
    ingredients, extras = st.columns([3, 2])
    with ingredients:
        st.markdown("#### Ingredients")
        if answer.servings:
            st.caption(f"Serves {answer.servings}")
        st.markdown("\n".join(f"- {item}" for item in answer.ingredients))
    with extras:
        if answer.also_needs:
            st.markdown("#### You also need")
            st.markdown(badges(answer.also_needs, "orange"))
        if answer.adaptations:
            st.markdown("#### Adapt")
            st.markdown("\n".join(f"- {item}" for item in answer.adaptations))
    st.markdown("#### Steps")
    st.markdown("\n".join(f"{n}. {step}" for n, step in enumerate(answer.steps, start=1)))
    show_video(answer)
    for note in answer.notes:
        st.caption(f":material/info: {note}")
    if answer.disclaimer:
        st.caption(f":material/medical_information: {answer.disclaimer}")


def show_video(answer: FinalAnswer) -> None:
    if answer.video:
        st.markdown("#### Video")
        st.video(answer.video.url)
        st.caption(
            f"{answer.video.title} ({answer.video.channel}). Checked against the recipe: "
            f"{answer.video.match_evidence} The video may use other ingredients; follow the "
            "list above for your allergies."
        )
    elif answer.video_search_url:
        st.caption(
            ":material/smart_display: No video clearly matched this recipe. "
            f"[Search YouTube]({answer.video_search_url})"
        )


def show_message(role: str, content: str | FinalAnswer) -> None:
    with st.chat_message(role, avatar=AVATARS[role]):
        if isinstance(content, FinalAnswer):
            show_answer(content)
        else:
            st.markdown(content)


def current_step(turn: TurnOut | None) -> int:
    if turn is None:
        return 0
    if turn.type is TurnType.FINAL:
        return 4
    if turn.question is not None:
        return STEP_OF_KIND[turn.question.kind]
    return 0


def progress_row(step: int) -> None:
    """Pantry → Safety → … with the done steps green and the current one highlighted."""
    parts = []
    for index, name in enumerate(STEPS):
        if index < step:
            parts.append(f":green-badge[:material/check: {name}]")
        elif index == step:
            parts.append(f":orange-badge[**{name}**]")
        else:
            parts.append(f":gray-badge[{name}]")
    st.markdown(" ".join(parts))


def welcome() -> str | None:
    """The empty-chat hero. Returns an example request when the user clicks one."""
    st.title("What's in your kitchen?")
    st.markdown(
        "Tell me what you have and what you feel like. I'll find recipes you can make, "
        "with allergens checked in code, twice."
    )
    return st.pills("Try one", EXAMPLES, key="example", label_visibility="collapsed")


# --- page ------------------------------------------------------------------------------


def sidebar() -> None:
    ss = st.session_state
    with st.sidebar:
        st.header(":material/skillet: PantryChef", anchor=False)
        st.caption("Recipes from what you already have.")
        st.text_input(
            "Your name",
            key="user_name",
            placeholder="e.g. alice",
            help="Only needed to remember your allergies and diets between visits.",
        )
        profile = None
        if ss.session_id is not None:
            try:
                profile = client().session(ss.session_id).profile
            except ApiError:
                profile = None
        if profile is not None:
            with st.container(border=True):
                st.markdown("**Your profile**")
                st.markdown(profile_markdown(profile))
        if st.button(
            "New conversation", key="new_conversation", icon=":material/add:", width="stretch"
        ):
            reset_conversation()
            st.rerun()
        if st.button(
            "Delete my data", key="delete_data", icon=":material/delete:", width="stretch"
        ):
            name = ss.get("user_name")
            deleted = client().delete_profile(name) if name else False
            reset_conversation(forget=True)
            ss.notice = "Your saved profile was deleted." if deleted else "Nothing was saved."
            st.rerun()
        st.divider()
        st.caption(
            ":material/health_and_safety: Not medical advice. Allergens are checked by code "
            "twice, but always check labels."
        )


def main() -> None:
    st.set_page_config(page_title="PantryChef", page_icon=":material/skillet:")
    init_state()
    sidebar()
    ss = st.session_state
    if notice := ss.pop("notice", None):
        st.info(notice, icon=":material/check_circle:")

    example = None
    if not ss.history:
        example = welcome()
        show_message(
            "assistant",
            "Tell me what you have at home and what you feel like, "
            "e.g. *I have eggs, milk and toast. Something sweet for breakfast?*",
        )
    else:
        progress_row(current_step(ss.turn))
    for role, content in ss.history:
        show_message(role, content)
    if error := ss.pop("error", None):
        st.error(error, icon=":material/error:")

    turn = ss.turn
    if turn is not None and turn.question is not None:
        FORMS[turn.question.kind](turn.question)
    elif text := st.chat_input("What do you have at home?", key="request") or example:
        send(text)
        st.rerun()


main()
