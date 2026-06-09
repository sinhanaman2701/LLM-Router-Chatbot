from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.routers.auth import login
from chatbot.state.schemas import ConversationSession, UserInfo


def _make_request():
    mock_server_auth = MagicMock()
    mock_server_auth.authenticate_user = AsyncMock(
        return_value={
            "user_id": "u1",
            "community_id": "c1",
            "user_email": "resident@example.com",
            "unit_id": "flat101",
            "role": "resident",
        }
    )

    state_manager = MagicMock()
    state_manager.get_expired_confirmation_recovery = AsyncMock()
    state_manager.create_session = AsyncMock(
        return_value=ConversationSession(
            session_id="11111111-1111-1111-1111-111111111111",
            user=UserInfo(
                user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                community_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                email="resident@example.com",
                unit_id="flat101",
                role="resident",
            ),
            created_at=1748000000,
            last_activity_at=1748000000,
        )
    )

    audit_logger = MagicMock()
    audit_logger.log = AsyncMock()

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                mock_server_auth=mock_server_auth,
                state_manager=state_manager,
                audit_logger=audit_logger,
            )
        )
    )


@pytest.mark.asyncio
async def test_login_returns_recovery_message_and_audits_expired_pending_confirmation():
    request = _make_request()
    request.app.state.state_manager.get_expired_confirmation_recovery.return_value = {
        "session_id": "old-session",
        "task_id": "task-1",
        "tool_name": "create_booking",
        "params": {"facility_id": "fac_1"},
        "prompt_version": "facility_planner_v1.0",
    }

    response = await login(request, email="resident@example.com", password="password")

    assert response.recovery_message is not None
    request.app.state.audit_logger.log.assert_called_once()
    assert request.app.state.audit_logger.log.call_args.kwargs["status"] == "EXPIRED_PENDING"


@pytest.mark.asyncio
async def test_login_without_recovery_keeps_response_clean():
    request = _make_request()
    request.app.state.state_manager.get_expired_confirmation_recovery.return_value = None

    response = await login(request, email="resident@example.com", password="password")

    assert response.recovery_message is None
    request.app.state.audit_logger.log.assert_not_called()
