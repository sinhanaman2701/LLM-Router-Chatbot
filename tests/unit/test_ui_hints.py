from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.agents.planners.base_planner import PlannerOutput
from chatbot.routers.chat import _build_ui_hints, _compute_ui_hints
from chatbot.state.schemas import ConversationSession, SlotState, TaskContext, UserInfo


def _make_session(
    *,
    facility_name: str | None = "Tennis Court",
    facility_id: str | None = "fac_1",
    date: str | None = None,
    start_time: str | None = None,
    duration_minutes: int | None = 60,
    open_time: str | None = "07:00",
    close_time: str | None = "22:00",
    active_task: bool = True,
) -> ConversationSession:
    now = int(time.time())
    user = UserInfo(
        user_id="u1",
        community_id="c1",
        email="test@example.com",
        unit_id="flat1",
        role="resident",
    )
    if not active_task:
        return ConversationSession(
            session_id="s1",
            user=user,
            active_task=None,
            created_at=now,
            last_activity_at=now,
        )
    task = TaskContext(
        task_id="t1",
        capability="facility_booking",
        prompt_version="facility_planner_v1.0",
        slots=SlotState(
            facility_name=facility_name,
            facility_id=facility_id,
            date=date,
            start_time=start_time,
            duration_minutes=duration_minutes,
            open_time=open_time,
            close_time=close_time,
        ),
        created_at=now,
        last_updated_at=now,
    )
    return ConversationSession(
        session_id="s1",
        user=user,
        active_task=task,
        created_at=now,
        last_activity_at=now,
    )


def _user_question() -> PlannerOutput:
    return PlannerOutput(type="user_question", content="What date?")


def _final_answer() -> PlannerOutput:
    return PlannerOutput(type="final_answer", content="Done.")


def _make_redis():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    return redis


def _make_api_adapter():
    adapter = MagicMock()
    adapter.get_facility_list = AsyncMock(
        return_value=[
            {
                "id": "fac_1",
                "name": "Tennis Court",
                "open_time": "07:00",
                "close_time": "22:00",
                "default_duration_min": 60,
            }
        ]
    )
    adapter.get_facility_booking_data = AsyncMock(
        return_value={
            "bookings": [
                {"date": "2026-07-20", "start_time": "08:00"},
                {"date": "2026-07-20", "start_time": "10:00"},
            ],
            "open_time": "07:00",
            "close_time": "22:00",
            "default_duration_min": 60,
        }
    )
    return adapter


def test_no_active_task_returns_empty():
    assert _compute_ui_hints(_make_session(active_task=False), _user_question()) == {}


def test_date_none_returns_inline_date_picker():
    assert _compute_ui_hints(_make_session(date=None), _user_question()) == {
        "type": "date_picker_inline",
        "submit_prefix": "Set date to ",
    }


def test_date_set_start_time_none_returns_inline_time_picker():
    assert _compute_ui_hints(_make_session(date="2026-07-20", start_time=None), _user_question()) == {
        "type": "time_picker_inline",
        "current_date": "2026-07-20",
        "submit_prefix": "Set time to ",
    }


def test_date_and_start_time_set_returns_time_change_pill():
    assert _compute_ui_hints(_make_session(date="2026-07-20", start_time="09:00"), _user_question()) == {
        "type": "time_change_pill",
        "current_date": "2026-07-20",
        "current_time": "09:00",
        "submit_prefix": "Change time to ",
    }


def test_final_answer_suppresses_hints():
    assert _compute_ui_hints(_make_session(date=None), _final_answer()) == {}


def test_no_planner_output_suppresses_hints():
    assert _compute_ui_hints(_make_session(date=None), None) == {}


@pytest.mark.asyncio
async def test_build_ui_hints_populates_full_slot_picker():
    session = _make_session(date="2026-07-20", start_time=None)
    result = await _build_ui_hints(session, _user_question(), _make_redis(), _make_api_adapter())
    assert result["type"] == "time_picker_inline"
    assert result["facility_id"] == "fac_1"
    assert result["duration_minutes"] == 60
    assert any(slot["time"] == "07:00" and slot["status"] == "available" for slot in result["slots"])
    assert any(slot["time"] == "08:00" and slot["status"] == "unavailable" and slot["selectable"] is False for slot in result["slots"])


@pytest.mark.asyncio
async def test_build_ui_hints_marks_selected_time():
    session = _make_session(date="2026-07-20", start_time="09:00")
    result = await _build_ui_hints(session, _user_question(), _make_redis(), _make_api_adapter())
    assert result["type"] == "time_change_pill"
    assert any(slot["time"] == "09:00" and slot["status"] == "selected" and slot["selectable"] is True for slot in result["slots"])


@pytest.mark.asyncio
async def test_build_ui_hints_returns_empty_when_facility_resolution_fails():
    session = _make_session(facility_name=None, facility_id=None, date="2026-07-20", start_time=None)
    adapter = _make_api_adapter()
    adapter.get_facility_list = AsyncMock(return_value=[])
    result = await _build_ui_hints(session, _user_question(), _make_redis(), adapter)
    assert result == {}
