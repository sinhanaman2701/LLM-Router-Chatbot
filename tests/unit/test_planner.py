from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.agents.planners.base_planner import BasePlanner, PlannerOutput, _format_slots
from chatbot.agents.planners.facility_planner import FacilityPlanner
from chatbot.state.schemas import (
    ConversationSession,
    HarnessContext,
    HarnessResult,
    SlotState,
    TaskContext,
    ToolCallRequest,
    UserInfo,
)


def _make_session(planner_memory=None, slots=None):
    now = 1748000000
    task = TaskContext(
        task_id="task-1",
        capability="facility_booking",
        prompt_version="facility_planner_v1.0",
        planner_memory=planner_memory or [],
        slots=slots or SlotState(),
        created_at=now,
        last_updated_at=now,
    )
    return ConversationSession(
        session_id="sess-1",
        user=UserInfo(
            user_id="user-1",
            community_id="comm-1",
            email="test@example.com",
            unit_id="flat101",
            role="resident",
        ),
        active_task=task,
        created_at=now,
        last_activity_at=now,
    )


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


def _make_planner(llm_responses: list[str]):
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=llm_responses)

    harness = MagicMock()
    harness.execute = AsyncMock(
        return_value=HarnessResult(
            status="SUCCESS",
            tool_run_id="run-1",
            data=[{"id": "fac_1", "name": "Tennis Court", "default_duration_min": 60}],
        )
    )

    state_manager = MagicMock()
    state_manager.update_planner_memory = AsyncMock()
    state_manager.set_awaiting_confirmation = AsyncMock()

    return FacilityPlanner(llm, harness, state_manager)


FINAL_ANSWER = 'Thought: Done.\n\nAction:\n{"type": "final_answer", "summary": "Booking complete.", "status": "success"}'
TOOL_CALL = 'Thought: Need facilities.\n\nAction:\n{"type": "tool_call", "tool_name": "get_facility_list", "params": {}}'
USER_QUESTION = 'Thought: Need date.\n\nAction:\n{"type": "user_question", "question": "Which date?", "missing_slot": "date"}'


@pytest.mark.asyncio
async def test_final_answer_exits_immediately():
    planner = _make_planner([FINAL_ANSWER])
    output = await planner.run(_make_session(), "Book tennis", _make_context(), {})
    assert output.type == "final_answer"
    assert output.content == "Booking complete."
    assert output.status == "success"


@pytest.mark.asyncio
async def test_user_question_exits():
    planner = _make_planner([USER_QUESTION])
    output = await planner.run(_make_session(), "Book something", _make_context(), {})
    assert output.type == "user_question"
    assert "date" in output.content.lower()


@pytest.mark.asyncio
async def test_tool_call_then_final_answer():
    planner = _make_planner([TOOL_CALL, FINAL_ANSWER])
    output = await planner.run(_make_session(), "Book tennis", _make_context(), {})
    assert output.type == "final_answer"
    assert planner._harness.execute.call_count == 1


@pytest.mark.asyncio
async def test_iteration_cap_returns_incomplete():
    # Always emit a tool_call — should hit iteration cap
    planner = _make_planner([TOOL_CALL] * 10)
    output = await planner.run(_make_session(), "Book tennis", _make_context(), {})
    assert output.type == "final_answer"
    assert output.status == "incomplete"


@pytest.mark.asyncio
async def test_malformed_output_continues():
    # First response is malformed, second is a valid final_answer
    planner = _make_planner(["This is not valid format", FINAL_ANSWER])
    output = await planner.run(_make_session(), "Book tennis", _make_context(), {})
    assert output.type == "final_answer"


@pytest.mark.asyncio
async def test_awaiting_confirmation_exits():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=TOOL_CALL)

    harness = MagicMock()
    harness.execute = AsyncMock(
        return_value=HarnessResult(
            status="AWAITING_CONFIRMATION",
            tool_run_id="run-1",
            confirmation_token="tok123",
            pending_call=ToolCallRequest(
                tool_name="create_booking",
                params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
                requested_by="planner",
                task_id="task-1",
            ),
            confirmation_summary="Confirm booking Tennis Court?",
        )
    )

    state_manager = MagicMock()
    state_manager.update_planner_memory = AsyncMock()
    state_manager.set_awaiting_confirmation = AsyncMock()

    planner = FacilityPlanner(llm, harness, state_manager)
    output = await planner.run(_make_session(), "Book tennis", _make_context(), {})
    assert output.type == "user_question"
    assert "Confirm" in output.content
    state_manager.set_awaiting_confirmation.assert_called_once()
