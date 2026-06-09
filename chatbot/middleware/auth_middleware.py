from __future__ import annotations

import base64
import hashlib
import hmac
import time
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from chatbot.config import settings


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_session_token(session_id: str) -> str:
    signature = hmac.new(
        settings.SESSION_HMAC_SECRET.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return _b64url_encode(f"{session_id}.{signature}".encode("utf-8"))


async def _record_auth_failure(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    block_key = f"rate_ip_block:{ip}"
    count_key = f"rate_ip:{ip}:{int(time.time() // 60)}"
    redis = request.app.state.redis
    blocked = await redis.get(block_key)
    if blocked:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="IP temporarily blocked")
    count = await redis.incr(count_key)
    if count == 1:
        await redis.expire(count_key, 60)
    if count > settings.IP_AUTH_FAILURE_LIMIT:
        await redis.set(block_key, "1", ex=settings.IP_AUTH_BLOCK_SECONDS)
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="IP temporarily blocked")


async def verify_hmac_token(token: str, request: Request) -> str:
    try:
        decoded = _b64url_decode(token).decode("utf-8")
        session_id, provided_sig = decoded.split(".", 1)
        UUID(session_id)
    except Exception as exc:
        await _record_auth_failure(request)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    expected_sig = hmac.new(
        settings.SESSION_HMAC_SECRET.encode("utf-8"),
        session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        await _record_auth_failure(request)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return session_id


async def require_session_id(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = header.removeprefix("Bearer ").strip()
    return await verify_hmac_token(token, request)


async def require_session_id_dep(session_id: str = Depends(require_session_id)) -> str:
    return session_id
