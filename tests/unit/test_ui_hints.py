from __future__ import annotations

import time

from chatbot.agents.planners.base_planner import PlannerOutput
from chatbot.routers.chat import _compute_ui_hints
from chatbot.state.schemas import ConversationSession, SlotState, TaskContext, UserInfo


def _make_session(
    date: str | None = None,
    start_time: str | None = None,
    awaiting_confirmation: bool = False,
    active_task: bool = True,
) -> ConversationSession:
    now = int(time.time())
    user = UserInfo(
        user_id="u1", community_id="c1",
        email="test@example.com", unit_id="flat1", role="resident",
    )
    if not active_task:
        return ConversationSession(
            session_id="s1", user=user, active_task=None,
            created_at=now, last_activity_at=now,
        )
    task = TaskContext(
        task_id="t1", capability="facility_booking",
        prompt_version="facility_planner_v1.0",
        slots=SlotState(date=date, start_time=start_time),
        awaiting_confirmation=awaiting_confirmation,
        created_at=now, last_updated_at=now,
    )
    return ConversationSession(
        session_id="s1", user=user, active_task=task,
        created_at=now, last_activity_at=now,
    )


def _user_question() -> PlannerOutput:
    return PlannerOutput(type="user_question", content="What date?")


def _final_answer() -> PlannerOutput:
    return PlannerOutput(type="final_answer", content="Basketball not found.")


# ── Existing cases — all require planner_output="user_question" ──────────────

def test_no_active_task_returns_empty():
    assert _compute_ui_hints(_make_session(active_task=False), _user_question()) == {}


def test_date_none_returns_date_picker():
    assert _compute_ui_hints(_make_session(date=None), _user_question()) == {"type": "date_picker"}


def test_awaiting_confirmation_with_date_returns_confirm_pill():
    result = _compute_ui_hints(_make_session(date="2026-07-20", awaiting_confirmation=True), _user_question())
    assert result == {"type": "date_confirm_pill", "current_date": "2026-07-20"}


def test_date_set_start_time_none_returns_change_pill():
    result = _compute_ui_hints(_make_session(date="2026-07-20", start_time=None), _user_question())
    assert result == {"type": "date_change_pill", "current_date": "2026-07-20"}


def test_date_and_start_time_both_set_returns_empty():
    assert _compute_ui_hints(_make_session(date="2026-07-20", start_time="09:00"), _user_question()) == {}


def test_awaiting_confirmation_date_none_still_returns_confirm_pill():
    result = _compute_ui_hints(_make_session(date=None, awaiting_confirmation=True), _user_question())
    assert result == {"type": "date_confirm_pill", "current_date": None}


# ── Regression: date picker must NOT appear when planner concluded ────────────

def test_final_answer_suppresses_date_picker():
    # "Basketball not found" scenario: date=None but planner said final_answer
    result = _compute_ui_hints(_make_session(date=None), _final_answer())
    assert result == {}


def test_no_planner_output_suppresses_hints():
    # small_talk / unclear / cancel_task paths never set planner_output
    result = _compute_ui_hints(_make_session(date=None), None)
    assert result == {}
