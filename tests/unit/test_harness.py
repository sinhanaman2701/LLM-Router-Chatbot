from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from unittest.mock import patch

from chatbot.agents.harness.harness import AgentHarness
from chatbot.agents.harness.policy_engine import PolicyEngine, PolicyResult
from chatbot.state.schemas import HarnessContext, ToolCallRequest


def _make_context():
    return HarnessContext(
        user_id="user-1",
        user_email="test@example.com",
        community_id="comm-1",
        capability="facility_booking",
        task_id="task-1",
        session_id="sess-1",
        correlation_id="corr-1",
        user_role="resident",
        prompt_version="facility_planner_v1.0",
    )


def _make_harness(policy_action="ALLOW", api_return=None, api_raise=None):
    policy_engine = MagicMock(spec=PolicyEngine)
    policy_engine.evaluate = AsyncMock(
        return_value=PolicyResult(action=policy_action, rule_id="test_rule", reason="test")
    )
    audit_logger = MagicMock()
    audit_logger.log = AsyncMock()

    api_adapter = MagicMock()
    if api_raise:
        api_adapter.get_facility_list = AsyncMock(side_effect=api_raise)
        api_adapter.make_booking = AsyncMock(side_effect=api_raise)
    else:
        return_val = api_return or [{"id": "fac_1", "name": "Tennis Court"}]
        api_adapter.get_facility_list = AsyncMock(return_value=return_val)
        api_adapter.make_booking = AsyncMock(return_value={"booking_id": "bk_1"})

    redis = MagicMock()
    redis.hget = AsyncMock(return_value=None)
    redis.hgetall = AsyncMock(return_value={})
    redis.hincrby = AsyncMock()
    redis.hset = AsyncMock()
    redis.pipeline = MagicMock(return_value=MagicMock(execute=AsyncMock(return_value=[1, True])))

    return AgentHarness(
        policy_engine=policy_engine,
        audit_logger=audit_logger,
        api_adapter=api_adapter,
        redis=redis,
    )


@pytest.mark.asyncio
async def test_pii_redaction_in_audit_log():
    harness = _make_harness(policy_action="ALLOW")
    tool_call = ToolCallRequest(
        tool_name="get_facility_list",
        params={},
        requested_by="planner",
        task_id="task-1",
    )
    result = await harness.execute(tool_call, _make_context())
    assert result.status == "SUCCESS"
    # Audit logger was called; check params don't contain plain email
    log_call_kwargs = harness._audit_logger.log.call_args.kwargs
    params_logged = log_call_kwargs["params_json"]
    assert "test@example.com" not in str(params_logged)


@pytest.mark.asyncio
async def test_awaiting_confirmation_returned():
    harness = _make_harness(policy_action="REQUIRE_CONFIRMATION")
    tool_call = ToolCallRequest(
        tool_name="create_booking",
        params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
        requested_by="planner",
        task_id="task-1",
    )
    result = await harness.execute(tool_call, _make_context(), pre_confirmed=False)
    assert result.status == "AWAITING_CONFIRMATION"
    assert result.confirmation_token is not None
    assert result.pending_call is not None


@pytest.mark.asyncio
async def test_pre_confirmed_skips_confirmation():
    harness = _make_harness(policy_action="REQUIRE_CONFIRMATION")
    tool_call = ToolCallRequest(
        tool_name="create_booking",
        params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
        requested_by="planner",
        task_id="task-1",
    )
    result = await harness.execute(tool_call, _make_context(), pre_confirmed=True)
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_uncertain_state_on_non_idempotent_5xx():
    mock_response = MagicMock()
    mock_response.status_code = 503
    exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
    harness = _make_harness(policy_action="ALLOW", api_raise=exc)
    # Patch create_booking in facility_tools to raise the error
    with patch("chatbot.agents.harness.harness.create_booking", AsyncMock(side_effect=exc)):
        tool_call = ToolCallRequest(
            tool_name="create_booking",
            params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
            requested_by="planner",
            task_id="task-1",
        )
        result = await harness.execute(tool_call, _make_context(), pre_confirmed=True)
    assert result.status == "UNCERTAIN_STATE"


@pytest.mark.asyncio
async def test_idempotent_tool_retries_on_5xx():
    mock_response = MagicMock()
    mock_response.status_code = 503
    exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_response)
    call_count = {"n": 0}

    async def flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise exc
        return [{"id": "fac_1"}]

    harness = _make_harness(policy_action="ALLOW")
    tool_call = ToolCallRequest(
        tool_name="get_facility_list",
        params={},
        requested_by="planner",
        task_id="task-1",
    )
    with patch.dict("chatbot.agents.harness.harness.TOOL_FUNCTION_MAP", {"get_facility_list": flaky}):
        with patch("asyncio.sleep", AsyncMock()):
            result = await harness.execute(tool_call, _make_context())
    assert result.status == "SUCCESS"
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_denied_by_policy():
    harness = _make_harness(policy_action="DENY")
    tool_call = ToolCallRequest(
        tool_name="create_booking",
        params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
        requested_by="planner",
        task_id="task-1",
    )
    result = await harness.execute(tool_call, _make_context())
    assert result.status == "DENIED"
