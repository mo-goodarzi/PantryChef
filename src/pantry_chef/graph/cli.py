"""Chat with PantryChef in the terminal (the full graph, with its questions).

Usage:
    uv run python scripts/chat_cli.py --user alice
    uv run python scripts/chat_cli.py --user alice --thread <id>   # continue a conversation

Type "quit" to leave. Without consent, the conversation (and the allergies in it) is
deleted when you leave.
"""

import argparse
import sys
from collections.abc import Callable

from pydantic import BaseModel

from pantry_chef.graph.runner import Conversation
from pantry_chef.models.chat import (
    AmountReply,
    ChoiceReply,
    ConfirmReply,
    FinalAnswer,
    QuantityReply,
    Question,
    QuestionKind,
    SafetyReply,
    Turn,
)
from pantry_chef.models.query import AmountStatus

YES = {"y", "yes", "yeah", "yep", "sure", "ok", "correct", "right"}
NO = {"n", "no", "nope", "wrong"}
UNKNOWN_WORDS = {"", "?", "dont know", "don't know", "not sure", "unknown", "idk"}
PLENTY_WORDS = {"plenty", "lots", "a lot", "enough"}


# --- reading answers -----------------------------------------------------------------


def parse_yes_no(text: str) -> bool:
    word = text.strip().lower()
    if word in YES:
        return True
    if word in NO:
        return False
    raise ValueError("please answer yes or no")


def parse_amount(text: str) -> AmountReply:
    """ "2" -> 2 (a count), "200 g" -> 200 g, "don't know" -> unknown, "plenty"."""
    words = text.strip().lower()
    if words in UNKNOWN_WORDS:
        return AmountReply(status=AmountStatus.UNKNOWN)
    if words in PLENTY_WORDS:
        return AmountReply(status=AmountStatus.PLENTY)
    number, _, unit = words.partition(" ")
    try:
        quantity = float(number)
    except ValueError:
        raise ValueError("type a number (e.g. 2 or 200 g), 'don't know' or 'plenty'") from None
    return AmountReply(quantity=quantity, unit=unit.strip() or None, status=AmountStatus.KNOWN)


def parse_choice(text: str) -> ChoiceReply:
    word = text.strip().lower()
    if word in {"more", "m", "other", "others"}:
        return ChoiceReply(more=True)
    if word.isdigit():
        return ChoiceReply(choice=int(word))
    raise ValueError("type the number of a recipe, or 'more' for other recipes")


def read_answer(question: Question, read: Callable[[str], str]) -> BaseModel:
    """Ask for the answer to one question (`read` is input() in the terminal)."""
    if question.kind is QuestionKind.SAFETY:
        return SafetyReply(text=read("you> "))
    if question.kind is QuestionKind.SAFETY_CONFIRM:
        correct = parse_yes_no(read("Is this right? (yes/no) "))
        consent = correct and parse_yes_no(read("Remember it for next time? (yes/no) "))
        return ConfirmReply(correct=correct, consent_to_store=consent)
    if question.kind is QuestionKind.QUANTITIES:
        amounts = {item: parse_amount(read(f"  {item}: ")) for item in question.items}
        return QuantityReply(amounts=amounts)
    return parse_choice(read("your choice (number or 'more')> "))


# --- showing turns -------------------------------------------------------------------


def render_question(question: Question) -> str:
    if question.kind is QuestionKind.SAFETY_CONFIRM:
        return question.text.removesuffix("Is this right? And may I remember it for next time?")
    if question.kind is not QuestionKind.CHOICE:
        return question.text
    lines = [question.text]
    if question.note:
        lines.append(f"({question.note})")
    for o in question.options:
        lines.append(f" {o.number}. {o.name} ({o.minutes} min)")
        if o.why:
            lines.append(f"    why: {o.why}")
        if o.uses:
            lines.append(f"    uses: {', '.join(o.uses)}")
        for warning in o.warnings:
            lines.append(f"    ALLERGY: {warning}")
        if o.adaptations:
            lines.append(f"    adapt: {'; '.join(o.adaptations)}")
        if o.also_needs:
            lines.append(f"    also needs: {', '.join(o.also_needs)}")
    return "\n".join(lines)


def render_answer(answer: FinalAnswer) -> str:
    lines = [f"{answer.name} ({answer.minutes} min)", f"Why it fits: {answer.why_it_fits}"]
    lines.append("Ingredients: " + ", ".join(answer.ingredients))
    lines += [f"ALLERGY: {warning}" for warning in answer.warnings]
    if answer.adaptations:
        lines.append("Adapt: " + "; ".join(answer.adaptations))
    if answer.also_needs:
        lines.append("You also need: " + ", ".join(answer.also_needs))
    lines.append("Steps:")
    lines += [f" {n}. {step}" for n, step in enumerate(answer.steps, start=1)]
    lines += answer.notes
    if answer.disclaimer:
        lines.append(f"Note: {answer.disclaimer}")
    return "\n".join(lines)


def render_turn(turn: Turn) -> str:
    if turn.question is not None:
        return render_question(turn.question)
    if turn.answer is not None:
        return render_answer(turn.answer)
    return turn.reply or ""


# --- loop ----------------------------------------------------------------------------


def run(chat: Conversation, read: Callable[[str], str], write: Callable[[str], None]) -> None:
    """One conversation: requests until "quit"; every question is answered in turn."""
    pending = chat.pending_question()
    write(
        render_question(pending)
        if pending
        else "What do you have at home, and what do you feel like?"
    )
    turn = Turn(thread_id=chat.thread_id, question=pending)
    while True:
        if turn.question is None:
            message = read("you> ").strip()
            if message.lower() in {"quit", "exit", "q"}:
                return
            if not message:
                continue
            turn = chat.send(message)
        else:
            try:
                turn = chat.reply(read_answer(turn.question, read))
            except ValueError as error:
                write(f"({error})")
                continue
        write(render_turn(turn))


def main() -> None:
    from pantry_chef.config import get_settings
    from pantry_chef.graph.app import chat_from_settings
    from pantry_chef.observability import configure_logging

    parser = argparse.ArgumentParser(description="Chat with PantryChef.")
    parser.add_argument("--user", help="your name, to remember your profile (with consent)")
    parser.add_argument("--thread", help="continue an earlier conversation")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    try:
        app = chat_from_settings(settings)
    except (FileNotFoundError, ValueError) as error:
        sys.exit(f"error: {error}")
    chat = Conversation(app.graph, thread_id=args.thread, user_id=args.user)
    print(f"(conversation {chat.thread_id}; type 'quit' to leave)")
    try:
        run(chat, input, print)
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        chat.close()
        app.close()


if __name__ == "__main__":
    main()
