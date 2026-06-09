from __future__ import annotations

import secrets


def generate_token() -> str:
    return secrets.token_urlsafe(16)
