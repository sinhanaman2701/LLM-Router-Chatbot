from __future__ import annotations

import asyncio
import time

import httpx


BASE_URL = "http://127.0.0.1:8000"
CONCURRENT_SESSIONS = 50


async def _login(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post(
        f"{BASE_URL}/auth/login",
        data={"email": email, "password": "password"},
    )
    response.raise_for_status()
    payload = response.json()
    return payload["token"]


async def _exercise_session(index: int) -> float:
    async with httpx.AsyncClient(timeout=20.0) as client:
        token = await _login(client, f"resident{index}@example.com")
        start = time.monotonic()
        response = await client.post(
            f"{BASE_URL}/chat/message",
            headers={"Authorization": f"Bearer {token}"},
            json={"user_message": "What facilities are available?"},
        )
        response.raise_for_status()
        return (time.monotonic() - start) * 1000


async def main() -> None:
    durations = await asyncio.gather(*[_exercise_session(index) for index in range(CONCURRENT_SESSIONS)])
    durations = sorted(durations)
    p95_index = max(0, int(len(durations) * 0.95) - 1)
    print(
        {
            "concurrent_sessions": CONCURRENT_SESSIONS,
            "avg_ms": round(sum(durations) / len(durations), 2),
            "p95_ms": round(durations[p95_index], 2),
            "max_ms": round(max(durations), 2),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
