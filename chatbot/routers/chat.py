from __future__ import annotations

from datetime import datetime, timedelta
import json
import time
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from pydantic import BaseModel, ConfigDict, field_validator

from chatbot.agents.planners.base_planner import PlannerOutput
from chatbot.config import settings
from chatbot.middleware.auth_middleware import require_session_id_dep
from chatbot.observability.context import bind_log_context, clear_log_context
from chatbot.state.schemas import ChatMessage, HarnessContext
from chatbot.tools.facility_tools import get_facility_availability

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_message: str

    @field_validator("user_message")
    @classmethod
    def validate_user_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("user_message must not be empty")
        return cleaned


class ChatAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    poll_url: str


async def _process_message(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    app_state,
    request_context: dict | None = None,
) -> None:
    clear_log_context()
    if request_context:
        bind_log_context(**request_context)
    bind_log_context(component="chat", session_id=session_id, async_request_id=request_id)
    redis = app_state.redis
    state_manager = app_state.state_manager
    router_agent = app_state.router_agent
    facility_planner = app_state.facility_planner
    preferences_manager = app_state.preferences_manager
    synthesizer = app_state.synthesizer
    harness = app_state.harness
    db_pool = app_state.db_pool

    try:
        planner_output: PlannerOutput | None = None
        session = await state_manager.get_session(session_id)
        user = session.user

        preferences = await preferences_manager.get_all(db_pool, redis, user.user_id)
        try:
            facility_catalog = await _get_cached_facilities(
                redis, app_state.api_adapter, user.community_id
            )
        except Exception as exc:
            logger.warning("facility_catalog_unavailable_for_router", error=str(exc))
            facility_catalog = None

        correlation_id = (
            request_context.get("correlation_id")
            if request_context and request_context.get("correlation_id")
            else str(uuid4())
        )
        task = session.active_task
        prompt_version = task.prompt_version if task else "facility_planner_v1.0"
        capability = task.capability if task else "facility_booking"
        task_id = task.task_id if task else ""

        harness_context = HarnessContext(
            user_id=user.user_id,
            user_email=user.email,
            community_id=user.community_id,
            capability=capability,
            task_id=task_id,
            session_id=session_id,
            correlation_id=correlation_id,
            user_role=user.role,
            prompt_version=prompt_version,
        )

        decision = await router_agent.classify(
            session,
            user_message,
            facility_catalog=facility_catalog,
        )

        response_text: str

        if (
            task is not None
            and task.awaiting_confirmation
            and decision.intent_class not in ("confirmation", "rejection")
        ):
            session, confirmation_expired = await state_manager.bump_confirmation_turns(session_id)
            if confirmation_expired:
                response_text = (
                    "I cancelled the pending confirmation because it was left unanswered. "
                    "We can continue the task if you'd like."
                )
                await state_manager._atomic_session_write(
                    session_id,
                    lambda s: _append_messages(s, user_message, response_text),
                )
                _fresh = await state_manager.get_session(session_id)
                _hints = await _build_ui_hints(
                    _fresh,
                    planner_output,
                    redis,
                    app_state.api_adapter,
                )
                result_payload = json.dumps({"status": "done", "response": response_text, "ui_hints": _hints})
                await redis.set(f"request:{request_id}", result_payload, ex=settings.REQUEST_RESULT_TTL)
                return
            task = session.active_task

        if decision.intent_class == "small_talk":
            response_text = "I'm here to help with facility bookings. What would you like to do?"

        elif decision.intent_class == "unclear":
            session = await state_manager._atomic_session_write(
                session_id,
                lambda s: _bump_unclear(s),
            )
            count = session.consecutive_unclear_count
            if count >= settings.MAX_CONSECUTIVE_UNCLEAR:
                response_text = "I'm having trouble understanding. Please contact support or try rephrasing your request."
            else:
                response_text = "I'm not sure what you're asking. Could you rephrase that?"

        elif (
            decision.intent_class == "confirmation"
            and task is not None
            and task.awaiting_confirmation
            and task.confirmation_token is not None
        ):
            try:
                pending_call = await state_manager.release_confirmation(
                    session_id, task.confirmation_token
                )
                # Refresh session + context after releasing confirmation
                session = await state_manager.get_session(session_id)
                harness_context = harness_context.model_copy(update={"task_id": pending_call.task_id})
                result = await harness.execute(pending_call, harness_context, pre_confirmed=True)
                if result.status == "SUCCESS":
                    task_after = session.active_task
                    if pending_call.tool_name == "create_booking":
                        booking_id = (result.data or {}).get("booking_id", "N/A") if isinstance(result.data, dict) else "N/A"
                        response_text = f"Done! Your booking is confirmed. Booking ID: {booking_id}."
                    elif pending_call.tool_name == "cancel_booking":
                        cancelled_booking_id = (result.data or {}).get("cancelled_booking_id", "N/A") if isinstance(result.data, dict) else "N/A"
                        response_text = f"Done! Your booking has been cancelled. Booking ID: {cancelled_booking_id}."
                    else:
                        response_text = "Done! The action is complete."
                    if task_after and task_after.slots.facility_name:
                        await preferences_manager.upsert(
                            db_pool, redis, user.user_id, user.community_id,
                            "preferred_facility", task_after.slots.facility_name, 0.9,
                        )
                    if task_after and task_after.slots.start_time:
                        await preferences_manager.upsert(
                            db_pool, redis, user.user_id, user.community_id,
                            "preferred_time", task_after.slots.start_time, 0.8,
                        )
                    session = await state_manager.clear_task(session_id)
                elif result.status == "UNCERTAIN_STATE":
                    response_text = result.error or "The action may or may not have completed. Please check your bookings."
                else:
                    response_text = result.reason or result.error or "The action could not be completed."
            except (KeyError, PermissionError) as exc:
                response_text = "I couldn't find a pending action to confirm. Please try again."
                logger.warning("confirmation_release_failed", error=str(exc))

        elif (
            decision.intent_class == "rejection"
            and task is not None
            and task.awaiting_confirmation
        ):
            session = await state_manager.reject_confirmation(session_id, user_message)
            task = session.active_task
            if task:
                harness_context = harness_context.model_copy(
                    update={"task_id": task.task_id, "capability": task.capability}
                )
            planner_output = await facility_planner.run(session, user_message, harness_context, preferences)
            response_text = synthesizer.synthesize(planner_output)

        elif decision.intent_class == "new_task":
            cap = decision.capability if decision.capability != "none" else "facility_booking"
            session = await state_manager.init_task(
                session_id,
                capability=cap,
                slots={k: v for k, v in decision.extracted_slots.items() if v is not None},
                prompt_version="facility_planner_v1.0",
            )
            # Refresh harness context with new task_id
            new_task = session.active_task
            if new_task:
                harness_context = harness_context.model_copy(
                    update={"task_id": new_task.task_id, "capability": new_task.capability}
                )
            planner_output = await facility_planner.run(session, user_message, harness_context, preferences)
            response_text = synthesizer.synthesize(planner_output)

        elif decision.intent_class in ("continue_task", "side_question"):
            if decision.intent_class == "side_question":
                if task is not None:
                    await state_manager.stash_task(session_id)
                response_text = await _answer_side_question(
                    redis,
                    app_state.api_adapter,
                    session,
                    user_message,
                )
                await state_manager.restore_latest_task(session_id)
            elif decision.extracted_slots:
                session = await state_manager.update_slots(session_id, decision.extracted_slots)
            else:
                session = await state_manager.get_session(session_id)
            if decision.intent_class == "continue_task":
                if session.active_task is None:
                    session = await state_manager.init_task(
                        session_id,
                        capability="facility_booking",
                        slots={k: v for k, v in decision.extracted_slots.items() if v is not None},
                        prompt_version="facility_planner_v1.0",
                    )
                new_task = session.active_task
                if new_task:
                    harness_context = harness_context.model_copy(
                        update={"task_id": new_task.task_id, "capability": new_task.capability}
                    )
                planner_output = await facility_planner.run(session, user_message, harness_context, preferences)
                response_text = synthesizer.synthesize(planner_output)

        elif decision.intent_class == "switch_task":
            if task is not None:
                await state_manager.suspend_task(session_id)
            cap = decision.capability if decision.capability != "none" else "facility_booking"
            session = await state_manager.init_task(
                session_id,
                capability=cap,
                slots={k: v for k, v in decision.extracted_slots.items() if v is not None},
                prompt_version="facility_planner_v1.0",
            )
            new_task = session.active_task
            if new_task:
                harness_context = harness_context.model_copy(
                    update={"task_id": new_task.task_id, "capability": new_task.capability}
                )
            planner_output = await facility_planner.run(session, user_message, harness_context, preferences)
            response_text = synthesizer.synthesize(planner_output)

        elif decision.intent_class == "cancel_task":
            await state_manager.clear_task(session_id)
            response_text = "Task cancelled. Is there anything else I can help you with?"

        elif decision.intent_class == "resume_task":
            session = await state_manager.restore_latest_task(session_id)
            restored_task = session.active_task
            if restored_task:
                harness_context = harness_context.model_copy(
                    update={"task_id": restored_task.task_id, "capability": restored_task.capability}
                )
                planner_output = await facility_planner.run(session, user_message, harness_context, preferences)
                response_text = synthesizer.synthesize(planner_output)
            else:
                response_text = "There's no previous task to resume. What would you like to do?"

        else:
            response_text = "I'm not sure how to handle that. Please try again."

        # Append messages to session history
        await state_manager._atomic_session_write(
            session_id,
            lambda s: _append_messages(s, user_message, response_text),
        )

        _fresh = await state_manager.get_session(session_id)
        _hints = await _build_ui_hints(
            _fresh,
            planner_output,
            redis,
            app_state.api_adapter,
        )
        result_payload = json.dumps({"status": "done", "response": response_text, "ui_hints": _hints})
        await redis.set(f"request:{request_id}", result_payload, ex=settings.REQUEST_RESULT_TTL)

    except Exception as exc:
        logger.error("chat_pipeline_error", request_id=request_id, error=str(exc), exc_info=True)
        error_payload = json.dumps({"status": "error", "error": "An internal error occurred. Please try again."})
        await redis.set(f"request:{request_id}", error_payload, ex=settings.REQUEST_RESULT_TTL)
    finally:
        clear_log_context()


