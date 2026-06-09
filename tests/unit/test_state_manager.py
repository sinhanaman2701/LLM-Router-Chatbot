from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import WatchError

from chatbot.config import settings
from chatbot.state.schemas import ConversationSession, TaskContext, ToolCallRequest, UserInfo
from chatbot.state.state_manager import StateManager


def _make_session() -> ConversationSession:
    now = int(time.time())
    return ConversationSession(
        session_id="sess-1",
        user=UserInfo(
            user_id="user-1",
            community_id="comm-1",
            email="resident@example.com",
            unit_id="flat101",
            role="resident",
        ),
        created_at=now,
        last_activity_at=now,
    )


def _make_task(task_id: str) -> TaskContext:
    return TaskContext(
        task_id=task_id,
        capability="facility_booking",
        prompt_version="facility_planner_v1.0",
        created_at=1748000000,
        last_updated_at=1748000000,
    )


class _Pipeline:
    def __init__(self, session: ConversationSession, execute_side_effects):
        self._session = session
        self._execute_side_effects = iter(execute_side_effects)
        self.watch = AsyncMock()
        self.get = AsyncMock(side_effect=self._get)
        self.execute = AsyncMock(side_effect=self._execute)
        self.multi = MagicMock()
        self.set = MagicMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def _get(self, key):
        return self._session.model_dump_json()

    async def _execute(self):
        effect = next(self._execute_side_effects)
        if isinstance(effect, Exception):
            raise effect
        return [True]


@pytest.mark.asyncio
async def test_atomic_session_write_retries_after_watch_error():
    session = _make_session()
    pipeline = _Pipeline(session, [WatchError(), None])
    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=[pipeline, pipeline])

    manager = StateManager(redis)
    manager._now_ts = MagicMock(return_value=session.created_at + 1)
    updated = await manager._atomic_session_write(
        session.session_id,
        lambda s: s.model_copy(update={"message_count": 1}),
    )

    assert updated.message_count == 1
    assert pipeline.watch.await_count == 2


@pytest.mark.asyncio
async def test_restore_latest_task_prefers_stashed_then_suspended():
    session = _make_session()
    session.stashed_task = _make_task("stashed-1")
    session.suspended_tasks = [_make_task("suspended-1")]

    redis = MagicMock()
    manager = StateManager(redis)
    manager._atomic_session_write = AsyncMock(
        side_effect=lambda session_id, modifier: modifier(session)
    )

    restored = await manager.restore_latest_task(session.session_id)
    assert restored.active_task is not None
    assert restored.active_task.task_id == "stashed-1"
    assert restored.stashed_task is None


@pytest.mark.asyncio
async def test_set_awaiting_confirmation_persists_pending_marker():
    session = _make_session()
    session.active_task = _make_task("task-1")
    redis = MagicMock()
    redis.set = AsyncMock()
    manager = StateManager(redis)
    manager._atomic_session_write = AsyncMock(return_value=session)

    await manager.set_awaiting_confirmation(
        session.session_id,
        ToolCallRequest(
            tool_name="create_booking",
            params={"facility_id": "fac_1"},
            requested_by="planner",
            task_id="task-1",
        ),
        "tok-1",
    )

    marker = json.loads(redis.set.call_args.args[1])
    assert marker["tool_name"] == "create_booking"
    assert marker["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_get_expired_confirmation_recovery_returns_only_after_session_expiry():
    session = _make_session()
    expired_session = session.model_copy(
        update={"created_at": session.created_at - settings.SESSION_HARD_TTL - 1}
    )
    redis = MagicMock()
    redis.get = AsyncMock(side_effect=[
        json.dumps({"session_id": session.session_id, "tool_name": "create_booking"}),
        session.model_dump_json(),
        json.dumps({"session_id": session.session_id, "tool_name": "create_booking"}),
        expired_session.model_dump_json(),
    ])
    redis.delete = AsyncMock()

    manager = StateManager(redis)
    manager._now_ts = MagicMock(return_value=session.created_at + 10)
    fresh = await manager.get_expired_confirmation_recovery(session.user.user_id)
    assert fresh is None

    manager._now_ts = MagicMock(return_value=expired_session.created_at + settings.SESSION_HARD_TTL + 10)
    expired = await manager.get_expired_confirmation_recovery(session.user.user_id)
    assert expired is not None
    redis.delete.assert_called_once()
