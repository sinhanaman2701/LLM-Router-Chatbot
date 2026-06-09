from __future__ import annotations

import json
import re
import time
from pathlib import Path

import structlog

from chatbot.agents.llm.base_client import BaseLLMClient
from chatbot.config import settings
from chatbot.observability.context import bind_log_context
from chatbot.state.schemas import ConversationSession, RouterDecision

logger = structlog.get_logger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "router_v1.0.txt"

INTENT_CLASS_DEFINITIONS: tuple[dict[str, object], ...] = (
    {
        "name": "new_task",
        "description": "Start a fresh booking-related task when there is no active task or the user clearly begins a new request.",
        "rules": (
            "Use for new booking, cancellation, or bookings lookup requests.",
            "Prefer switch_task instead if the user explicitly changes direction mid-task.",
        ),
    },
    {
        "name": "continue_task",
        "description": "Provide details or corrections that advance the current active task.",
        "rules": (
            "Use when the user answers a planner question or fills missing slots.",
            "Do not use for unrelated informational questions.",
        ),
    },
    {
        "name": "switch_task",
        "description": "Pause the current task and start a different booking-related task.",
        "rules": (
            "Use when the user explicitly pivots to a different goal mid-conversation.",
            "Prefer new_task only when there is no meaningful active task to suspend.",
        ),
    },
    {
        "name": "cancel_task",
        "description": "Abandon the current active task entirely.",
        "rules": (
            "Use for messages like cancel this, never mind, or stop this booking.",
            "Do not use for rejecting only one pending confirmation step.",
        ),
    },
    {
        "name": "resume_task",
        "description": "Return to a previously paused task after a switch or side question.",
        "rules": (
            "Use when the user says go back, continue the earlier booking, or resume the previous task.",
            "Prefer continue_task when they are still working on the current active task.",
        ),
    },
    {
        "name": "side_question",
        "description": "Ask a quick read-only question without changing the active task.",
        "rules": (
            "Use for short informational questions like what facilities are available.",
            "Do not use if the user is changing the core goal of the conversation.",
        ),
    },
    {
        "name": "confirmation",
        "description": "Approve a pending action that is waiting for explicit confirmation.",
        "rules": (
            "Use for yes, confirm, go ahead, ok, or proceed when a confirmation is pending.",
            "Prefer continue_task if there is no pending action being approved.",
        ),
    },
    {
        "name": "rejection",
        "description": "Reject or correct a pending action without abandoning the broader task.",
        "rules": (
            "Use for no, not that, change that, or actually a different time when confirmation is pending.",
            "Prefer cancel_task only when the whole task should be abandoned.",
        ),
    },
    {
        "name": "small_talk",
        "description": "Handle casual conversation unrelated to facility booking.",
        "rules": (
            "Use for greetings, thanks, or chit-chat with no booking intent.",
            "Do not use if the message still contains a booking-related request.",
        ),
    },
    {
        "name": "unclear",
        "description": "Use when the user intent cannot be classified confidently.",
        "rules": (
            "Use when the message is too vague or ambiguous to route safely.",
            "Keep extracted_slots empty unless a slot value is explicit and reliable.",
        ),
    },
)


def _format_slots(session: ConversationSession) -> str:
    if session.active_task is None:
        return "none"
    slots = session.active_task.slots
    parts = []
    for field_name in ("facility_name", "date", "start_time", "duration_minutes"):
        val = getattr(slots, field_name, None)
        if val is not None:
            parts.append(f"{field_name}: {val}")
    return ", ".join(parts) if parts else "none"


def _format_intent_definitions() -> str:
    lines: list[str] = []
    for item in INTENT_CLASS_DEFINITIONS:
        lines.append(f"- {item['name']}: {item['description']}")
        for rule in item["rules"]:
            lines.append(f"  - {rule}")
    return "\n".join(lines)


def _format_facility_catalog(facilities: list[dict[str, object]] | None) -> str:
    if not facilities:
        return "unknown"
    parts: list[str] = []
    for facility in facilities[:12]:
        name = facility.get("facility_name") or facility.get("name")
        category = facility.get("category")
        if isinstance(name, str) and isinstance(category, str):
            parts.append(f"{name} ({category})")
        elif isinstance(name, str):
            parts.append(name)
    return ", ".join(parts) if parts else "unknown"


