from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.agents.harness.policy_store import PolicyStore


class _AcquireContext:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _make_store():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": "always_confirm_booking",
            "capability": "facility_booking",
            "tool_name": "create_booking",
            "conditions_json": [],
            "action": "REQUIRE_CONFIRMATION",
            "risk_level": "HIGH",
            "reason": "Bookings always require explicit user confirmation.",
            "active": True,
            "version": 1,
        }
    )
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireContext(conn))
    redis = MagicMock()
    redis.delete = AsyncMock()
    return PolicyStore(pool, redis), conn


@pytest.mark.asyncio
async def test_policy_store_rejects_non_admin_updates():
    store, conn = _make_store()

    with pytest.raises(PermissionError):
        await store.update_rule(
            actor_user_id="00000000-0000-0000-0000-000000000001",
            actor_role="resident",
            rule_id="always_confirm_booking",
            new_value={"active": False},
            change_reason="test",
        )

    assert conn.execute.await_count == 1
    assert "INSERT INTO policy_change_log" in conn.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_policy_store_requires_second_super_admin_for_critical_changes():
    store, conn = _make_store()

    with pytest.raises(PermissionError):
        await store.update_rule(
            actor_user_id="00000000-0000-0000-0000-000000000001",
            actor_role="community_admin",
            rule_id="always_confirm_booking",
            new_value={"active": False},
            change_reason="turn off confirmation",
        )

    assert conn.execute.await_count == 1


@pytest.mark.asyncio
async def test_policy_store_updates_and_invalidates_cache():
    store, conn = _make_store()

    updated = await store.update_rule(
        actor_user_id="00000000-0000-0000-0000-000000000001",
        actor_role="super_admin",
        rule_id="always_confirm_booking",
        new_value={"reason": "Still required."},
        change_reason="clarify reason",
        approver_user_id="00000000-0000-0000-0000-000000000002",
        approver_role="super_admin",
    )

    assert updated.version == 2
    assert conn.execute.await_count == 2
    assert "UPDATE policy_rules" in conn.execute.await_args_list[0].args[0]
    assert "INSERT INTO policy_change_log" in conn.execute.await_args_list[1].args[0]
