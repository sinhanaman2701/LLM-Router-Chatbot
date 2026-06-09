from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.agents.planners.base_planner import PlannerOutput
from chatbot.routers.chat import _process_message
from chatbot.state.schemas import ConversationSession, RouterDecision, SlotState, TaskContext, ToolCallRequest, UserInfo


def _make_session(*, task: TaskContext | None = None) -> ConversationSession:
    now = 1748000000
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


def _make_task(*, awaiting_confirmation: bool = False) -> TaskContext:
    now = 1748000000
    return TaskContext(
        task_id="task-1",
        capability="facility_booking",
        prompt_version="facility_planner_v1.0",
        slots=SlotState(
            facility_name="Tennis Court",
            facility_id="fac_1",
            open_time="07:00",
            close_time="22:00",
            duration_minutes=60,
        ),
        awaiting_confirmation=awaiting_confirmation,
        confirmation_token="tok-1" if awaiting_confirmation else None,
        pending_tool_call=ToolCallRequest(
            tool_name="create_booking",
            params={"facility_id": "fac_1", "date": "2026-07-10", "start_time": "10:00", "end_time": "11:00"},
            requested_by="planner",
            task_id="task-1",
        ) if awaiting_confirmation else None,
        created_at=now,
        last_updated_at=now,
    )


def _make_app_state(session: ConversationSession):
    cleared_session = _make_session(task=None)
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()

    state_manager = MagicMock()
    state_manager.get_session = AsyncMock(return_value=session)
    state_manager._atomic_session_write = AsyncMock(side_effect=lambda session_id, fn: fn(session))
    state_manager.init_task = AsyncMock(return_value=session)
    state_manager.update_slots = AsyncMock(return_value=session)
    state_manager.stash_task = AsyncMock()
    state_manager.suspend_task = AsyncMock()
    state_manager.restore_latest_task = AsyncMock(return_value=session)
    state_manager.restore_stashed_task = AsyncMock(return_value=session)
    state_manager.reject_confirmation = AsyncMock(return_value=session)
    state_manager.clear_task = AsyncMock(return_value=cleared_session)
    state_manager.bump_confirmation_turns = AsyncMock(return_value=(session, False))

    router_agent = MagicMock()
    router_agent.classify = AsyncMock()

    facility_planner = MagicMock()
    facility_planner.run = AsyncMock(return_value=PlannerOutput(type="final_answer", content="planner reply", status="success"))

    preferences_manager = MagicMock()
    preferences_manager.get_all = AsyncMock(return_value={})
    preferences_manager.upsert = AsyncMock()

    synthesizer = MagicMock()
    synthesizer.synthesize = MagicMock(return_value="planner reply")

    api_adapter = MagicMock()
    api_adapter.get_facility_list = AsyncMock(return_value=[
        {"name": "Tennis Court", "category": "Sports", "open_time": "07:00", "close_time": "22:00", "default_duration_min": 60},
        {"name": "Swimming Pool", "category": "Recreation"},
        {"name": "Gym", "category": "Fitness", "open_time": "06:00", "close_time": "22:00", "default_duration_min": 90},
    ])
    api_adapter.get_my_bookings = AsyncMock(return_value={"bookings": [{"booking_id": "bk_1"}]})

    return SimpleNamespace(
        redis=redis,
        state_manager=state_manager,
        router_agent=router_agent,
        facility_planner=facility_planner,
        preferences_manager=preferences_manager,
        synthesizer=synthesizer,
        harness=MagicMock(),
        db_pool=MagicMock(),
        api_adapter=api_adapter,
    )


@pytest.mark.asyncio
async def test_switch_task_uses_suspended_stack():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="switch_task",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="Actually cancel my booking instead",
        app_state=app_state,
    )

    app_state.state_manager.suspend_task.assert_called_once_with(session.session_id)
    app_state.state_manager.stash_task.assert_not_called()


@pytest.mark.asyncio
async def test_resume_task_uses_restore_latest_task():
    session = _make_session(task=None)
    restored = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.state_manager.restore_latest_task.return_value = restored
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="resume_task",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="resume the earlier booking",
        app_state=app_state,
    )

    app_state.state_manager.restore_latest_task.assert_called_once_with(session.session_id)
    app_state.state_manager.restore_stashed_task.assert_not_called()


@pytest.mark.asyncio
async def test_rejection_keeps_task_and_replans():
    session = _make_session(task=_make_task(awaiting_confirmation=True))
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="rejection",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="no, make it later",
        app_state=app_state,
    )

    app_state.state_manager.reject_confirmation.assert_called_once_with(session.session_id, "no, make it later")
    app_state.facility_planner.run.assert_called_once()
    app_state.state_manager.clear_task.assert_not_called()


