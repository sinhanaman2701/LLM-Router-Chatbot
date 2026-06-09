from __future__ import annotations

import json
import time
from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog

from chatbot.agents.llm.base_client import BaseLLMClient
from chatbot.config import settings
from chatbot.observability.context import bind_log_context
from chatbot.state.schemas import (
    ConversationSession,
    HarnessContext,
    HarnessResult,
    ToolCallRequest,
)

logger = structlog.get_logger(__name__)


@dataclass
class PlannerOutput:
    type: Literal["user_question", "final_answer"]
    content: str
    status: str | None = None  # "success"|"incomplete"|"error" for final_answer


def format_harness_result(result: HarnessResult) -> str:
    if result.status == "SUCCESS":
        return json.dumps({"status": "SUCCESS", "data": result.data})
    if result.status == "DENIED":
        return json.dumps({"status": "DENIED", "reason": result.reason})
    if result.status == "UNCERTAIN_STATE":
        return json.dumps({"status": "UNCERTAIN_STATE", "error": result.error})
    return json.dumps({"status": result.status, "error": result.error})


def _format_slots(session: ConversationSession) -> str:
    if session.active_task is None:
        return "none"
    slots = session.active_task.slots
    parts = []
    for field_name in ("facility_name", "facility_id", "date", "start_time", "end_time", "duration_minutes"):
        val = getattr(slots, field_name, None)
        if val is not None:
            parts.append(f"{field_name}: {val}")
    return ", ".join(parts) if parts else "none"


def _format_preferences(preferences: dict[str, str]) -> str:
    if not preferences:
        return "None yet."
    return "\n".join(f"- {k}: {v}" for k, v in preferences.items())


