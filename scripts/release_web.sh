#!/usr/bin/env bash
set -euo pipefail

uv sync
uv run alembic upgrade head
