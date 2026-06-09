from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.agents.harness.audit_logger import AuditLogger


class _AcquireContext:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_audit_logger_executes_insert_with_all_placeholders():
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireContext(conn))

    logger = AuditLogger(pool)
    await logger.log(
        tool_run_id="00000000-0000-0000-0000-000000000001",
        tool_name="create_booking",
        params_json={"facility_id": "fac_1"},
        result_json={"booking_id": "bk_1"},
        context={
            "session_id": "sess-1",
            "task_id": "task-1",
            "user_id": "00000000-0000-0000-0000-000000000002",
            "community_id": "00000000-0000-0000-0000-000000000003",
            "prompt_version": "facility_planner_v1.0",
        },
        status="SUCCESS",
        policy_rule_id="always_confirm_booking",
        latency_ms=125,
        prompt_version="facility_planner_v1.0",
        pre_confirmed=True,
    )

    conn.execute.assert_called_once()
    sql = conn.execute.call_args.args[0]
    assert "$13" in sql
    assert "VALUES (" in sql
    assert conn.execute.call_args.args[5] == json.dumps({"facility_id": "fac_1"})
    assert conn.execute.call_args.args[6] == json.dumps({"booking_id": "bk_1"})
