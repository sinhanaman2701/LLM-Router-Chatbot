from __future__ import annotations

from typing import Any
import time

import httpx
from redis.asyncio import Redis
import structlog

from chatbot.config import settings

logger = structlog.get_logger(__name__)


class MockServerAuthError(RuntimeError):
    pass


class MockServerAuth:
    CACHE_KEY = "api:auth_token:mock_server"

    def __init__(self, redis: Redis, metrics=None):
        self.redis = redis
        self._metrics = metrics

    @staticmethod
    def _multipart(data: dict[str, Any]) -> dict[str, tuple[None, str]]:
        return {key: (None, str(value)) for key, value in data.items()}

    async def login(self, http_client: httpx.AsyncClient) -> str:
        start_time = time.monotonic()
        response = await http_client.post(
            f"{settings.MOCK_SERVER_URL}/auth/m_login",
            files=self._multipart(
                {
                    "email": settings.MOCK_SERVER_EMAIL,
                    "password": settings.MOCK_SERVER_PASSWORD,
                }
            ),
        )
        response.raise_for_status()
        cookie = response.cookies.get("session_token")
        if not cookie:
            raise MockServerAuthError("Mock server login did not return a session_token cookie")
        http_client.cookies.set("session_token", cookie)
        await self.redis.set(self.CACHE_KEY, cookie, ex=settings.API_AUTH_COOKIE_TTL)
        if self._metrics is not None:
            self._metrics.observe_mock_server_auth(outcome="refresh")
            self._metrics.observe_dependency(
                dependency="mock_server_auth_login",
                outcome="success",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )
        logger.info("mock_server_auth_login_succeeded")
        return cookie

    async def get_cookie(self, http_client: httpx.AsyncClient) -> str:
        cached = await self.redis.get(self.CACHE_KEY)
        if cached:
            http_client.cookies.set("session_token", cached)
            return str(cached)
        if self._metrics is not None:
            self._metrics.observe_mock_server_auth(outcome="cache_miss")
        return await self.login(http_client)

    async def refresh(self, http_client: httpx.AsyncClient) -> str:
        return await self.login(http_client)

    async def authenticate_user(self, email: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.MOCK_SERVER_URL}/auth/m_login",
                files=self._multipart({"email": email, "password": password}),
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("m_system_status_code") != 0:
                raise MockServerAuthError(payload.get("m_system_status_message") or "Mock auth failed")
            return payload["m_app_response"]["m_response_data"]