@pytest.mark.asyncio
async def test_side_question_uses_read_only_path_without_planner():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="what facilities are available?",
        app_state=app_state,
    )

    app_state.state_manager.stash_task.assert_called_once_with(session.session_id)
    app_state.state_manager.restore_latest_task.assert_called_once_with(session.session_id)
    app_state.facility_planner.run.assert_not_called()

    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "Available facilities" in payload["response"]


@pytest.mark.asyncio
async def test_side_question_my_bookings_reads_upcoming_and_past_shape():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.api_adapter.get_my_bookings = AsyncMock(
        return_value={
            "upcoming_bookings": [
                {"booking_id": "bk_123", "facility_name": "Tennis Court", "date": "2026-07-10", "start_time": "10:00"}
            ],
            "past_bookings": [],
        }
    )
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-bookings",
        session_id=session.session_id,
        user_message="what are my bookings?",
        app_state=app_state,
    )

    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "bk_123" in payload["response"]


@pytest.mark.asyncio
async def test_side_question_can_lookup_specific_booking_id():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.api_adapter.get_my_bookings = AsyncMock(
        return_value={
            "upcoming_bookings": [
                {
                    "booking_id": "bk_1781009047020",
                    "facility_name": "Tennis Court",
                    "date": "2026-07-10",
                    "start_time": "10:00",
                    "status": "Confirmed",
                }
            ],
            "past_bookings": [],
        }
    )
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-booking-id",
        session_id=session.session_id,
        user_message="this is my booking bk_1781009047020",
        app_state=app_state,
    )

    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "Booking bk_1781009047020 is for Tennis Court" in payload["response"]


@pytest.mark.asyncio
async def test_side_question_can_answer_facility_hours_from_cached_context():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="what are the timings for the tennis court?",
        app_state=app_state,
    )

    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "open from 07:00 to 22:00" in payload["response"]


@pytest.mark.asyncio
async def test_side_question_can_answer_by_category():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="what can i do for fitness",
        app_state=app_state,
    )

    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "For fitness, you can use: Gym" in payload["response"]


@pytest.mark.asyncio
async def test_pending_confirmation_times_out_after_unrelated_turns():
    session = _make_session(task=_make_task(awaiting_confirmation=True))
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="side_question",
        confidence=0.99,
        extracted_slots={},
    )
    app_state.state_manager.bump_confirmation_turns.return_value = (session, True)

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="what facilities are available?",
        app_state=app_state,
    )

    app_state.facility_planner.run.assert_not_called()
    payload = json.loads(app_state.redis.set.call_args_list[-1].args[1])
    assert "cancelled the pending confirmation" in payload["response"]


@pytest.mark.asyncio
async def test_confirmation_success_clears_active_task():
    session = _make_session(task=_make_task(awaiting_confirmation=True))
    app_state = _make_app_state(session)
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="confirmation",
        confidence=0.99,
        extracted_slots={},
    )
    app_state.state_manager.release_confirmation = AsyncMock(
        return_value=session.active_task.pending_tool_call
    )
    app_state.state_manager.get_session = AsyncMock(side_effect=[session, session, _make_session(task=None)])
    app_state.harness.execute = AsyncMock(
        return_value=SimpleNamespace(
            status="SUCCESS",
            data={"booking_id": "bk_123"},
            reason=None,
            error=None,
        )
    )

    await _process_message(
        request_id="req-2",
        session_id=session.session_id,
        user_message="yes",
        app_state=app_state,
    )

    app_state.state_manager.clear_task.assert_called_once_with(session.session_id)


@pytest.mark.asyncio
async def test_continue_task_with_slot_update_still_runs_planner():
    session = _make_session(task=_make_task())
    app_state = _make_app_state(session)
    updated_session = _make_session(task=_make_task())
    app_state.state_manager.update_slots.return_value = updated_session
    app_state.router_agent.classify.return_value = RouterDecision(
        capability="facility_booking",
        intent_class="continue_task",
        confidence=0.99,
        extracted_slots={"start_time": "20:00"},
    )

    await _process_message(
        request_id="req-1",
        session_id=session.session_id,
        user_message="actually make it 8pm instead",
        app_state=app_state,
    )

    app_state.state_manager.update_slots.assert_called_once_with(session.session_id, {"start_time": "20:00"})
    app_state.facility_planner.run.assert_called_once()
