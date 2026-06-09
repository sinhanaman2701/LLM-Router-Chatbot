from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.agents.router.router_agent import RouterAgent
from chatbot.state.schemas import ConversationSession, UserInfo


def _make_session():
    now = 1748000000
    return ConversationSession(
        session_id="sess-1",
        user=UserInfo(
            user_id="user-1",
            community_id="comm-1",
            email="test@example.com",
            unit_id="flat101",
            role="resident",
        ),
        active_task=None,
        created_at=now,
        last_activity_at=now,
    )


def _make_router(response: str):
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=response)
    return RouterAgent(llm_client=llm)


VALID_RESPONSE = (
    'Thought: User wants to book a facility.\n\nAction:\n'
    '{"capability": "facility_booking", "intent_class": "new_task", "confidence": 0.95, "extracted_slots": {"facility_name": "Tennis Court"}}'
)

FACILITY_CATALOG = [
    {"name": "Tennis Court", "category": "Sports"},
    {"name": "Gym", "category": "Fitness"},
]

LOW_CONFIDENCE = (
    'Thought: Unclear.\n\nAction:\n'
    '{"capability": "facility_booking", "intent_class": "new_task", "confidence": 0.5, "extracted_slots": {}}'
)


@pytest.mark.asyncio
async def test_valid_classification():
    router = _make_router(VALID_RESPONSE)
    decision = await router.classify(_make_session(), "Book the tennis court", facility_catalog=FACILITY_CATALOG)
    assert decision.capability == "facility_booking"
    assert decision.intent_class == "new_task"
    assert decision.confidence == 0.95
    assert decision.extracted_slots.get("facility_name") == "Tennis Court"


@pytest.mark.asyncio
async def test_low_confidence_becomes_unclear():
    router = _make_router(LOW_CONFIDENCE)
    decision = await router.classify(_make_session(), "something vague", facility_catalog=FACILITY_CATALOG)
    assert decision.intent_class == "unclear"


@pytest.mark.asyncio
async def test_router_prompt_includes_intent_descriptions():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=VALID_RESPONSE)
    router = RouterAgent(llm_client=llm)

    await router.classify(_make_session(), "switch my task", facility_catalog=FACILITY_CATALOG)

    system_prompt = llm.chat.call_args.kwargs["system_prompt"]
    assert "Pause the current task and start a different booking-related task." in system_prompt
    assert "Reject or correct a pending action without abandoning the broader task." in system_prompt
    assert "Tennis Court (Sports)" in system_prompt
    assert "Gym (Fitness)" in system_prompt


@pytest.mark.asyncio
async def test_malformed_output_returns_unclear():
    router = _make_router("This is just plain text with no Action block.")
    decision = await router.classify(_make_session(), "hmm", facility_catalog=FACILITY_CATALOG)
    assert decision.intent_class == "unclear"
    assert decision.confidence == 0.0


@pytest.mark.asyncio
async def test_invalid_json_returns_unclear():
    router = _make_router('Thought: ok\n\nAction:\n{not valid json}')
    decision = await router.classify(_make_session(), "hmm", facility_catalog=FACILITY_CATALOG)
    assert decision.intent_class == "unclear"


@pytest.mark.asyncio
async def test_llm_error_returns_unclear():
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=Exception("LLM unavailable"))
    router = RouterAgent(llm_client=llm)
    decision = await router.classify(_make_session(), "book something", facility_catalog=FACILITY_CATALOG)
    assert decision.intent_class == "unclear"
