from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from chatbot.middleware.auth_middleware import sign_session_token, verify_hmac_token


def _make_request():
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock()
    redis.set = AsyncMock()
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
    )


@pytest.mark.asyncio
async def test_verify_hmac_token_accepts_signed_token():
    request = _make_request()
    session_id = "11111111-1111-1111-1111-111111111111"
    token = sign_session_token(session_id)

    verified = await verify_hmac_token(token, request)
    assert verified == session_id


@pytest.mark.asyncio
async def test_verify_hmac_token_rejects_tampered_token():
    request = _make_request()
    token = sign_session_token("11111111-1111-1111-1111-111111111111")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(HTTPException) as exc:
        await verify_hmac_token(tampered, request)

    assert exc.value.status_code == 401
    request.app.state.redis.incr.assert_called_once()
