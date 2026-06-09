from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any
from uuid import uuid4

import httpx
import structlog
from pydantic import ValidationError

from chatbot.agents.harness.audit_logger import AuditLogger
from chatbot.agents.harness.confirmation_manager import generate_token
from chatbot.agents.harness.policy_engine import PolicyEngine
from chatbot.agents.harness.tool_registry import TOOL_REGISTRY, ToolDef
from chatbot.config import settings
from chatbot.observability.context import bind_log_context
from chatbot.state.schemas import HarnessContext, HarnessResult, ToolCallRequest
from chatbot.tools.facility_tools import (
    cancel_booking,
    create_booking,
    get_facility_availability,
    get_facility_list,
    get_my_bookings,
)

logger = structlog.get_logger(__name__)

TOOL_FUNCTION_MAP = {
    "get_facility_list": get_facility_list,
    "get_facility_availability": get_facility_availability,
    "create_booking": create_booking,
    "cancel_booking": cancel_booking,
    "get_my_bookings": get_my_bookings,
}


def _build_confirmation_summary(tool_name: str, params: dict[str, Any]) -> str:
    if tool_name == "create_booking":
        return (
            f"I'm about to book {params.get('facility_id')} on {params.get('date')} "
            f"from {params.get('start_time')} to {params.get('end_time')}. Shall I confirm?"
        )
    if tool_name == "cancel_booking":
        return f"I'm about to cancel booking {params.get('booking_id')}. Shall I confirm?"
    return f"I'm about to run {tool_name}. Shall I confirm?"


