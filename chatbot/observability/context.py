from __future__ import annotations

from typing import Any

import structlog


def bind_log_context(**values: Any) -> None:
    filtered = {key: value for key, value in values.items() if value is not None}
    if filtered:
        structlog.contextvars.bind_contextvars(**filtered)


def clear_log_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_log_context() -> dict[str, Any]:
    return structlog.contextvars.get_contextvars()
