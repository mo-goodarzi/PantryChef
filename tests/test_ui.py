"""The Streamlit UI end to end: AppTest -> typed client -> API -> graph -> fixture DB."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pantry_chef.agents.safety import AllergyMention, SafetyAnswer
from pantry_chef.api.client import ApiError
from pantry_chef.api.schemas import TurnOut, TurnType
from pantry_chef.models.chat import AmountReply, AmountUnit, Question, QuestionKind
from pantry_chef.models.query import AmountStatus

from .test_api import default_llm, make_client  # noqa: F401  (fixture)

UI = Path(__file__).parents[1] / "ui" / "streamlit_app.py"


@pytest.fixture
def open_ui(make_client):  # noqa: F811
    def start(llm=None, video=None, client=None):
        app = AppTest.from_file(str(UI), default_timeout=30)
        app.session_state["client"] = client or make_client(llm or default_llm(), video=video)
        app.run()
        return app

    return start


def chat_text(app) -> str:
    return "\n".join(m.markdown[0].value for m in app.chat_message if m.markdown)


def ask(app, text):
    app.chat_input(key="request").set_value(text).run()


def through_safety(app, answer="none", consent=False):
    ask(app, "I have flour, butter, eggs and milk")
    app.text_area(key="safety_text").input(answer)
    app.button(key="safety_send").click().run()
    app.radio(key="confirm_correct").set_value("Yes")
    if consent:
        app.checkbox(key="confirm_consent").check()
    app.button(key="confirm_send").click().run()


def test_greets_and_waits_for_a_request(open_ui):
    app = open_ui()
    assert not app.exception
    assert "Tell me what you have at home" in chat_text(app)


def test_safety_questions_then_recipe_cards_then_the_recipe(open_ui):
    app = open_ui()
    through_safety(app)
    assert not app.exception
    text = chat_text(app)
    assert "food allergies" in text and "No allergies" in text
    assert "recipe(s) you can make" in text

    app.button(key="choose_1").click().run()
    assert "Why it fits" in chat_text(app)
    assert app.chat_input(key="request")  # ready for the next request

    app.button(key="back").click().run()
    assert not app.exception
    assert "Back to the recipes." in chat_text(app)
    assert app.button(key="choose_2")  # the recipe cards are back


def test_cards_and_the_recipe_show_photos_when_there_are_any(open_ui, enriched_conn):
    enriched_conn.execute(
        "UPDATE recipes SET image_url = 'https://img.sndimg.com/food/image/upload/"
        "w_555,h_416,c_fit/v1/img/recipes/p.jpg'"
    )
    enriched_conn.commit()
    app = open_ui()
    through_safety(app)
    cards = len(app.get("image"))
    assert cards >= 2  # one photo per option card

    app.button(key="choose_1").click().run()
    assert not app.exception and "Photo: Food.com" in str(app.get("image")[-1].proto)


def test_no_photo_means_no_image(open_ui):
    app = open_ui()
    through_safety(app)
    assert not app.get("image")


def test_the_video_question_then_the_video_with_the_recipe(open_ui):
    from .test_graph import a_video

    app = open_ui(video=a_video)
    through_safety(app)
    app.button(key="choose_1").click().run()
    assert app.button(key="video_yes") and app.button(key="video_no")

    app.button(key="video_yes").click().run()
    assert not app.exception
    assert "Why it fits" in chat_text(app)
    assert any("follow the list above" in c.value for c in app.caption)


def test_an_unconfirmed_video_is_shown_with_a_warning(open_ui):
    from pantry_chef.models.video import VideoOutcome

    from .test_graph import a_video

    def top_result(recipe, steps):
        top = a_video(recipe, steps).video.model_copy(update={"verified": False})
        return VideoOutcome(fallback=top, search_url="https://www.youtube.com/results?q=x")

    app = open_ui(video=top_result)
    through_safety(app)
    app.button(key="choose_1").click().run()
    app.button(key="video_yes").click().run()
    assert not app.exception
    assert any("No video was confirmed" in w.value for w in app.warning)


def test_show_me_other_recipes(open_ui):
    app = open_ui()
    through_safety(app)
    app.button(key="choose_more").click().run()
    assert not app.exception
    assert "Show me more recipes." in chat_text(app)
    assert app.button(key="choose_1")  # the earlier recipes stay on the page


def test_profile_in_the_sidebar_and_delete_my_data(open_ui):
    llm = default_llm(safety_intake=SafetyAnswer(allergies=[AllergyMention(said="peanuts")]))
    app = open_ui(llm)
    through_safety(app, "peanuts", consent=True)
    sidebar = "\n".join(m.value for m in app.sidebar.markdown)
    assert "Allergies: peanuts" in sidebar and "Saved for new conversations here" in sidebar

    app.button(key="delete_data").click().run()
    assert "Your saved profile was deleted." in [i.value for i in app.info]
    assert not app.session_state["history"]


def test_a_saved_profile_belongs_to_one_browser_session_not_to_a_name(
    open_ui,
    make_client,  # noqa: F811
):
    """No name box: another visitor can never load (or delete) someone's allergies."""
    llm = default_llm(safety_intake=SafetyAnswer(allergies=[AllergyMention(said="peanuts")]))
    client = make_client(llm)
    first = open_ui(client=client)
    assert not first.sidebar.text_input  # nothing to type a name into
    through_safety(first, "peanuts", consent=True)

    first.button(key="new_conversation").click().run()
    ask(first, "I have flour, butter, eggs and milk")
    assert "recipe(s) you can make" in chat_text(first)  # remembered: no safety question

    second = open_ui(client=client)
    assert second.session_state["user_id"] != first.session_state["user_id"]
    ask(second, "I have flour, butter, eggs and milk")
    assert "food allergies" in chat_text(second)  # asked again, nothing loaded
    second.button(key="delete_data").click().run()
    assert "Nothing was saved." in [i.value for i in second.info]


