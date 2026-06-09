#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"

exec uv run uvicorn chatbot.main:app --host 0.0.0.0 --port "$PORT"
