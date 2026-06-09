from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import structlog

from chatbot.config import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PolicyRuleRecord:
    id: str
    capability: str
    tool_name: str
    conditions_json: list[dict]
    action: str
    risk_level: str
    reason: str
    active: bool
    version: int


class PolicyStore:
    def __init__(self, db_pool, redis) -> None:
        self._pool = db_pool
        self._redis = redis

    @staticmethod
    def _cache_key(capability: str, tool_name: str) -> str:
        return f"policy:{capability}:{tool_name}"

    async def get_active_rules(self, capability: str, tool_name: str) -> list[PolicyRuleRecord]:
        cache_key = self._cache_key(capability, tool_name)
        cached = await self._redis.get(cache_key)
        if cached:
            payload = json.loads(cached)
            return [PolicyRuleRecord(**item) for item in payload]

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, capability, tool_name, conditions_json, action, risk_level, reason, active, version
                FROM policy_rules
                WHERE capability = $1 AND tool_name = $2 AND active = true
                ORDER BY
                    CASE action
                        WHEN 'DENY' THEN 0
                        WHEN 'REQUIRE_CONFIRMATION' THEN 1
                        ELSE 2
                    END,
                    id
                """,
                capability,
                tool_name,
            )

        records = [
            PolicyRuleRecord(
                id=row["id"],
                capability=row["capability"],
                tool_name=row["tool_name"],
                conditions_json=list(row["conditions_json"] or []),
                action=row["action"],
                risk_level=row["risk_level"],
                reason=row["reason"],
                active=row["active"],
                version=row["version"],
            )
            for row in rows
        ]
        await self._redis.set(
            cache_key,
            json.dumps([record.__dict__ for record in records]),
            ex=settings.POLICY_CACHE_TTL,
        )
        return records

    async def update_rule(
        self,
        *,
        actor_user_id: str,
        actor_role: str,
        rule_id: str,
        new_value: dict,
        change_reason: str,
        approver_user_id: str | None = None,
        approver_role: str | None = None,
    ) -> PolicyRuleRecord:
        if actor_role not in {"super_admin", "community_admin"}:
            await self._record_rejected_attempt(
                rule_id=rule_id,
                actor_user_id=actor_user_id,
                current_value=None,
                attempted_value=new_value,
                reason=f"REJECTED: {change_reason}",
            )
            logger.warning("policy_update_rejected", rule_id=rule_id, actor_role=actor_role, reason="insufficient_role")
            raise PermissionError("Only admin roles may update policy rules")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, capability, tool_name, conditions_json, action, risk_level, reason, active, version
                FROM policy_rules
                WHERE id = $1
                """,
                rule_id,
            )
            if row is None:
                raise KeyError(f"Policy rule {rule_id} not found")

            current = PolicyRuleRecord(
                id=row["id"],
                capability=row["capability"],
                tool_name=row["tool_name"],
                conditions_json=list(row["conditions_json"] or []),
                action=row["action"],
                risk_level=row["risk_level"],
                reason=row["reason"],
                active=row["active"],
                version=row["version"],
            )

            merged = {
                "capability": new_value.get("capability", current.capability),
                "tool_name": new_value.get("tool_name", current.tool_name),
                "conditions_json": new_value.get("conditions_json", current.conditions_json),
                "action": new_value.get("action", current.action),
                "risk_level": new_value.get("risk_level", current.risk_level),
                "reason": new_value.get("reason", current.reason),
                "active": new_value.get("active", current.active),
            }

            if self._is_critical_change(current, merged):
                if approver_role != "super_admin" or not approver_user_id or approver_user_id == actor_user_id:
                    await self._record_rejected_attempt(
                        rule_id=rule_id,
                        actor_user_id=actor_user_id,
                        current_value=current.__dict__,
                        attempted_value=merged,
                        reason=f"REJECTED: {change_reason}",
                    )
                    logger.warning("policy_update_rejected", rule_id=rule_id, actor_role=actor_role, reason="missing_second_approval")
                    raise PermissionError("Critical policy changes require a second super_admin approval")

            await conn.execute(
                """
                UPDATE policy_rules
                SET capability = $2,
                    tool_name = $3,
                    conditions_json = $4::jsonb,
                    action = $5,
                    risk_level = $6,
                    reason = $7,
                    active = $8,
                    version = version + 1,
                    updated_at = now()
                WHERE id = $1
                """,
                rule_id,
                merged["capability"],
                merged["tool_name"],
                json.dumps(merged["conditions_json"]),
                merged["action"],
                merged["risk_level"],
                merged["reason"],
                merged["active"],
            )
            await conn.execute(
                """
                INSERT INTO policy_change_log (
                    rule_id, old_value_json, new_value_json, changed_by_user_id, reason
                ) VALUES ($1, $2::jsonb, $3::jsonb, $4::uuid, $5)
                """,
                rule_id,
                json.dumps(current.__dict__),
                json.dumps(merged),
                str(UUID(actor_user_id)),
                change_reason,
            )

        await self._redis.delete(self._cache_key(current.capability, current.tool_name))
        if merged["capability"] != current.capability or merged["tool_name"] != current.tool_name:
            await self._redis.delete(self._cache_key(merged["capability"], merged["tool_name"]))

        return PolicyRuleRecord(
            id=rule_id,
            capability=merged["capability"],
            tool_name=merged["tool_name"],
            conditions_json=list(merged["conditions_json"]),
            action=merged["action"],
            risk_level=merged["risk_level"],
            reason=merged["reason"],
            active=bool(merged["active"]),
            version=current.version + 1,
        )

    @staticmethod
    def _is_critical_change(current: PolicyRuleRecord, merged: dict) -> bool:
        state_changing_tools = {"create_booking", "cancel_booking"}
        if current.tool_name in state_changing_tools or merged["tool_name"] in state_changing_tools:
            if current.action != merged["action"]:
                return True
            if current.active != bool(merged["active"]):
                return True
        if current.action in {"DENY", "REQUIRE_CONFIRMATION"} and current.action != merged["action"]:
            return True
        return False

    async def _record_rejected_attempt(
        self,
        *,
        rule_id: str,
        actor_user_id: str,
        current_value: dict | None,
        attempted_value: dict,
        reason: str,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO policy_change_log (
                        rule_id, old_value_json, new_value_json, changed_by_user_id, reason
                    ) VALUES ($1, $2::jsonb, $3::jsonb, $4::uuid, $5)
                    """,
                    rule_id,
                    json.dumps(current_value or {}),
                    json.dumps(attempted_value),
                    str(UUID(actor_user_id)),
                    reason,
                )
        except Exception as exc:
            logger.error("policy_change_log_failed", rule_id=rule_id, error=str(exc))