_FALLBACK = RouterDecision(
    capability="none",
    intent_class="unclear",
    confidence=0.0,
    extracted_slots={},
)

_DATE_SELECTION_PATTERNS = (
    re.compile(r"^\s*set date to (\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE),
    re.compile(r"^\s*change date to (\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE),
)
_TIME_SELECTION_PATTERNS = (
    re.compile(r"^\s*set time to (\d{2}:\d{2})\s*$", re.IGNORECASE),
    re.compile(r"^\s*change time to (\d{2}:\d{2})\s*$", re.IGNORECASE),
)


def _fast_path_decision(user_message: str) -> RouterDecision | None:
    for pattern in _DATE_SELECTION_PATTERNS:
        match = pattern.match(user_message)
        if match:
            return RouterDecision(
                capability="facility_booking",
                intent_class="continue_task",
                confidence=1.0,
                extracted_slots={"date": match.group(1)},
            )
    for pattern in _TIME_SELECTION_PATTERNS:
        match = pattern.match(user_message)
        if match:
            return RouterDecision(
                capability="facility_booking",
                intent_class="continue_task",
                confidence=1.0,
                extracted_slots={"start_time": match.group(1)},
            )
    return None


class RouterAgent:
    def __init__(self, llm_client: BaseLLMClient, metrics=None) -> None:
        self._llm = llm_client
        self._prompt_template = PROMPT_PATH.read_text()
        self._metrics = metrics

    async def classify(
        self,
        session: ConversationSession,
        user_message: str,
        facility_catalog: list[dict[str, object]] | None = None,
    ) -> RouterDecision:
        fast_path = _fast_path_decision(user_message)
        if fast_path is not None:
            self._record_metrics("success", 0.0, fast_path.intent_class, fast_path.confidence)
            return fast_path

        active_capability = (
            session.active_task.capability if session.active_task else "none"
        )
        slots_text = _format_slots(session)
        system_prompt = (
            self._prompt_template
            .replace("{active_capability}", active_capability)
            .replace("{slots_text}", slots_text)
            .replace("{facility_catalog}", _format_facility_catalog(facility_catalog))
            .replace("{intent_class_definitions}", _format_intent_definitions())
        )
        bind_log_context(component="router", session_id=session.session_id, user_id=session.user.user_id)
        start_time = time.monotonic()

        try:
            response = await self._llm.chat(
                [{"role": "user", "content": user_message}],
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.error("router_llm_error", error=str(exc))
            self._record_metrics("error", time.monotonic() - start_time, "unclear", 0.0)
            return _FALLBACK

        # Parse Thought + Action
        if "\nAction:\n" not in response:
            logger.warning("router_parse_no_action")
            self._record_metrics("error", time.monotonic() - start_time, "unclear", 0.0)
            return _FALLBACK

        try:
            parts = response.split("\nAction:\n", 1)
            action_str = parts[1].strip()
            # Extract just the JSON object
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
            action = json.loads(action_str[:end_idx])
            decision = RouterDecision.model_validate(action)
        except Exception as exc:
            logger.warning("router_parse_error", error=str(exc))
            self._record_metrics("error", time.monotonic() - start_time, "unclear", 0.0)
            return _FALLBACK

        # Apply confidence threshold
        if decision.confidence < settings.ROUTER_CONFIDENCE_LOW:
            self._record_metrics("success", time.monotonic() - start_time, "unclear", decision.confidence)
            return RouterDecision(
                capability=decision.capability,
                intent_class="unclear",
                confidence=decision.confidence,
                extracted_slots={},
            )

        self._record_metrics("success", time.monotonic() - start_time, decision.intent_class, decision.confidence)
        return decision

    def _record_metrics(self, outcome: str, elapsed_seconds: float, intent_class: str, confidence: float) -> None:
        if self._metrics is None:
            return
        duration_ms = elapsed_seconds * 1000
        self._metrics.observe_llm(
            component="router",
            model=settings.OLLAMA_MODEL,
            prompt_version="router_v1.0",
            outcome=outcome,
            duration_ms=duration_ms,
        )
        self._metrics.observe_router_confidence(
            intent_class=intent_class,
            confidence=confidence,
            low_threshold=settings.ROUTER_CONFIDENCE_LOW,
            high_threshold=settings.ROUTER_CONFIDENCE_HIGH,
        )
