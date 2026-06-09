FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock alembic.ini README.md /app/
COPY chatbot /app/chatbot
COPY scripts /app/scripts

RUN pip install uv \
    && uv sync --frozen --no-dev

RUN chmod +x /app/scripts/start_web.sh /app/scripts/release_web.sh

EXPOSE 8000

CMD ["bash", "scripts/start_web.sh"]
