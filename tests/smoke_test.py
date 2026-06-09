from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from chatbot.config import settings
from chatbot.main import app
from chatbot.services.api_adapter import ApiAdapter
from chatbot.services.mock_server_auth import MockServerAuth
from chatbot.state.schemas import ToolCallRequest, UserInfo
from chatbot.state.state_manager import StateManager


async def run_async_checks() -> None:
    redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    http_client = httpx.AsyncClient(timeout=10.0)
    auth = MockServerAuth(redis)
    adapter = ApiAdapter(http_client, redis, auth)
    state_manager = StateManager(redis)

    try:
        cookie = await auth.login(http_client)
        assert cookie.startswith("mock_session_token_"), "cookie not set after login"
        print("1. MockServerAuth.login() set cookie")

        facilities = await adapter.get_facility_list()
        assert len(facilities) == 4, f"expected 4 facilities, got {len(facilities)}"
        print("2. ApiAdapter.get_facility_list() returned 4 facilities")

        booking_date = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
        created = await adapter.make_booking(
            "fac_1",
            booking_date,
            "11:00",
            "12:00",
            settings.MOCK_SERVER_EMAIL,
        )
        assert created["booking_id"].startswith("bk_"), f"unexpected booking id: {created}"

        availability = await adapter.get_facility_booking_data("fac_1")
        availability_bookings = availability.get("bookings", [])
        assert any(
            booking.get("booking_id") == created["booking_id"]
            for booking in availability_bookings
        ), "created booking missing from facility booking data"

        my_bookings = await adapter.get_my_bookings()
        upcoming = my_bookings.get("upcoming_bookings", [])
        assert any(
            booking.get("booking_id") == created["booking_id"]
            for booking in upcoming
        ), "created booking missing from my bookings"
        print("3. Booking state is visible in availability and my bookings")

        user = UserInfo(
            user_id="00000000-0000-0000-0000-000000000001",
            community_id="00000000-0000-0000-0000-000000000002",
            email=settings.MOCK_SERVER_EMAIL,
            unit_id="flat101",
            role="resident",
        )
        session = await state_manager.create_session(user)
        await state_manager.init_task(
            session.session_id,
            "facility_booking",
            {
                "facility_name": "Tennis Court",
                "facility_id": "fac_1",
                "date": (datetime.now(UTC) + timedelta(days=1)).date().isoformat(),
                "start_time": "09:00",
                "end_time": "10:00",
                "duration_minutes": 60,
                "open_time": "07:00",
                "close_time": "22:00",
            },
            "facility_planner_v1.0",
        )
        updated = await state_manager.update_slots(
            session.session_id,
            {"facility_name": "Swimming Pool"},
        )
        task = updated.active_task
        assert task is not None
        assert task.slots.facility_id is None
        assert task.slot_validation.date_valid is False
        assert task.slot_validation.time_valid is False
        assert task.slot_validation.slot_available is False
        print("4. StateManager cascade reset verified")
    finally:
        await http_client.aclose()
        await redis.aclose()


def run_http_checks() -> None:
    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login",
            data={"email": settings.MOCK_SERVER_EMAIL, "password": settings.MOCK_SERVER_PASSWORD},
        )
        assert login_response.status_code == 200, login_response.text
        token = login_response.json()["token"]
        print("5. /auth/login returned an HMAC token")

        chat_response = client.post(
            "/chat/message",
            json={"user_message": "Book the tennis court tomorrow"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert chat_response.status_code == 202, chat_response.text
        request_id = chat_response.json()["request_id"]
        print("6. /chat/message returned 202 with request_id")

        status_response = client.get(f"/chat/status/{request_id}")
        assert status_response.status_code == 200, status_response.text
        assert status_response.json() == {"status": "processing"}
        print("7. /chat/status/{request_id} returned processing")


def main() -> None:
    asyncio.run(run_async_checks())
    run_http_checks()
    print("Smoke test passed")


if __name__ == "__main__":
    main()
