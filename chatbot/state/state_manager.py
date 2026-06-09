from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import WatchError

from chatbot.config import settings
from chatbot.state.schemas import (
    ConversationSession,
    SlotState,
    TaskContext,
    ToolCallRequest,
    UserInfo,
)


class ConcurrentWriteError(RuntimeError):
    pass


class SessionNotFoundError(KeyError):
    pass


class StateManager:
    def __init__(self, redis: Redis, metrics=None):
        self.redis = redis
        self.metrics = metrics

    @staticmethod
    def _pending_confirmation_key(user_id: str) -> str:
        return f"pending_confirmation:{user_id}"

    @staticmethod
    def _now_ts() -> int:
        return int(time.time())

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"session:{session_id}"

    @staticmethod
    def _user_session_key(user_id: str) -> str:
        return f"user_session:{user_id}"

    async def _write_session(self, session: ConversationSession) -> None:
        await self.redis.set(
            self._session_key(session.session_id),
            session.model_dump_json(),
            ex=settings.SESSION_INACTIVITY_TTL,
        )
        await self.redis.set(
            self._user_session_key(session.user.user_id),
            session.session_id,
            ex=settings.SESSION_HARD_TTL,
        )

    def _ensure_not_hard_expired(self, session: ConversationSession) -> None:
        if self._now_ts() - session.created_at > settings.SESSION_HARD_TTL:
            raise SessionNotFoundError(f"Session {session.session_id} exceeded hard TTL")

    async def _atomic_session_write(
        self,
        session_id: str,
        modifier_fn: Callable[[ConversationSession], ConversationSession],
        max_retries: int = 3,
    ) -> ConversationSession:
        key = self._session_key(session_id)
        for _ in range(max_retries):
            async with self.redis.pipeline() as pipe:
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        raise SessionNotFoundError(f"Session {session_id} not found")
                    session = ConversationSession.model_validate_json(raw)
                    self._ensure_not_hard_expired(session)
                    modified = modifier_fn(session)
                    modified.last_activity_at = self._now_ts()
                    pipe.multi()
                    pipe.set(key, modified.model_dump_json(), ex=settings.SESSION_INACTIVITY_TTL)
                    pipe.set(
                        self._user_session_key(modified.user.user_id),
                        modified.session_id,
                        ex=settings.SESSION_HARD_TTL,
                    )
                    await pipe.execute()
                    return modified
                except WatchError:
                    if self.metrics is not None:
                        self.metrics.increment_session_write_retry()
                    continue
        raise ConcurrentWriteError("Session write failed after max retries")

    async def get_session(self, session_id: str) -> ConversationSession:
        raw = await self.redis.get(self._session_key(session_id))
        if raw is None:
            raise SessionNotFoundError(f"Session {session_id} not found")
        session = ConversationSession.model_validate_json(raw)
        self._ensure_not_hard_expired(session)
        return session

    @staticmethod
    def _clear_confirmation(task: TaskContext) -> None:
        task.awaiting_confirmation = False
        task.pending_tool_call = None
        task.confirmation_token = None
        task.confirmation_turns = 0

    def _apply_slot_cascade(self, task: TaskContext, slot_updates: dict) -> None:
        slots = task.slots.model_copy(deep=True)
        validation = task.slot_validation.model_copy(deep=True)
        clear_confirmation = False
        blocked_fields: set[str] = set()

        if "facility_name" in slot_updates and slot_updates["facility_name"] != task.slots.facility_name:
            slots.facility_name = slot_updates["facility_name"]
            slots.facility_id = None
            slots.open_time = None
            slots.close_time = None
            blocked_fields.update({"facility_id", "open_time", "close_time"})
            validation.facility_valid = False
            validation.date_valid = False
            validation.time_valid = False
            validation.slot_available = False
            clear_confirmation = True

        if "date" in slot_updates and slot_updates["date"] != task.slots.date:
            slots.date = slot_updates["date"]
            validation.date_valid = False
            validation.time_valid = False
            validation.slot_available = False
            clear_confirmation = True

        if "start_time" in slot_updates and slot_updates["start_time"] != task.slots.start_time:
            slots.start_time = slot_updates["start_time"]
            slots.end_time = None
            blocked_fields.add("end_time")
            validation.time_valid = False
            validation.slot_available = False
            clear_confirmation = True

        if (
            "duration_minutes" in slot_updates
            and slot_updates["duration_minutes"] != task.slots.duration_minutes
        ):
            slots.duration_minutes = slot_updates["duration_minutes"]
            slots.end_time = None
            blocked_fields.add("end_time")
            validation.slot_available = False
            clear_confirmation = True

        for field, value in slot_updates.items():
            if field not in {"facility_name", "date", "start_time", "duration_minutes"} and field not in blocked_fields:
                setattr(slots, field, value)

        task.slots = slots
        task.slot_validation = validation
        task.last_updated_at = self._now_ts()

        if clear_confirmation:
            self._clear_confirmation(task)

    async def create_session(self, user: UserInfo) -> ConversationSession:
        now = self._now_ts()
        session = ConversationSession(
            session_id=str(uuid.uuid4()),
            user=user,
            active_task=None,
            stashed_task=None,
            suspended_tasks=[],
            message_history=[],
            intent_at_last_turn=None,
            message_count=0,
            consecutive_unclear_count=0,
            no_progress_turns=0,
            created_at=now,
            last_activity_at=now,
        )
        await self._write_session(session)
        return session

    async def get_expired_confirmation_recovery(self, user_id: str) -> dict | None:
        key = self._pending_confirmation_key(user_id)
        raw = await self.redis.get(key)
        if raw is None:
            return None

        data = json.loads(raw)
        session_id = data.get("session_id")
        if session_id:
            session_raw = await self.redis.get(self._session_key(session_id))
            if session_raw is not None:
                try:
                    session = ConversationSession.model_validate_json(session_raw)
                    if self._now_ts() - session.created_at <= settings.SESSION_HARD_TTL:
                        return None
                except Exception:
                    pass

        await self.redis.delete(key)
        return data

    async def init_task(
        self,
        session_id: str,
        capability: str,
        slots: dict,
        prompt_version: str,
    ) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            now = self._now_ts()
            session.active_task = TaskContext(
                task_id=str(uuid.uuid4()),
                capability=capability,
                prompt_version=prompt_version,
                planner_memory=[],
                history_summary=None,
                slots=SlotState(**slots),
                turn_count=0,
                created_at=now,
                last_updated_at=now,
            )
            session.intent_at_last_turn = capability
            return session

        return await self._atomic_session_write(session_id, modifier)

    async def restore_task(self, session_id: str, task_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            for index in range(len(session.suspended_tasks) - 1, -1, -1):
                if session.suspended_tasks[index].task_id == task_id:
                    session.active_task = session.suspended_tasks.pop(index)
                    session.active_task.last_updated_at = self._now_ts()
                    return session
            raise KeyError(f"Task {task_id} not found")

        return await self._atomic_session_write(session_id, modifier)

    async def suspend_task(self, session_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.active_task is not None:
                session.suspended_tasks.append(session.active_task)
                session.suspended_tasks = session.suspended_tasks[-3:]
                session.active_task = None
            return session

        return await self._atomic_session_write(session_id, modifier)

    async def stash_task(self, session_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            session.stashed_task = session.active_task
            session.active_task = None
            return session

        return await self._atomic_session_write(session_id, modifier)

    async def restore_stashed_task(self, session_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            session.active_task = session.stashed_task
            session.stashed_task = None
            if session.active_task is not None:
                session.active_task.last_updated_at = self._now_ts()
            return session

        return await self._atomic_session_write(session_id, modifier)

    async def restore_latest_task(self, session_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.stashed_task is not None:
                session.active_task = session.stashed_task
                session.stashed_task = None
            elif session.suspended_tasks:
                session.active_task = session.suspended_tasks.pop()
            else:
                session.active_task = None
            if session.active_task is not None:
                session.active_task.last_updated_at = self._now_ts()
            return session

        return await self._atomic_session_write(session_id, modifier)

    async def update_slots(self, session_id: str, slot_updates: dict) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.active_task is None:
                raise KeyError("No active task to update")
            self._apply_slot_cascade(session.active_task, slot_updates)
            return session

        session = await self._atomic_session_write(session_id, modifier)
        if session.active_task is None or not session.active_task.awaiting_confirmation:
            await self.redis.delete(self._pending_confirmation_key(session.user.user_id))
        return session

    async def set_awaiting_confirmation(
        self,
        session_id: str,
        tool_call: ToolCallRequest,
        token: str,
    ) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.active_task is None:
                raise KeyError("No active task to update")
            session.active_task.awaiting_confirmation = True
            session.active_task.pending_tool_call = tool_call
            session.active_task.confirmation_token = token
            session.active_task.confirmation_turns = 0
            session.active_task.last_updated_at = self._now_ts()
            return session

        session = await self._atomic_session_write(session_id, modifier)
        if session.active_task is not None:
            await self.redis.set(
                self._pending_confirmation_key(session.user.user_id),
                json.dumps(
                    {
                        "session_id": session.session_id,
                        "task_id": session.active_task.task_id,
                        "user_id": session.user.user_id,
                        "community_id": session.user.community_id,
                        "tool_name": tool_call.tool_name,
                        "params": tool_call.params,
                        "created_at": self._now_ts(),
                        "prompt_version": session.active_task.prompt_version,
                    }
                ),
                ex=settings.SESSION_HARD_TTL * 2,
            )
        return session

    async def release_confirmation(self, session_id: str, token: str) -> ToolCallRequest:
        released_call: ToolCallRequest | None = None

        def modifier(session: ConversationSession) -> ConversationSession:
            nonlocal released_call
            if session.active_task is None or session.active_task.pending_tool_call is None:
                raise KeyError("No pending tool call found")
            if session.active_task.confirmation_token != token:
                raise PermissionError("Confirmation token mismatch")
            released_call = session.active_task.pending_tool_call
            self._clear_confirmation(session.active_task)
            session.active_task.last_updated_at = self._now_ts()
            return session

        session = await self._atomic_session_write(session_id, modifier)
        await self.redis.delete(self._pending_confirmation_key(session.user.user_id))
        if released_call is None:
            raise KeyError("No pending tool call found")
        return released_call

    async def reject_confirmation(self, session_id: str, user_message: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.active_task is None:
                raise KeyError("No active task found")
            task = session.active_task
            rejected_tool = task.pending_tool_call.tool_name if task.pending_tool_call else "pending_action"
            self._clear_confirmation(task)
            task.planner_memory.append(
                {
                    "role": "user",
                    "content": (
                        f"User rejected the pending action for {rejected_tool}: {user_message}. "
                        "Adjust the plan and ask a follow-up question if needed."
                    ),
                }
            )
            task.last_updated_at = self._now_ts()
            return session

        session = await self._atomic_session_write(session_id, modifier)
        await self.redis.delete(self._pending_confirmation_key(session.user.user_id))
        return session

    async def bump_confirmation_turns(self, session_id: str) -> tuple[ConversationSession, bool]:
        expired = False

        def modifier(session: ConversationSession) -> ConversationSession:
            nonlocal expired
            if session.active_task is None or not session.active_task.awaiting_confirmation:
                return session
            session.active_task.confirmation_turns += 1
            if session.active_task.confirmation_turns >= settings.CONFIRMATION_TIMEOUT_TURNS:
                expired = True
                self._clear_confirmation(session.active_task)
            session.active_task.last_updated_at = self._now_ts()
            return session

        session = await self._atomic_session_write(session_id, modifier)
        if expired:
            await self.redis.delete(self._pending_confirmation_key(session.user.user_id))
        return session, expired

    async def clear_task(self, session_id: str) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            session.active_task = None
            return session

        session = await self._atomic_session_write(session_id, modifier)
        await self.redis.delete(self._pending_confirmation_key(session.user.user_id))
        return session

    async def update_planner_memory(
        self,
        session_id: str,
        planner_memory: list[dict],
        history_summary: str | None,
    ) -> ConversationSession:
        def modifier(session: ConversationSession) -> ConversationSession:
            if session.active_task is None:
                raise KeyError('No active task to update planner memory')
            session.active_task.planner_memory = planner_memory
            session.active_task.history_summary = history_summary
            session.active_task.last_updated_at = self._now_ts()
            return session

        return await self._atomic_session_write(session_id, modifier)
