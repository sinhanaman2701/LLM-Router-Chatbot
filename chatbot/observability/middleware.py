from __future__ import annotations

import time
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from chatbot.observability.context import bind_log_context, clear_log_context

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        clear_log_context()
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        client_ip = request.client.host if request.client else "unknown"
        bind_log_context(
            request_id=request_id,
            correlation_id=correlation_id,
            method=request.method,
            route=request.url.path,
            client_ip=client_ip,
            component="api",
        )
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        start_time = time.monotonic()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            return response
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            metrics = getattr(request.app.state, "metrics", None)
            if metrics is not None:
                metrics.observe_request(
                    component="api",
                    route=request.url.path,
                    method=request.method,
                    status=status_code,
                    duration_ms=duration_ms,
                )
            logger.info(
                "http_request_complete",
                status_code=status_code,
                duration_ms=round(duration_ms, 2),
            )
            clear_log_context()