def _bump_unclear(session):
    session.consecutive_unclear_count += 1
    return session


def _compute_ui_hints(session, planner_output: PlannerOutput | None = None) -> dict:
    if planner_output is None or planner_output.type != "user_question":
        return {}

    task = session.active_task
    if task is None:
        return {}
    slots = task.slots
    if slots.date is None:
        return {"type": "date_picker_inline", "submit_prefix": "Set date to "}
    if slots.start_time is None:
        return {
            "type": "time_picker_inline",
            "current_date": slots.date,
            "submit_prefix": "Set time to ",
        }
    return {
        "type": "time_change_pill",
        "current_date": slots.date,
        "current_time": slots.start_time,
        "submit_prefix": "Change time to ",
    }


def _time_slots(open_time: str, close_time: str, duration_min: int) -> list[str]:
    fmt = "%H:%M"
    start = datetime.strptime(open_time, fmt)
    end = datetime.strptime(close_time, fmt)
    delta = timedelta(minutes=duration_min)
    slots: list[str] = []
    current = start
    while current + delta <= end:
        slots.append(current.strftime(fmt))
        current += delta
    return slots


async def _build_ui_hints(
    session,
    planner_output: PlannerOutput | None,
    redis,
    api_adapter,
) -> dict:
    base_hint = _compute_ui_hints(session, planner_output)
    if not base_hint:
        return {}

    if base_hint["type"] == "date_picker_inline":
        return base_hint

    task = session.active_task
    if task is None or task.slots.date is None:
        return {}

    try:
        facilities = await _get_cached_facilities(redis, api_adapter, session.user.community_id)
        facility = _find_facility(facilities, task.slots.facility_name, task.slots.facility_id)
        facility_id = task.slots.facility_id or (
            str(facility.get("id") or facility.get("facility_id")) if facility else None
        )
        if not facility_id:
            return {}

        availability = await get_facility_availability(api_adapter, facility_id, task.slots.date)
        open_time = task.slots.open_time or availability.get("open_time") or (
            facility.get("open_time") if facility else None
        )
        close_time = task.slots.close_time or availability.get("close_time") or (
            facility.get("close_time") if facility else None
        )
        duration_minutes = task.slots.duration_minutes or availability.get("default_duration_min") or (
            facility.get("default_duration_min") if facility else None
        )
        if not open_time or not close_time or not duration_minutes:
            return {}

        available_slots = set(availability.get("available_slots") or [])
        selected_time = task.slots.start_time
        slot_entries = []
        for slot_time in _time_slots(open_time, close_time, int(duration_minutes)):
            status = "available"
            selectable = True
            if selected_time and slot_time == selected_time:
                status = "selected"
            elif slot_time not in available_slots:
                status = "unavailable"
                selectable = False
            slot_entries.append(
                {
                    "time": slot_time,
                    "status": status,
                    "selectable": selectable,
                }
            )

        return {
            **base_hint,
            "facility_id": facility_id,
            "facility_name": task.slots.facility_name or (
                facility.get("facility_name") or facility.get("name") if facility else None
            ),
            "current_time": selected_time,
            "open_time": open_time,
            "close_time": close_time,
            "duration_minutes": int(duration_minutes),
            "slots": slot_entries,
        }
    except Exception as exc:
        logger.warning("ui_hint_build_failed", error=str(exc))
        return {}


