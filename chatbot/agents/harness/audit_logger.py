from __future__ import annotations

import json

import structlog

logger = structlog.get_logger(__name__)


class AuditLogger:
    def __init__(self, db_pool, metrics=None) -> None:
        self._pool = db_pool
        self._metrics = metrics

    async def log(
        self,
        *,
        tool_run_id: str,
        tool_name: str,
        params_json: dict,
        result_json: dict | list | None,
        context: dict,
        status: str,
        policy_rule_id: str | None,
        latency_ms: int | None,
        prompt_version: str | None,
        pre_confirmed: bool,
    ) -> None:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs (
                        tool_run_id, session_id, task_id, tool_name,
                        params_json, result_json, status, pre_confirmed,
                        user_id, community_id, policy_rule_id, prompt_version, latency_ms
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, $8,
                        $9, $10, $11, $12, $13
                    )
                    """,
                    tool_run_id,
                    context.get("session_id", ""),
                    context.get("task_id", ""),
                    tool_name,
                    json.dumps(params_json),
                    json.dumps(result_json) if result_json is not None else None,
                    status,
                    pre_confirmed,
                    context.get("user_id", ""),
                    context.get("community_id", ""),
                    policy_rule_id,
                    prompt_version or context.get("prompt_version"),
                    latency_ms,
                )
        except Exception as exc:
            if self._metrics is not None:
                self._metrics.increment_audit_log_failure()
            logger.error("audit_log_failed", tool_run_id=tool_run_id, error=str(exc))
