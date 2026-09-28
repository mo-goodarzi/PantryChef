"""One conversation with the graph: send a message, answer questions, close.

Each call is one Langfuse trace (grouped by session = thread id). The CLI now and the
UI/API later use this class, so they never deal with LangGraph details.
"""

import uuid
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from pantry_chef.graph.state import ChatState
from pantry_chef.models.chat import ChoiceReply, Question, QuestionKind, Turn
from pantry_chef.observability import trace


def check_choice(answer: ChoiceReply, question: Question) -> None:
    """Reject a choice that is not one of the shown options (before resuming the graph)."""
    if question.kind is not QuestionKind.CHOICE or answer.more:
        return
    if not 1 <= (answer.choice or 0) <= len(question.options):
        raise ValueError(f"choose a number from 1 to {len(question.options)}")


class Conversation:
    def __init__(
        self,
        graph: CompiledStateGraph,
        thread_id: str | None = None,
        user_id: str | None = None,
    ):
        self.graph = graph
        self.thread_id = thread_id or uuid.uuid4().hex
        self.user_id = user_id
        self.config: Any = {"configurable": {"thread_id": self.thread_id}}

    def send(self, message: str) -> Turn:
        """A new request from the user (starts from the top of the graph)."""
        return self._run({"message": message, "user_id": self.user_id})

    def reply(self, answer: BaseModel) -> Turn:
        """Answer the pending question (SafetyReply, ConfirmReply, QuantityReply or
        ChoiceReply)."""
        question = self.pending_question()
        if question is None:
            raise ValueError("there is no question to answer")
        if isinstance(answer, ChoiceReply):
            check_choice(answer, question)
        return self._run(Command(resume=answer.model_dump(mode="json")))

    def pending_question(self) -> Question | None:
        interrupts = self.graph.get_state(self.config).interrupts
        return Question.model_validate(interrupts[0].value) if interrupts else None

    def state(self) -> ChatState:
        return ChatState.model_validate(self.graph.get_state(self.config).values)

    def close(self) -> None:
        """End the conversation. Without consent, its saved state (which includes the
        user's allergies) is deleted; only consented profiles are kept."""
        profile = self.state().profile
        if profile is None or not profile.consent_to_store:
            checkpointer = self.graph.checkpointer
            if checkpointer is not None and hasattr(checkpointer, "delete_thread"):
                checkpointer.delete_thread(self.thread_id)

    def _run(self, graph_input: Any) -> Turn:
        with trace("chat_turn", session_id=self.thread_id, user_id=self.user_id):
            result = self.graph.invoke(graph_input, self.config)
        interrupts = result.get("__interrupt__")
        if interrupts:
            return Turn(
                thread_id=self.thread_id, question=Question.model_validate(interrupts[0].value)
            )
        state = ChatState.model_validate({k: v for k, v in result.items() if k != "__interrupt__"})
        return Turn(thread_id=self.thread_id, answer=state.answer, reply=state.reply)
