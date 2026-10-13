"""Limits for the public demo: a daily LLM budget and a cap on turns per conversation.

A turn is one call that runs the graph (a message or an answer). Graph calls run one at a
time (the API's lock), so the usage meter's difference around a turn is exactly what that
turn cost. Spend is kept per UTC day in state.db, so a restart does not reset the budget.
Both limits are off unless set (DAILY_BUDGET_USD, MAX_TURNS_PER_SESSION).
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pantry_chef.llm import usage

BUDGET_MESSAGE = (
    "The demo has used today's budget for AI calls. Please come back tomorrow "
    "(it resets at midnight UTC)."
)
TURNS_MESSAGE = (
    "This conversation has reached the demo's limit of {n} steps. "
    "Start a new conversation to keep going."
)


class LimitReached(Exception):
    """A limit stops this turn; the message is safe to show to the user."""


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass
class DemoLimits:
    state: sqlite3.Connection
    daily_budget_usd: float | None = None
    max_turns_per_session: int | None = None
    today: Callable[[], str] = utc_today
    turns: dict[str, int] = field(default_factory=dict)  # per session, since start

    def spent_today(self) -> float:
        row = self.state.execute(
            "SELECT usd FROM daily_spend WHERE day = ?", (self.today(),)
        ).fetchone()
        return row["usd"] if row else 0.0

    def check(self, session_id: str) -> None:
        """Raise LimitReached if this turn may not run."""
        if self.daily_budget_usd is not None and self.spent_today() >= self.daily_budget_usd:
            raise LimitReached(BUDGET_MESSAGE)
        limit = self.max_turns_per_session
        if limit is not None and self.turns.get(session_id, 0) >= limit:
            raise LimitReached(TURNS_MESSAGE.format(n=limit))

    def record(self, session_id: str, spent: dict[str, usage.ModelUsage]) -> float:
        """Count the turn and add what it cost; returns that cost in USD."""
        self.turns[session_id] = self.turns.get(session_id, 0) + 1
        usd = usage.total_cost(spent)  # every model has a price (checked at start)
        with self.state:
            self.state.execute(
                "INSERT INTO daily_spend (day, usd) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd",
                (self.today(), usd),
            )
        return usd


def require_prices(models: list[str]) -> None:
    """With a budget, every model must have a price, or the cap could not count it."""
    missing = sorted({m for m in models if m not in usage.PRICES})
    if missing:
        raise ValueError(
            f"DAILY_BUDGET_USD is set but {missing} have no price in llm/usage.py PRICES"
        )
