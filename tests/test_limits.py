"""Demo limits: the daily LLM budget (kept in state.db) and turns per conversation."""

import pytest

from pantry_chef.api.limits import (
    BUDGET_MESSAGE,
    DemoLimits,
    LimitReached,
    require_prices,
)
from pantry_chef.db.state import open_state_db
from pantry_chef.llm.usage import ModelUsage

# gpt-5.4-mini: $0.75 / 1M input, $4.50 / 1M output -> 1M in + 100k out = $1.20
TURN = {"gpt-5.4-mini": ModelUsage(calls=3, input_tokens=1_000_000, output_tokens=100_000)}


def limits(tmp_path, day="2026-10-13", **options):
    return DemoLimits(open_state_db(tmp_path / "state.db"), today=lambda: day, **options)


def test_the_budget_stops_turns_once_spent_and_resets_the_next_day(tmp_path):
    demo = limits(tmp_path, daily_budget_usd=2.0)
    demo.check("s1")
    assert demo.record("s1", TURN) == pytest.approx(1.20)
    demo.check("s2")  # $1.20 of $2: still fine
    demo.record("s2", TURN)
    with pytest.raises(LimitReached, match="today's budget"):
        demo.check("s3")  # $2.40: over, for every session

    tomorrow = limits(tmp_path, day="2026-10-14", daily_budget_usd=2.0)
    tomorrow.check("s3")


def test_spend_survives_a_restart(tmp_path):
    limits(tmp_path, daily_budget_usd=1.0).record("s1", TURN)
    with pytest.raises(LimitReached):
        limits(tmp_path, daily_budget_usd=1.0).check("s2")  # a new process, same day


def test_turns_per_session_are_capped_per_conversation(tmp_path):
    demo = limits(tmp_path, max_turns_per_session=2)
    for _ in range(2):
        demo.check("s1")
        demo.record("s1", {})
    with pytest.raises(LimitReached, match="limit of 2 steps"):
        demo.check("s1")
    demo.check("s2")  # another conversation is not affected


def test_a_turn_without_llm_calls_costs_nothing(tmp_path):
    demo = limits(tmp_path, daily_budget_usd=0.01)
    assert demo.record("s1", {}) == 0.0
    demo.check("s1")


def test_a_budget_needs_a_price_for_every_model():
    require_prices(["gpt-5.4-mini", "gpt-5.4"])
    with pytest.raises(ValueError, match="no-price-model"):
        require_prices(["gpt-5.4-mini", "no-price-model"])


def test_budget_message_is_for_users():
    assert "come back tomorrow" in BUDGET_MESSAGE


def test_limits_are_off_locally_and_on_with_settings(tmp_path):
    from pantry_chef.config import Settings
    from pantry_chef.graph.app import demo_limits

    state = open_state_db(tmp_path / "state.db")
    assert demo_limits(Settings(_env_file=None), state) is None
    on = demo_limits(Settings(_env_file=None, daily_budget_usd=2, max_turns_per_session=40), state)
    assert on is not None and on.daily_budget_usd == 2 and on.max_turns_per_session == 40
    with pytest.raises(ValueError, match="no price"):
        demo_limits(Settings(_env_file=None, daily_budget_usd=2, video_model="mystery"), state)