def _append_messages(session, user_msg: str, bot_msg: str):
    now = int(time.time())
    session.message_history.append(ChatMessage(role="user", content=user_msg, ts=now))
    session.message_history.append(ChatMessage(role="bot", content=bot_msg, ts=now))
    session.message_count += 2
    session.consecutive_unclear_count = 0
    return session


async def _get_cached_facilities(redis, api_adapter, community_id: str) -> list[dict]:
    cache_key = f"facilities:{community_id}"
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    facilities = await api_adapter.get_facility_list()
    await redis.set(cache_key, json.dumps(facilities), ex=settings.FACILITY_LIST_CACHE_TTL)
    return facilities


def _find_facility(facilities: list[dict], name: str | None, facility_id: str | None) -> dict | None:
    for facility in facilities:
        if facility_id and str(facility.get("id") or facility.get("facility_id")) == facility_id:
            return facility
        facility_name = facility.get("facility_name") or facility.get("name")
        if name and facility_name and facility_name.lower() == name.lower():
            return facility
    return None


def _find_facilities_by_category(facilities: list[dict], user_message: str) -> list[dict]:
    lowered = user_message.lower()
    matches: list[dict] = []
    for facility in facilities:
        category = facility.get("category")
        if isinstance(category, str) and category.lower() in lowered:
            matches.append(facility)
    return matches


