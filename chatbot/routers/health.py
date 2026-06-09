from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    metrics = request.app.state.metrics

    redis_start = time.monotonic()
    await request.app.state.redis.ping()
    metrics.observe_dependency(
        dependency="redis_ready",
        outcome="success",
        duration_ms=(time.monotonic() - redis_start) * 1000,
    )

    db_start = time.monotonic()
    async with request.app.state.db_pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    metrics.observe_dependency(
        dependency="db_ready",
        outcome="success",
        duration_ms=(time.monotonic() - db_start) * 1000,
    )

    mock_start = time.monotonic()
    response = await request.app.state.http_client.post(
        f"{request.app.state.settings.MOCK_SERVER_URL}/community_v2/m_get_dashboard_static_data",
        files={"_": (None, "")},
        timeout=5.0,
    )
    response.raise_for_status()
    metrics.observe_dependency(
        dependency="mock_server_ready",
        outcome="success",
        duration_ms=(time.monotonic() - mock_start) * 1000,
    )
    return {"status": "ready"}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> str:
    return request.app.state.metrics.render()
