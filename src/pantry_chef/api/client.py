"""Typed HTTP client for the API (used by the Streamlit UI and the tests)."""

from typing import Any

import httpx
from pydantic import BaseModel

from pantry_chef.api.schemas import SessionOut, TurnOut


class ApiError(Exception):
    """The API refused a request; `message` is safe to show to the user."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


SERVER_ERROR = "something went wrong on our side; please try again in a moment"


def error_message(response: httpx.Response) -> str:
    """The API's `detail`, when the error body is JSON. An unexpected server error (e.g. an
    LLM provider failure) comes back as plain text "Internal Server Error": a general
    message instead, never a crash."""
    try:
        detail = response.json().get("detail")
    except ValueError:  # not JSON
        detail = None
    if detail:
        return str(detail)
    return SERVER_ERROR if response.status_code >= 500 else response.reason_phrase


class PantryChefClient:
    def __init__(self, base_url: str = "http://localhost:8000", http: httpx.Client | None = None):
        # LLM calls (rerank, matcher) can take a while on a cold cache.
        self.http = http or httpx.Client(base_url=base_url, timeout=120)

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TransportError as error:  # connection refused, timeout, ...
            raise ApiError(
                503, f"cannot reach the PantryChef API ({type(error).__name__})"
            ) from error
        if response.status_code >= 400:
            raise ApiError(response.status_code, error_message(response))
        return response

    def health(self) -> bool:
        return self._call("GET", "/health").json()["status"] == "ok"

    def new_session(self) -> str:
        return self._call("POST", "/sessions").json()["session_id"]

    def send(self, session_id: str, text: str, user_id: str | None = None) -> TurnOut:
        body = {"text": text, "user_id": user_id}
        return TurnOut.model_validate(
            self._call("POST", f"/sessions/{session_id}/messages", json=body).json()
        )

    def reply(self, session_id: str, answer: BaseModel) -> TurnOut:
        body = {"answer": answer.model_dump(mode="json")}
        return TurnOut.model_validate(
            self._call("POST", f"/sessions/{session_id}/resume", json=body).json()
        )

    def back(self, session_id: str) -> TurnOut:
        """After a recipe was chosen: show its options again."""
        return TurnOut.model_validate(self._call("POST", f"/sessions/{session_id}/back").json())

    def session(self, session_id: str) -> SessionOut:
        return SessionOut.model_validate(self._call("GET", f"/sessions/{session_id}").json())

    def close(self, session_id: str, forget: bool = False) -> None:
        self._call("DELETE", f"/sessions/{session_id}", params={"forget": forget})

    def delete_profile(self, user_id: str) -> bool:
        """True if a stored profile was deleted, False if there was none."""
        try:
            self._call("DELETE", f"/users/{user_id}/profile")
        except ApiError as error:
            if error.status == 404:
                return False
            raise
        return True