class _CircuitBreaker:
    """Simple per-tool circuit breaker backed by Redis hash."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _key(self, tool_name: str) -> str:
        return f"cb:{tool_name}"

    async def get_state(self, tool_name: str) -> str:
        state = await self._redis.hget(self._key(tool_name), "state")
        return state or "CLOSED"

    async def is_open(self, tool_name: str) -> bool:
        key = self._key(tool_name)
        state = await self._redis.hget(key, "state")
        if state != "OPEN":
            return False
        open_until = float(await self._redis.hget(key, "open_until") or 0)
        if time.time() > open_until:
            await self._redis.hset(key, "state", "HALF_OPEN")
            return False
        return True

    async def record_success(self, tool_name: str) -> None:
        key = self._key(tool_name)
        pipe = self._redis.pipeline()
        pipe.hget(key, "state")
        results = await pipe.execute()
        state = results[0]
        if state == "HALF_OPEN":
            await self._redis.hset(key, mapping={"state": "CLOSED", "failure_count": 0, "success_count": 0, "total_count": 0})
        else:
            await self._redis.hincrby(key, "success_count", 1)
            await self._redis.hincrby(key, "total_count", 1)

    async def record_failure(self, tool_name: str) -> None:
        key = self._key(tool_name)
        await self._redis.hincrby(key, "failure_count", 1)
        await self._redis.hincrby(key, "total_count", 1)
        # Check if we should open
        data = await self._redis.hgetall(key)
        failure_count = int(data.get("failure_count", 0))
        total_count = int(data.get("total_count", 0))
        if (
            total_count >= settings.CB_MIN_SAMPLE_SIZE
            and total_count > 0
            and failure_count / total_count >= settings.CB_FAILURE_THRESHOLD
        ):
            open_until = time.time() + settings.CB_OPEN_DURATION_SECONDS
            await self._redis.hset(key, mapping={"state": "OPEN", "open_until": open_until})


class AgentHarness:
    def __init__(
        self,
        registry: dict[str, ToolDef] | None = None,
        policy_engine: PolicyEngine | None = None,
        audit_logger: AuditLogger | None = None,
        api_adapter: Any = None,
        redis: Any = None,
        metrics: Any = None,
    ) -> None:
        self.registry = registry or TOOL_REGISTRY
        self._policy_engine = policy_engine
        self._audit_logger = audit_logger
        self._api_adapter = api_adapter
        self._cb = _CircuitBreaker(redis) if redis else None
        self._metrics = metrics

    def _redact(self, params: dict[str, Any], sensitive_fields: list[str]) -> dict[str, Any]:
        redacted = dict(params)
        for field in sensitive_fields:
            if field in redacted and redacted[field] is not None:
                value = str(redacted[field]).encode("utf-8")
                redacted[field] = f"[REDACTED:{hashlib.sha256(value).hexdigest()[:8]}]"
        return redacted

    def _check_domain_access(self, tool_name: str, capability: str) -> bool:
        tool_def = self.registry.get(tool_name)
        return bool(tool_def and capability in tool_def.domain)

    async def _execute_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        tool_def: ToolDef,
    ) -> Any:
        fn = TOOL_FUNCTION_MAP[tool_name]
        last_exc: Exception | None = None
        max_attempts = tool_def.max_retries if tool_def.idempotent else 1

        for attempt in range(max_attempts):
            try:
                return await asyncio.wait_for(
                    fn(self._api_adapter, **params),
                    timeout=tool_def.timeout_seconds,
                )
            except (httpx.HTTPStatusError,) as exc:
                status_code = exc.response.status_code if hasattr(exc, "response") else 0
                if not tool_def.idempotent:
                    raise
                if status_code < 500 and status_code != 429:
                    raise
                last_exc = exc
            except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
                if not tool_def.idempotent:
                    raise
                last_exc = exc

            if attempt < max_attempts - 1:
                backoff = settings.HARNESS_RETRY_BASE_SECONDS * (2 ** attempt)
                await asyncio.sleep(backoff)

        raise last_exc or RuntimeError("Execution failed")

    async def execute(
        self,
        tool_call: ToolCallRequest,
        context: HarnessContext,
        pre_confirmed: bool = False,
    ) -> HarnessResult:
        tool_run_id = str(uuid4())
        start_time = time.monotonic()
        bind_log_context(
            component="harness",
            session_id=context.session_id,
            task_id=context.task_id,
            user_id=context.user_id,
            community_id=context.community_id,
            tool_run_id=tool_run_id,
            tool_name=tool_call.tool_name,
        )
        logger.info("harness_execute_started", pre_confirmed=pre_confirmed)

        # Tool lookup + domain check
        tool_def = self.registry.get(tool_call.tool_name)
        if tool_def is None or not self._check_domain_access(tool_call.tool_name, context.capability):
            if self._metrics is not None:
                self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="error")
            return HarnessResult(
                status="ERROR",
                tool_run_id=tool_run_id,
                error="Tool not found or access denied",
            )

        # Param validation
        try:
            tool_def.param_schema(**tool_call.params)
        except ValidationError as exc:
            if self._metrics is not None:
                self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="error")
            return HarnessResult(status="ERROR", tool_run_id=tool_run_id, error=str(exc))

        redacted_params = self._redact(tool_call.params, tool_def.sensitive_fields)
        context_dict = context.model_dump()

        # Circuit breaker
        if self._cb and await self._cb.is_open(tool_call.tool_name):
            if self._metrics is not None:
                self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="error")
            return HarnessResult(
                status="ERROR",
                tool_run_id=tool_run_id,
                error=f"Service {tool_call.tool_name} is temporarily unavailable (circuit open).",
            )

        # Policy engine
        if self._policy_engine:
            policy_result = await self._policy_engine.evaluate(
                tool_call.tool_name, tool_call.params, context
            )
            if policy_result.action == "DENY":
                if self._audit_logger:
                    await self._audit_logger.log(
                        tool_run_id=tool_run_id,
                        tool_name=tool_call.tool_name,
                        params_json=redacted_params,
                        result_json=None,
                        context=context_dict,
                        status="DENIED",
                        policy_rule_id=policy_result.rule_id,
                        latency_ms=None,
                        prompt_version=context.prompt_version,
                        pre_confirmed=pre_confirmed,
                    )
                if self._metrics is not None:
                    self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="denied")
                return HarnessResult(
                    status="DENIED",
                    tool_run_id=tool_run_id,
                    reason=policy_result.reason,
                )

            if policy_result.action == "REQUIRE_CONFIRMATION" and not pre_confirmed:
                token = generate_token()
                summary = _build_confirmation_summary(tool_call.tool_name, tool_call.params)
                pending_call = ToolCallRequest(
                    tool_name=tool_call.tool_name,
                    params=tool_call.params,
                    requested_by=tool_call.requested_by,
                    task_id=tool_call.task_id,
                )
                if self._audit_logger:
                    await self._audit_logger.log(
                        tool_run_id=tool_run_id,
                        tool_name=tool_call.tool_name,
                        params_json=redacted_params,
                        result_json=None,
                        context=context_dict,
                        status="AWAITING_CONFIRMATION",
                        policy_rule_id=policy_result.rule_id,
                        latency_ms=None,
                        prompt_version=context.prompt_version,
                        pre_confirmed=False,
                    )
                if self._metrics is not None:
                    self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="confirm")
                return HarnessResult(
                    status="AWAITING_CONFIRMATION",
                    tool_run_id=tool_run_id,
                    confirmation_token=token,
                    pending_call=pending_call,
                    confirmation_summary=summary,
                )
        else:
            policy_result = None  # type: ignore[assignment]

        # Inject user_email for create_booking
        params = dict(tool_call.params)
        if tool_call.tool_name == "create_booking":
            params["user_email"] = context.user_email

        # Execute
        try:
            result = await self._execute_tool(tool_call.tool_name, params, tool_def)
            if self._cb:
                await self._cb.record_success(tool_call.tool_name)
                if self._metrics is not None:
                    state = await self._cb.get_state(tool_call.tool_name)
                    self._metrics.set_circuit_breaker_state(tool_name=tool_call.tool_name, state=state)
        except httpx.HTTPStatusError as exc:
            if self._cb:
                await self._cb.record_failure(tool_call.tool_name)
                if self._metrics is not None:
                    state = await self._cb.get_state(tool_call.tool_name)
                    self._metrics.set_circuit_breaker_state(tool_name=tool_call.tool_name, state=state)
            status_code = exc.response.status_code if hasattr(exc, "response") else 0
            if not tool_def.idempotent and status_code >= 500:
                if self._audit_logger:
                    await self._audit_logger.log(
                        tool_run_id=tool_run_id,
                        tool_name=tool_call.tool_name,
                        params_json=redacted_params,
                        result_json=None,
                        context=context_dict,
                        status="UNCERTAIN",
                        policy_rule_id=getattr(policy_result, "rule_id", None),
                        latency_ms=int((time.monotonic() - start_time) * 1000),
                        prompt_version=context.prompt_version,
                        pre_confirmed=pre_confirmed,
                    )
                if self._metrics is not None:
                    self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="uncertain")
                return HarnessResult(
                    status="UNCERTAIN_STATE",
                    tool_run_id=tool_run_id,
                    error="The action may or may not have completed. Please check before retrying.",
                )
            if self._metrics is not None:
                self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="error")
            return HarnessResult(status="ERROR", tool_run_id=tool_run_id, error=str(exc))
        except Exception as exc:
            if self._cb:
                await self._cb.record_failure(tool_call.tool_name)
                if self._metrics is not None:
                    state = await self._cb.get_state(tool_call.tool_name)
                    self._metrics.set_circuit_breaker_state(tool_name=tool_call.tool_name, state=state)
            logger.error("harness_tool_error", tool=tool_call.tool_name, error=str(exc))
            if self._metrics is not None:
                self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="error")
            return HarnessResult(status="ERROR", tool_run_id=tool_run_id, error=str(exc))

        latency_ms = int((time.monotonic() - start_time) * 1000)

        if self._audit_logger:
            await self._audit_logger.log(
                tool_run_id=tool_run_id,
                tool_name=tool_call.tool_name,
                params_json=redacted_params,
                result_json=result if isinstance(result, (dict, list)) else {"raw": str(result)},
                context=context_dict,
                status="SUCCESS",
                policy_rule_id=getattr(policy_result, "rule_id", None),
                latency_ms=latency_ms,
                prompt_version=context.prompt_version,
                pre_confirmed=pre_confirmed,
            )

        data = result if isinstance(result, (dict, list)) else {"raw": str(result)}
        if self._metrics is not None:
            self._metrics.observe_tool_call(component="harness", tool_name=tool_call.tool_name, outcome="success")
        logger.info("harness_execute_completed", duration_ms=latency_ms, outcome="SUCCESS")
        return HarnessResult(
            status="SUCCESS",
            tool_run_id=tool_run_id,
            data=data,
            latency_ms=latency_ms,
        )