class BasePlanner(ABC):
    def __init__(
        self,
        llm_client: BaseLLMClient,
        harness: Any,
        state_manager: Any,
        max_iterations: int,
        prompt_path: Path,
    ) -> None:
        self._llm = llm_client
        self._harness = harness
        self._state_manager = state_manager
        self._max_iterations = max_iterations
        self._system_prompt_template = prompt_path.read_text()
        self._prompt_name = prompt_path.stem

    def _build_system_prompt(
        self,
        session: ConversationSession,
        preferences: dict[str, str],
    ) -> str:
        today_date = datetime.now(timezone.utc).date().isoformat()
        task = session.active_task
        replacements = {
            "today_date": today_date,
            "user_email": session.user.email,
            "unit_id": session.user.unit_id,
            "community_id": session.user.community_id,
            "preferences_text": _format_preferences(preferences),
            "slots_text": _format_slots(session),
            "history_summary": (task.history_summary or "No prior context.") if task else "No prior context.",
        }
        result = self._system_prompt_template
        for key, value in replacements.items():
            result = result.replace(f"{{{key}}}", value)
        return result

    def _parse_action(self, response: str) -> dict[str, Any] | None:
        if "\nAction:\n" not in response:
            return None
        try:
            parts = response.split("\nAction:\n", 1)
            action_str = parts[1].strip()
            # Strip any trailing text after the JSON object
            brace_count = 0
            end_idx = 0
            for i, ch in enumerate(action_str):
                if ch == "{":
                    brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_idx = i + 1
                        break
            return json.loads(action_str[:end_idx])
        except (json.JSONDecodeError, IndexError):
            return None

    async def _summarize_memory(
        self,
        planner_memory: list[dict],
        existing_summary: str | None,
    ) -> tuple[list[dict], str]:
        keep_count = settings.PLANNER_MEMORY_MAX_ENTRIES // 2
        to_summarize = planner_memory[:-keep_count] if keep_count else planner_memory
        keep = planner_memory[-keep_count:] if keep_count else []

        entries_text = "\n".join(
            f"{e.get('role','?')}: {e.get('content','')[:300]}" for e in to_summarize
        )
        prior = f"Prior summary: {existing_summary}\n\n" if existing_summary else ""
        summary_prompt = (
            f"{prior}Summarize the following conversation history in 2-3 sentences:\n\n{entries_text}"
        )
        try:
            summary = await self._llm.chat(
                [{"role": "user", "content": summary_prompt}]
            )
        except Exception:
            summary = existing_summary or "Prior conversation history (summarized)."

        return keep, summary

    async def run(
        self,
        session: ConversationSession,
        user_message: str,
        harness_context: HarnessContext,
        preferences: dict[str, str],
    ) -> PlannerOutput:
        system_prompt = self._build_system_prompt(session, preferences)
        task = session.active_task

        # Build messages from planner_memory + current user message
        messages: list[dict[str, str]] = []
        if task:
            for entry in task.planner_memory:
                role = entry.get("role", "user")
                # Map "tool" role to "user" for LLM compat
                if role == "tool":
                    role = "user"
                messages.append({"role": role, "content": entry.get("content", "")})
        messages.append({"role": "user", "content": user_message})

        # Local copy of planner memory to build up during this run
        planner_memory: list[dict] = list(task.planner_memory) if task else []
        history_summary = task.history_summary if task else None

        for iteration in range(self._max_iterations):
            llm_start = time.monotonic()
            try:
                response = await self._llm.chat(messages, system_prompt=system_prompt)
            except Exception as exc:
                logger.error("planner_llm_error", error=str(exc))
                self._record_llm_metrics("error", time.monotonic() - llm_start)
                return PlannerOutput(
                    type="final_answer",
                    content="I encountered an error. Please try again.",
                    status="error",
                )
            self._record_llm_metrics("success", time.monotonic() - llm_start)

            action = self._parse_action(response)
            if action is None:
                # Malformed output — tell the model and retry
                logger.warning("planner_parse_error", iteration=iteration)
                error_msg = 'Your response did not follow the required format. Please respond with "Thought:" followed by "\\nAction:\\n{json}".'
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": error_msg})
                continue

            # Append LLM response to messages and memory
            messages.append({"role": "assistant", "content": response})
            planner_memory.append({"role": "assistant", "content": response})

            action_type = action.get("type")

            if action_type == "tool_call":
                bind_log_context(component="facility_planner", task_id=harness_context.task_id, session_id=harness_context.session_id)
                tool_call = ToolCallRequest(
                    tool_name=action.get("tool_name", ""),
                    params=action.get("params", {}),
                    requested_by="facility_planner",
                    task_id=harness_context.task_id,
                )
                result = await self._harness.execute(tool_call, harness_context)

                if result.status == "AWAITING_CONFIRMATION":
                    # Store confirmation in session and return immediately
                    await self._state_manager.set_awaiting_confirmation(
                        harness_context.session_id,
                        result.pending_call,
                        result.confirmation_token,
                    )
                    planner_memory.append({
                        "role": "user",
                        "content": f"Tool Result: {json.dumps({'status': 'AWAITING_CONFIRMATION', 'summary': result.confirmation_summary})}",
                    })
                    await self._persist_memory(harness_context.session_id, planner_memory, history_summary)
                    return PlannerOutput(
                        type="user_question",
                        content=result.confirmation_summary or "Shall I confirm this action?",
                    )

                tool_result_str = f"Tool Result: {format_harness_result(result)}"
                messages.append({"role": "user", "content": tool_result_str})
                planner_memory.append({"role": "user", "content": tool_result_str})

            elif action_type == "user_question":
                planner_memory, history_summary = await self._maybe_prune(planner_memory, history_summary)
                await self._persist_memory(harness_context.session_id, planner_memory, history_summary)
                return PlannerOutput(
                    type="user_question",
                    content=action.get("question", "Could you provide more details?"),
                )

            elif action_type == "final_answer":
                planner_memory, history_summary = await self._maybe_prune(planner_memory, history_summary)
                await self._persist_memory(harness_context.session_id, planner_memory, history_summary)
                return PlannerOutput(
                    type="final_answer",
                    content=action.get("summary", "Done."),
                    status=action.get("status", "success"),
                )

        # Iteration cap
        await self._persist_memory(harness_context.session_id, planner_memory, history_summary)
        return PlannerOutput(
            type="final_answer",
            content="I was unable to complete the task within the allowed steps. Please try again.",
            status="incomplete",
        )

    async def _maybe_prune(
        self,
        planner_memory: list[dict],
        history_summary: str | None,
    ) -> tuple[list[dict], str | None]:
        if len(planner_memory) > settings.PLANNER_MEMORY_MAX_ENTRIES:
            planner_memory, history_summary = await self._summarize_memory(planner_memory, history_summary)
        return planner_memory, history_summary

    async def _persist_memory(
        self,
        session_id: str,
        planner_memory: list[dict],
        history_summary: str | None,
    ) -> None:
        try:
            await self._state_manager.update_planner_memory(session_id, planner_memory, history_summary)
        except Exception as exc:
            logger.error("planner_memory_persist_error", error=str(exc))

    def _record_llm_metrics(self, outcome: str, elapsed_seconds: float) -> None:
        metrics = getattr(self._state_manager, "metrics", None)
        if metrics is None:
            return
        metrics.observe_llm(
            component="facility_planner",
            model=settings.OLLAMA_MODEL,
            prompt_version=self._prompt_name,
            outcome=outcome,
            duration_ms=elapsed_seconds * 1000,
        )
