"""The terminal chat: answer parsing and a scripted conversation on the fixture."""

import pytest

from pantry_chef.graph.cli import parse_amount, parse_choice, parse_servings, parse_yes_no, run
from pantry_chef.models.chat import AmountUnit
from pantry_chef.models.query import AmountStatus

from .test_graph import BAKING, ScriptedLLM, conversation, make_deps


@pytest.mark.parametrize(
    ("text", "quantity", "unit", "status"),
    [
        ("2", 2.0, None, AmountStatus.KNOWN),
        ("200 g", 200.0, "g", AmountStatus.KNOWN),
        ("1.5 cups", 1.5, "cups", AmountStatus.KNOWN),
        ("don't know", None, None, AmountStatus.UNKNOWN),
        ("", None, None, AmountStatus.UNKNOWN),
        ("Plenty", None, None, AmountStatus.PLENTY),
    ],
)
def test_parse_amount(text, quantity, unit, status):
    amount = parse_amount(text)
    assert (amount.quantity, amount.unit, amount.status) == (quantity, unit, status)


def test_a_bare_number_takes_the_asked_unit():
    assert parse_amount("400", AmountUnit.GRAMS).unit == "g"
    assert parse_amount("2", AmountUnit.COUNT).unit is None
    assert parse_amount("1 lb", AmountUnit.GRAMS).unit == "lb"  # a unit typed wins


def test_parse_amount_rejects_words():
    with pytest.raises(ValueError, match="number"):
        parse_amount("a few")


def test_parse_yes_no_and_choice():
    assert parse_yes_no(" Yes ") and not parse_yes_no("no")
    with pytest.raises(ValueError):
        parse_yes_no("maybe")
    assert parse_choice("2").choice == 2
    assert parse_choice("more").more
    with pytest.raises(ValueError):
        parse_choice("the second one")


def test_a_whole_terminal_conversation(enriched_conn, state_conn):
    from pantry_chef.agents.safety import SafetyAnswer

    llm = ScriptedLLM(safety_intake=SafetyAnswer(), request_parsing=BAKING)
    chat = conversation(make_deps(enriched_conn, state_conn, llm))
    inputs = iter(
        [
            "I have flour, butter, eggs and milk",  # request
            "no allergies",  # safety question
            "yes",  # correct?
            "no",  # remember? (no consent)
            "9",  # not an option: asked again
            "1",  # choose the first recipe
            "back",  # the recipes again
            "2",  # choose another one
            "quit",
        ]
    )
    output: list[str] = []
    run(chat, lambda prompt: next(inputs), output.append)

    text = "\n".join(output)
    assert "food allergies" in text  # the safety question
    assert "No allergies" in text  # the confirmation
    assert "choose a number from 1 to" in text  # the bad choice was rejected
    assert "Steps:" in text and "Why it fits:" in text  # the final answer
    assert text.count("Which one would you like to make?") == 2  # first, then after "back"
    assert text.count("Steps:") == 2


def test_chat_refuses_to_start_without_the_embeddings(enriched_conn, tmp_path):
    from pathlib import Path

    from pantry_chef.config import Settings
    from pantry_chef.graph.app import chat_from_settings

    recipes = Path(enriched_conn.execute("PRAGMA database_list").fetchone()["file"])
    settings = Settings(
        db_path=recipes, chroma_path=tmp_path / "missing", state_db_path=tmp_path / "s.db"
    )
    with pytest.raises(FileNotFoundError, match="build_embeddings"):
        chat_from_settings(settings)


def test_answer_lists_one_ingredient_line_per_row_with_servings():
    from pantry_chef.graph.cli import render_answer
    from pantry_chef.models.chat import FinalAnswer

    answer = FinalAnswer(
        recipe_id=38,
        name="Berry Blue Frozen Dessert",
        minutes=30,
        why_it_fits="Uses your blueberries.",
        ingredients=["4 cups blueberries, fresh or frozen", "1/4 cup granulated sugar"],
        servings=4,
        steps=["toss the berries with sugar"],
    )
    text = render_answer(answer)

    assert "Ingredients (serves 4):\n - 4 cups blueberries, fresh or frozen\n" in text
    assert " - 1/4 cup granulated sugar" in text
    assert "(serves" not in render_answer(answer.model_copy(update={"servings": None}))
    assert "Photo" not in text
    with_photo = answer.model_copy(update={"image_url": "https://img.sndimg.com/p.jpg"})
    assert "Photo (Food.com): https://img.sndimg.com/p.jpg" in render_answer(with_photo)


def test_video_question_and_answer_lines():
    from pantry_chef.graph.cli import read_answer, render_answer
    from pantry_chef.models.chat import FinalAnswer, Question, QuestionKind, VideoReply
    from pantry_chef.models.video import TextSource, VideoResult

    question = Question(kind=QuestionKind.VIDEO, text="Would you like a video?")
    assert read_answer(question, lambda prompt: "yes") == VideoReply(want=True)
    assert read_answer(question, lambda prompt: "no") == VideoReply(want=False)

    answer = FinalAnswer(
        recipe_id=1, name="pancakes", minutes=20, why_it_fits=".", ingredients=[], steps=[]
    )
    video = VideoResult(
        video_id="v1",
        url="https://www.youtube.com/watch?v=v1",
        title="Fluffy pancakes",
        channel="Chef",
        match_score=1.0,
        match_evidence="e",
        text_source=TextSource.TRANSCRIPT,
        verified=True,
    )
    shown = render_answer(answer.model_copy(update={"video": video}))
    assert (
        "Video (checked against the recipe): Fluffy pancakes (Chef) https://www.youtube.com/watch?v=v1"
        in shown
    )
    link = render_answer(answer.model_copy(update={"video_search_url": "https://yt/s"}))
    assert "Search YouTube: https://yt/s" in link
    top = video.model_copy(update={"verified": False})
    shown = render_answer(answer.model_copy(update={"video_fallback": top}))
    assert "Top YouTube result, not confirmed to match this recipe: Fluffy pancakes" in shown
    assert "checked against the recipe" not in shown


def test_parse_servings():
    assert parse_servings(" 6 ").servings == 6
    assert parse_servings("").servings is None  # Enter keeps the recipe as written
    with pytest.raises(ValueError, match="whole number"):
        parse_servings("two")
    with pytest.raises(ValueError):  # outside 1..100
        parse_servings("0")


def test_servings_question_and_scaled_answer_lines():
    from pantry_chef.graph.cli import read_answer, render_answer
    from pantry_chef.models.chat import FinalAnswer, Question, QuestionKind

    question = Question(kind=QuestionKind.SERVINGS, text="How many?", servings=4)
    prompts = []
    reply = read_answer(question, lambda prompt: prompts.append(prompt) or "2")
    assert reply.servings == 2 and "Enter for 4" in prompts[0]

    answer = FinalAnswer(
        recipe_id=1,
        name="rice",
        minutes=20,
        why_it_fits="Uses your rice.",
        ingredients=["1 cup rice"],
        servings=2,
        scaled_from=4,
        steps=["cook"],
    )
    assert "Ingredients (for 2 servings; the recipe serves 4):" in render_answer(answer)
