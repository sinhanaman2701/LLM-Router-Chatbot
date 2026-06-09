from __future__ import annotations

import time
from typing import Any

import httpx
from redis.asyncio import Redis
import structlog

from chatbot.config import settings
from chatbot.services.mock_server_auth import MockServerAuth

logger = structlog.get_logger(__name__)


class MockServerAPIError(RuntimeError):
    pass


class ApiAdapter:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        redis: Redis,
        auth: MockServerAuth,
        metrics=None,
    ):
        self.http_client = http_client
        self.redis = redis
        self.auth = auth
        self._metrics = metrics

    @staticmethod
    def _multipart(data: dict[str, Any] | None = None) -> dict[str, tuple[None, str]]:
        if not data:
            return {"_": (None, "")}
        return {key: (None, str(value)) for key, value in data.items()}

    async def _request(
        self,
        path: str,
        *,
        form_data: dict[str, Any] | None = None,
        timeout_seconds: int = 10,
    ) -> Any:
        start_time = time.monotonic()
        outcome = "error"
        await self.auth.get_cookie(self.http_client)
        url = f"{settings.MOCK_SERVER_URL}{path}"

        try:
            for attempt in range(2):
                response = await self.http_client.post(
                    url,
                    files=self._multipart(form_data),
                    timeout=timeout_seconds,
                )
                if response.status_code == 401 and attempt == 0:
                    await self.auth.refresh(self.http_client)
                    continue
                response.raise_for_status()
                payload = response.json()
                if payload.get("m_system_status_code") != 0:
                    raise MockServerAPIError(payload.get("m_system_status_message") or "Mock API error")
                outcome = "success"
                return payload["m_app_response"]["m_response_data"]

            raise MockServerAPIError("Mock API request failed after refresh retry")
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            if self._metrics is not None:
                self._metrics.observe_dependency(
                    dependency=path,
                    outcome=outcome,
                    duration_ms=duration_ms,
                )
            logger.info("api_adapter_request_complete", path=path, outcome=outcome, duration_ms=round(duration_ms, 2))

    async def get_facility_list(self) -> list[dict[str, Any]]:
        data = await self._request("/facilities/m_get_facility_list")
        if not isinstance(data, list):
            raise MockServerAPIError("Facility list response was not a list")
        return data

    async def get_facility_booking_data(self, facility_id: str) -> dict[str, Any]:
        data = await self._request(f"/facilities/m_get_facility_booking_data/{facility_id}")
        if not isinstance(data, dict):
            raise MockServerAPIError("Facility booking data response was not an object")
        return data

    async def make_booking(
        self,
        facility_id: str,
        date: str,
        start_time: str,
        end_time: str,
        user_email: str,
    ) -> dict[str, Any]:
        data = await self._request(
            "/facilities/m_member_make_booking",
            form_data={
                "facility_id": facility_id,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "user_email": user_email,
            },
        )
        if not isinstance(data, dict):
            raise MockServerAPIError("Booking response was not an object")
        return data

    async def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        data = await self._request(
            "/facilities/m_cancel_booking",
            form_data={"booking_id": booking_id},
        )
        if not isinstance(data, dict):
            raise MockServerAPIError("Cancel response was not an object")
        return data

    async def get_my_bookings(self) -> dict[str, Any]:
        data = await self._request("/facilities/m_get_my_bookings_v3")
        if not isinstance(data, dict):
            raise MockServerAPIError("My bookings response was not an object")
        return data
