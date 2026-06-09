import asyncio

from chatbot.routers.ui import chat_ui


def test_chat_ui_serves_login_and_chat_shell():
    response = asyncio.run(chat_ui())

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "<title>Anacity Chat</title>" in body
    assert 'id="login-form"' in body
    assert 'fetch("/auth/login"' in body
    assert 'fetch("/chat/message"' in body
    assert 'fetch("/chat/status/" + requestId' in body
    assert 'id="session-id"' not in body
    assert 'id="auth-state"' not in body
    assert "MAX_POLL_ATTEMPTS" in body
    assert "The chat request was not found. Please try sending it again." in body
