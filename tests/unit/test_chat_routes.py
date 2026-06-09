from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.middleware.auth_middleware import sign_session_token
from chatbot.routers import chat


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat.router)

    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    app.state.redis = redis

    return app


def _auth_header() -> dict[str, str]:
    token = sign_session_token("11111111-1111-1111-1111-111111111111")
    return {"Authorization": f"Bearer {token}"}


def test_post_message_rejects_blank_input(monkeypatch):
    app = _make_test_app()
    monkeypatch.setattr(chat, "_process_message", AsyncMock())

    with TestClient(app) as client:
        response = client.post(
            "/chat/message",
            json={"user_message": "   "},
            headers=_auth_header(),
        )

    assert response.status_code == 422
    chat._process_message.assert_not_called()


def test_post_message_seeds_processing_state(monkeypatch):
    app = _make_test_app()
    mocked_process = AsyncMock()
    monkeypatch.setattr(chat, "_process_message", mocked_process)

    with TestClient(app) as client:
        response = client.post(
            "/chat/message",
            json={"user_message": "Book the tennis court"},
            headers=_auth_header(),
        )

    assert response.status_code == 202
    assert response.json()["poll_url"].startswith("/chat/status/")
    app.state.redis.set.assert_called()
    mocked_process.assert_called_once()


def test_get_status_returns_not_found_when_request_missing():
    app = _make_test_app()
    app.state.redis.get = AsyncMock(return_value=None)

    with TestClient(app) as client:
        response = client.get("/chat/status/missing-request")

    assert response.status_code == 200
    assert response.json() == {"status": "not_found"}
