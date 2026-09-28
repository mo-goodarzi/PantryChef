"""Request and response bodies of the HTTP API."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from pantry_chef.models.chat import FinalAnswer, Question, QuestionKind, Turn
from pantry_chef.models.profile import UserProfile


class TurnType(StrEnum):
    QUESTION = "question"  # safety intake, confirmation or quantities
    CANDIDATES = "candidates"  # recipes to choose from
    FINAL = "final"  # the chosen recipe
    MESSAGE = "message"  # a plain reply (e.g. "tell me what you have")


class SessionCreated(BaseModel):
    session_id: str


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)  # remembers the profile


class ResumeIn(BaseModel):
    """The answer to the pending question; its fields depend on the question kind
    (see models.chat.REPLY_MODELS)."""

    answer: dict[str, Any]


class TurnOut(BaseModel):
    session_id: str
    type: TurnType
    question: Question | None = None
    answer: FinalAnswer | None = None
    message: str | None = None

    @classmethod
    def from_turn(cls, turn: Turn) -> "TurnOut":
        if turn.question is not None:
            kind = TurnType.CANDIDATES
            if turn.question.kind is not QuestionKind.CHOICE:
                kind = TurnType.QUESTION
            return cls(session_id=turn.thread_id, type=kind, question=turn.question)
        if turn.answer is not None:
            return cls(session_id=turn.thread_id, type=TurnType.FINAL, answer=turn.answer)
        return cls(session_id=turn.thread_id, type=TurnType.MESSAGE, message=turn.reply)


class SessionOut(BaseModel):
    session_id: str
    pending_question: Question | None
    profile: UserProfile | None  # the user's own session: shown in the UI sidebar
