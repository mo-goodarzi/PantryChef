"""The Streamlit UI end to end: AppTest -> typed client -> API -> graph -> fixture DB."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from pantry_chef.agents.safety import AllergyMention, SafetyAnswer

from .test_api import default_llm, make_client  # noqa: F401  (fixture)

UI = Path(__file__).parents[1] / "ui" / "streamlit_app.py"


@pytest.fixture
def open_ui(make_client):  # noqa: F811
    def start(llm=None, name=""):
        app = AppTest.from_file(str(UI), default_timeout=30)
        app.session_state["client"] = make_client(llm or default_llm())
        app.run()
        if name:
            app.text_input(key="user_name").input(name).run()
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


def test_show_me_other_recipes(open_ui):
    app = open_ui()
    through_safety(app)
    app.button(key="choose_more").click().run()
    assert not app.exception
    assert "Show me other recipes." in chat_text(app)


def test_profile_in_the_sidebar_and_delete_my_data(open_ui):
    llm = default_llm(safety_intake=SafetyAnswer(allergies=[AllergyMention(said="peanuts")]))
    app = open_ui(llm, name="alice")
    through_safety(app, "peanuts", consent=True)
    sidebar = "\n".join(m.value for m in app.sidebar.markdown)
    assert "Allergies: peanuts" in sidebar and "Saved for next time" in sidebar

    app.button(key="delete_data").click().run()
    assert "Your saved profile was deleted." in [i.value for i in app.info]
    assert not app.session_state["history"]


def test_an_unreachable_api_is_shown_not_crashed():
    from pantry_chef.api.client import PantryChefClient

    app = AppTest.from_file(str(UI), default_timeout=30)
    app.session_state["client"] = PantryChefClient("http://127.0.0.1:9")
    app.run()
    ask(app, "I have eggs")
    assert not app.exception
    assert "cannot reach the PantryChef API" in chat_text(app)
