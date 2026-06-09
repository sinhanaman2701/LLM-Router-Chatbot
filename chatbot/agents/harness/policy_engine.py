from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import structlog

from chatbot.agents.harness.policy_store import PolicyStore
from chatbot.state.schemas import HarnessContext

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PolicyResult:
    action: Literal["ALLOW", "DENY", "REQUIRE_CONFIRMATION"]
    rule_id: str
    reason: str


class PolicyEngine:
    def __init__(self, redis, policy_store: PolicyStore | None = None, metrics=None) -> None:
        self._redis = redis
        self._policy_store = policy_store
        self._metrics = metrics

    async def evaluate(
        self,
        tool_name: str,
        params: dict,
        context: HarnessContext,
    ) -> PolicyResult:
        rule_records = await self._load_rules(context.capability, tool_name)
        matched_confirmation: PolicyResult | None = None

        for rule in rule_records:
            matched = await self._matches(rule.id, rule.conditions_json, tool_name, context)
            if not matched:
                continue
            result = PolicyResult(action=rule.action, rule_id=rule.id, reason=rule.reason)
            if self._metrics is not None:
                self._metrics.observe_policy(tool_name=tool_name, action=result.action)
            if result.action == "DENY":
                return result
            if result.action == "REQUIRE_CONFIRMATION" and matched_confirmation is None:
                matched_confirmation = result

        if matched_confirmation is not None:
            return matched_confirmation
        if self._metrics is not None:
            self._metrics.observe_policy(tool_name=tool_name, action="ALLOW")
        return PolicyResult(action="ALLOW", rule_id="default_allow", reason="")

    async def _load_rules(self, capability: str, tool_name: str):
        if self._policy_store is None:
            return self._default_rules(capability, tool_name)
        try:
            return await self._policy_store.get_active_rules(capability, tool_name)
        except Exception as exc:
            logger.error("policy_rule_load_failed", tool_name=tool_name, capability=capability, error=str(exc))
            return self._default_rules(capability, tool_name)

    async def _matches(self, rule_id: str, conditions: list[dict], tool_name: str, context: HarnessContext) -> bool:
        if not conditions:
            return True
        for condition in conditions:
            condition_type = condition.get("type")
            if condition_type == "hourly_limit":
                hour_window = int(time.time()) // 3600
                rate_key = f"rate_booking:{context.user_id}:{hour_window}"
                pipe = self._redis.pipeline()
                pipe.incr(rate_key)
                pipe.expire(rate_key, 3600)
                results = await pipe.execute()
                count = results[0]
                operator = condition.get("operator")
                value = int(condition.get("value", 0))
                if operator == ">" and count > value:
                    continue
                return False
            logger.warning("policy_condition_unsupported", rule_id=rule_id, tool_name=tool_name, condition_type=condition_type)
            return False
        return True

    @staticmethod
    def _default_rules(capability: str, tool_name: str):
        if capability != "facility_booking":
            return []
        defaults = {
            "create_booking": [
                type("Rule", (), {
                    "id": "rate_limit_booking",
                    "conditions_json": [{"type": "hourly_limit", "operator": ">", "value": 5}],
                    "action": "DENY",
                    "reason": "You have exceeded 5 bookings in the last hour.",
                })(),
                type("Rule", (), {
                    "id": "always_confirm_booking",
                    "conditions_json": [],
                    "action": "REQUIRE_CONFIRMATION",
                    "reason": "Booking requires user confirmation.",
                })(),
            ],
            "cancel_booking": [
                type("Rule", (), {
                    "id": "always_confirm_cancellation",
                    "conditions_json": [],
                    "action": "REQUIRE_CONFIRMATION",
                    "reason": "Cancellation requires user confirmation.",
                })(),
            ],
        }
        return defaults.get(tool_name, [])