async def _answer_side_question(redis, api_adapter, session, user_message: str) -> str:
    lowered = user_message.lower()
    if "bk_" in lowered and "booking" in lowered:
        bookings = await api_adapter.get_my_bookings()
        items: list[dict] = []
        if isinstance(bookings, dict):
            items = list((bookings.get("upcoming_bookings") or [])) + list((bookings.get("past_bookings") or []))
            if not items:
                items = bookings.get("bookings") or bookings.get("data") or []
        booking_id = None
        for token in user_message.replace(".", " ").replace(",", " ").split():
            if token.startswith("bk_"):
                booking_id = token
                break
        if booking_id:
            match = next(
                (
                    item for item in items
                    if isinstance(item, dict) and item.get("booking_id") == booking_id
                ),
                None,
            )
            if match:
                facility_name = match.get("facility_name") or match.get("facility_id") or "that facility"
                date = match.get("date") or "an unknown date"
                start_time = match.get("start_time") or "an unknown time"
                status = match.get("status") or "Confirmed"
                return (
                    f"Booking {booking_id} is for {facility_name} on {date} at {start_time}. "
                    f"Status: {status}. We can continue your earlier task whenever you're ready."
                )
            return f"I couldn't find booking {booking_id} in your current bookings."

    if any(phrase in lowered for phrase in ("my bookings", "my booking", "my reservations")):
        bookings = await api_adapter.get_my_bookings()
        if isinstance(bookings, dict):
            upcoming = bookings.get("upcoming_bookings") or []
            past = bookings.get("past_bookings") or []
            items = upcoming + past
            if not items:
                items = bookings.get("bookings") or bookings.get("data") or []
        else:
            items = bookings
        if not items:
            return "You do not have any active bookings right now."
        booking_ids = [item.get("booking_id") for item in items if isinstance(item, dict) and item.get("booking_id")]
        if booking_ids:
            return (
                f"You currently have {len(items)} booking(s): " + ", ".join(booking_ids[:5]) +
                ". We can go back to your earlier task whenever you're ready."
            )
        return f"You currently have {len(items)} booking(s). We can go back to your earlier task whenever you're ready."

    facilities = await _get_cached_facilities(redis, api_adapter, session.user.community_id)

    category_matches = _find_facilities_by_category(facilities, user_message)
    if category_matches:
        names = [facility.get("facility_name") or facility.get("name") for facility in category_matches]
        names = [name for name in names if name]
        category = category_matches[0].get("category")
        if names and isinstance(category, str):
            return (
                f"For {category.lower()}, you can use: " + ", ".join(names[:6]) +
                ". We can continue your earlier task whenever you're ready."
            )

    if any(phrase in lowered for phrase in ("what facilities", "which facilities", "available facilities", "amenities")):
        names = [facility.get("facility_name") or facility.get("name") for facility in facilities]
        names = [name for name in names if name]
        if names:
            return "Available facilities: " + ", ".join(names[:6]) + ". We can continue your earlier task whenever you're ready."

    if any(phrase in lowered for phrase in ("timing", "timings", "hours", "open", "close")):
        task = session.active_task or session.stashed_task
        if task is not None:
            facility = _find_facility(facilities, task.slots.facility_name, task.slots.facility_id)
            facility_name = task.slots.facility_name or (facility.get("facility_name") or facility.get("name") if facility else "that facility")
            open_time = task.slots.open_time or (facility.get("open_time") if facility else None)
            close_time = task.slots.close_time or (facility.get("close_time") if facility else None)
            default_duration = task.slots.duration_minutes or (facility.get("default_duration_min") if facility else None)
            if open_time and close_time:
                duration_text = f" Default slot duration is {default_duration} minutes." if default_duration else ""
                return f"{facility_name} is open from {open_time} to {close_time}.{duration_text} We can continue your earlier task whenever you're ready."

    return "I can answer quick facility questions and then continue your earlier task. What would you like to know?"


@router.post("/message", status_code=status.HTTP_202_ACCEPTED, response_model=ChatAcceptedResponse)
async def post_message(
    payload: ChatMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session_id: str = Depends(require_session_id_dep),
) -> ChatAcceptedResponse:
    request_id = str(uuid4())
    bind_log_context(component="chat", session_id=session_id, poll_request_id=request_id)
    await request.app.state.redis.set(
        f"request:{request_id}",
        '{"status":"processing"}',
        ex=settings.REQUEST_RESULT_TTL,
    )
    background_tasks.add_task(
        _process_message,
        request_id=request_id,
        session_id=session_id,
        user_message=payload.user_message,
        app_state=request.app.state,
        request_context={
            "request_id": getattr(request.state, "request_id", None),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )
    return ChatAcceptedResponse(request_id=request_id, poll_url=f"/chat/status/{request_id}")


@router.get("/status/{request_id}")
async def get_status(request_id: str, request: Request) -> dict:
    raw = await request.app.state.redis.get(f"request:{request_id}")
    if raw is None:
        return {"status": "not_found"}
    return json.loads(raw)
