from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Capability = Literal["facility_booking", "none"]
IntentClass = Literal[
    "new_task",
    "continue_task",
    "switch_task",
    "cancel_task",
    "resume_task",
    "side_question",
    "confirmation",
    "rejection",
    "small_talk",
    "unclear",
]
UserRole = Literal["resident", "guest", "community_admin", "super_admin"]
MessageRole = Literal["user", "bot", "assistant", "tool", "system"]
HarnessStatus = Literal[
    "SUCCESS",
    "DENIED",
    "AWAITING_CONFIRMATION",
    "UNCERTAIN_STATE",
    "ERROR",
]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserInfo(StrictModel):
    user_id: str
    community_id: str
    email: str
    unit_id: str
    role: UserRole


class SlotState(StrictModel):
    facility_name: str | None = None
    facility_id: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_minutes: int | None = None
    open_time: str | None = None
    close_time: str | None = None


class SlotValidation(StrictModel):
    facility_valid: bool = False
    date_valid: bool = False
    time_valid: bool = False
    slot_available: bool = False


class ChatMessage(StrictModel):
    role: MessageRole
    content: str
    ts: int


class ToolCallRequest(StrictModel):
    tool_name: str
    params: dict[str, Any]
    requested_by: str
    task_id: str


class TaskContext(StrictModel):
    task_id: str
    capability: Capability
    prompt_version: str
    planner_memory: list[dict[str, Any]] = Field(default_factory=list)
    history_summary: str | None = None
    slots: SlotState = Field(default_factory=SlotState)
    slot_validation: SlotValidation = Field(default_factory=SlotValidation)
    awaiting_confirmation: bool = False
    pending_tool_call: ToolCallRequest | None = None
    confirmation_token: str | None = None
    confirmation_turns: int = 0
    turn_count: int = 0
    created_at: int
    last_updated_at: int


class ConversationSession(StrictModel):
    session_id: str
    user: UserInfo
    active_task: TaskContext | None = None
    stashed_task: TaskContext | None = None
    suspended_tasks: list[TaskContext] = Field(default_factory=list)
    message_history: list[ChatMessage] = Field(default_factory=list)
    intent_at_last_turn: Capability | None = None
    message_count: int = 0
    consecutive_unclear_count: int = 0
    no_progress_turns: int = 0
    created_at: int
    last_activity_at: int


class HarnessContext(StrictModel):
    user_id: str
    user_email: str
    community_id: str
    capability: Capability
    task_id: str
    session_id: str
    correlation_id: str
    user_role: UserRole
    prompt_version: str


class HarnessResult(StrictModel):
    status: HarnessStatus
    tool_run_id: str
    data: dict[str, Any] | list[dict[str, Any]] | None = None
    reason: str | None = None
    error: str | None = None
    confirmation_token: str | None = None
    pending_call: ToolCallRequest | None = None
    confirmation_summary: str | None = None
    latency_ms: int | None = None


class RouterDecision(StrictModel):
    capability: Capability
    intent_class: IntentClass
    confidence: float
    extracted_slots: dict[str, Any] = Field(default_factory=dict)


class RequestContext(StrictModel):
    session: ConversationSession
    user: UserInfo
    preferences: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