class QuantityClient:
    """A fake API that asks the quantity question and records the answer."""

    def __init__(self):
        self.replies = []

    def reply(self, session_id, answer):
        self.replies.append(answer)
        return TurnOut(session_id=session_id, type=TurnType.MESSAGE, message="ok")

    def session(self, session_id):
        raise ApiError(404, "unknown session")


def quantity_ui():
    client = QuantityClient()
    app = AppTest.from_file(str(UI), default_timeout=30)
    app.session_state["client"] = client
    app.session_state["session_id"] = "s1"
    app.session_state["history"] = [("user", "I have eggs and chicken")]
    question = Question(
        kind=QuestionKind.QUANTITIES,
        text="How much do you have of: egg, chicken breast?",
        items=["egg", "chicken breast"],
        units={"egg": AmountUnit.COUNT, "chicken breast": AmountUnit.GRAMS},
    )
    app.session_state["turn"] = TurnOut(session_id="s1", type=TurnType.QUESTION, question=question)
    app.run()
    return app, client


def test_the_quantity_form_asks_grams_or_a_number_per_item():
    app, _ = quantity_ui()
    assert not app.exception
    labels = {n.key: n.label for n in app.number_input}
    assert labels == {"qty_value_egg": "how many", "qty_value_chicken breast": "grams"}
    assert not app.text_input  # no free-text unit


def test_the_quantity_form_untouched_means_dont_know():
    """Not "I have 0", which would rule out every recipe with eggs."""
    app, client = quantity_ui()
    app.button(key="qty_send").click().run()
    [reply] = client.replies
    for amount in reply.amounts.values():
        assert amount.status is AmountStatus.UNKNOWN and amount.quantity is None


def test_the_quantity_form_sends_grams_and_counts():
    app, client = quantity_ui()
    app.number_input(key="qty_value_egg").set_value(3)
    app.number_input(key="qty_value_chicken breast").set_value(400)
    app.button(key="qty_send").click().run()
    [reply] = client.replies
    assert reply.amounts["egg"] == AmountReply(quantity=3, unit=None, status=AmountStatus.KNOWN)
    assert reply.amounts["chicken breast"] == AmountReply(
        quantity=400, unit="g", status=AmountStatus.KNOWN
    )


def test_an_unreachable_api_is_shown_not_crashed():
    from pantry_chef.api.client import PantryChefClient

    app = AppTest.from_file(str(UI), default_timeout=30)
    app.session_state["client"] = PantryChefClient("http://127.0.0.1:9")
    app.run()
    ask(app, "I have eggs")
    assert not app.exception
    assert "cannot reach the PantryChef API" in chat_text(app)
