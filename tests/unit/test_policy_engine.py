from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.agents.harness.policy_engine import PolicyEngine
from chatbot.state.schemas import HarnessContext


def _make_context():
    return HarnessContext(
        user_id="user-123",
        user_email="test@test.com",
        community_id="comm-1",
        capability="facility_booking",
        task_id="task-1",
        session_id="sess-1",
        correlation_id="corr-1",
        user_role="resident",
        prompt_version="facility_planner_v1.0",
    )


def _make_redis(incr_value: int = 1):
    redis = MagicMock()
    pipe = MagicMock()
    pipe.incr = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[incr_value, True])
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


@pytest.mark.asyncio
async def test_create_booking_requires_confirmation():
    engine = PolicyEngine(redis=_make_redis(1))
    result = await engine.evaluate("create_booking", {}, _make_context())
    assert result.action == "REQUIRE_CONFIRMATION"
    assert result.rule_id == "always_confirm_booking"


@pytest.mark.asyncio
async def test_cancel_booking_requires_confirmation():
    engine = PolicyEngine(redis=_make_redis(1))
    result = await engine.evaluate("cancel_booking", {}, _make_context())
    assert result.action == "REQUIRE_CONFIRMATION"
    assert result.rule_id == "always_confirm_cancellation"


@pytest.mark.asyncio
async def test_rate_limit_denies_after_5():
    with patch("chatbot.agents.harness.policy_engine.settings.APP_ENV", "production"):
        engine = PolicyEngine(redis=_make_redis(6))
        result = await engine.evaluate("create_booking", {}, _make_context())
    assert result.action == "DENY"
    assert result.rule_id == "rate_limit_booking"


@pytest.mark.asyncio
async def test_rate_limit_is_skipped_outside_production():
    with patch("chatbot.agents.harness.policy_engine.settings.APP_ENV", "development"):
        engine = PolicyEngine(redis=_make_redis(6))
        result = await engine.evaluate("create_booking", {}, _make_context())
    assert result.action == "REQUIRE_CONFIRMATION"
    assert result.rule_id == "always_confirm_booking"


@pytest.mark.asyncio
async def test_get_facility_list_allows():
    engine = PolicyEngine(redis=_make_redis(1))
    result = await engine.evaluate("get_facility_list", {}, _make_context())
    assert result.action == "ALLOW"


@pytest.mark.asyncio
async def test_get_my_bookings_allows():
    engine = PolicyEngine(redis=_make_redis(1))
    result = await engine.evaluate("get_my_bookings", {}, _make_context())
    assert result.action == "ALLOW"
