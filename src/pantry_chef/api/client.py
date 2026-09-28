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


class PantryChefClient:
    def __init__(self, base_url: str = "http://localhost:8000", http: httpx.Client | None = None):
        # LLM calls (rerank, matcher) can take a while on a cold cache.
        self.http = http or httpx.Client(base_url=base_url, timeout=120)

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self.http.request(method, path, **kwargs)
        if response.status_code >= 400:
            detail = response.json().get("detail") if response.content else None
            raise ApiError(response.status_code, str(detail or response.reason_phrase))
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
