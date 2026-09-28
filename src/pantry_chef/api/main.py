"""HTTP API over the conversation graph.

POST   /sessions                      new conversation -> session id
POST   /sessions/{id}/messages        a request; runs until the next question or the end
POST   /sessions/{id}/resume          answer the pending question
GET    /sessions/{id}                 pending question and profile
DELETE /sessions/{id}?forget=true     end it (always deleted without consent or with forget)
DELETE /users/{user_id}/profile       "delete my data"
GET    /health

The session id is the LangGraph thread id, so conversations survive API restarts (the
checkpoints are in state.db). Graph calls share SQLite connections, so they run one at a
time under a lock; enough for a demo, see docs/decisions.md.

Run: uv run uvicorn pantry_chef.api.main:app --reload
"""

import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from fastapi import FastAPI, HTTPException, Response
from pydantic import ValidationError

from pantry_chef.api.schemas import MessageIn, ResumeIn, SessionCreated, SessionOut, TurnOut
from pantry_chef.graph.app import ChatApp
from pantry_chef.graph.runner import Conversation
from pantry_chef.models.chat import REPLY_MODELS
from pantry_chef.observability import get_logger

log = get_logger("api")


def default_chat() -> ChatApp:
    from pantry_chef.config import get_settings
    from pantry_chef.graph.app import chat_from_settings
    from pantry_chef.observability import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level)
    return chat_from_settings(settings, threaded=True)


def create_app(make_chat: Callable[[], ChatApp] = default_chat) -> FastAPI:
    """The API; tests pass a ChatApp built on the fixture database."""
    lock = threading.Lock()
    holder: dict[str, ChatApp] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        holder["chat"] = make_chat()
        yield
        holder.pop("chat").close()

    app = FastAPI(title="PantryChef", version="0.2.0", lifespan=lifespan)

    @contextmanager
    def conversation(session_id: str, user_id: str | None = None) -> Iterator[Conversation]:
        with lock:
            chat = Conversation(holder["chat"].graph, thread_id=session_id)
            chat.user_id = user_id or chat.state().user_id
            yield chat

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/sessions", status_code=201)
    def new_session() -> SessionCreated:
        return SessionCreated(session_id=uuid.uuid4().hex)

    @app.post("/sessions/{session_id}/messages")
    def send_message(session_id: str, message: MessageIn) -> TurnOut:
        with conversation(session_id, message.user_id) as chat:
            return TurnOut.from_turn(chat.send(message.text))

    @app.post("/sessions/{session_id}/resume")
    def resume(session_id: str, body: ResumeIn) -> TurnOut:
        with conversation(session_id) as chat:
            question = chat.pending_question()
            if question is None:
                raise HTTPException(409, "this session has no pending question")
            try:
                answer = REPLY_MODELS[question.kind].model_validate(body.answer)
                return TurnOut.from_turn(chat.reply(answer))
            except (ValidationError, ValueError) as error:
                raise HTTPException(422, str(error)) from error

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> SessionOut:
        with conversation(session_id) as chat:
            return SessionOut(
                session_id=session_id,
                pending_question=chat.pending_question(),
                profile=chat.state().profile,
            )

    @app.delete("/sessions/{session_id}", status_code=204)
    def close_session(session_id: str, forget: bool = False) -> Response:
        with conversation(session_id) as chat:
            chat.close(forget=forget)
        return Response(status_code=204)

    @app.delete("/users/{user_id}/profile", status_code=204)
    def delete_profile(user_id: str) -> Response:
        profiles = holder["chat"].profiles
        with lock:
            deleted = profiles.delete(user_id) if profiles is not None else False
        if not deleted:
            raise HTTPException(404, "no stored profile for this user")
        return Response(status_code=204)

    return app


app = create_app()
