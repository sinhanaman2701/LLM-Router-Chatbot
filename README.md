# LLM Router Chatbot

A production-ready agentic chatbot for community management, built on a **Router → Domain Planner → Agent Harness** architecture. Designed for Anacity's residential communities — handles facility booking through a flexible ReAct-loop planner that replaces brittle per-capability state machines.

---

## Architecture

```
User Message
     │
     ▼
Context Loader          ← Redis session + user preferences cache
     │
     ▼
Router Agent            ← Fast LLM: classifies intent + extracts slots (10 intent classes)
     │  RouterDecision
     ▼
Session & Task Manager  ← Init / restore / suspend / stash TaskContext (Redis, WATCH/MULTI/EXEC)
     │
     ▼
Domain Planner Agent    ← ReAct loop: Think → ToolCallRequest → Observe (max 5 iterations)
     │  ToolCallRequest
     ▼
Agent Harness           ← Schema validation → PII redaction → Policy check
     │                     → Idempotency → Confirmation → Execute → Audit log
     ▼
ApiAdapter              ← httpx AsyncClient with cookie jar; multipart/form-data to mock server
     │
     ▼
Response Synthesizer → ChatResponse → User (async 202/polling or SSE)
```

**Three design rules that make this safe:**
1. The router uses the same cheap model for classification — never for planning
2. The planner only emits structured `ToolCallRequest` JSON — it never calls anything directly
3. The harness is deterministic middleware: every tool call passes through schema validation, policy evaluation, PII redaction, and an append-only audit log before execution

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + uvicorn |
| LLM | `gemma4:31b-cloud` via [Ollama Cloud](https://ollama.com) |
| Session state | Redis 7 (WATCH/MULTI/EXEC optimistic locking) |
| Persistent store | Postgres 18 (asyncpg + SQLAlchemy 2.x async + Alembic) |
| HTTP client | httpx AsyncClient (persistent cookie jar) |
| Validation | Pydantic v2 |
| Logging | structlog (structured JSON) |
| Metrics | Custom Prometheus-compatible `/metrics` endpoint |
| Package manager | uv |
| Python | ≥3.11 |

---

## Features

### v2 Scope: Facility Booking
- Browse available facilities and check real-time slot availability
- Book a facility with human-in-the-loop confirmation before execution
- Cancel an existing booking (also confirmation-gated)
- View upcoming and past bookings
- Mid-flow intent switching, corrections, and side questions handled gracefully

### Architecture Capabilities
- **10 intent classes**: new_task, continue_task, switch_task, cancel_task, resume_task, side_question, confirmation, rejection, small_talk, unclear
- **Slot cascade invalidation**: changing the facility resets date/time validity; changing the date resets time validity
- **Suspended task stack** (depth 3): users can switch topics and resume previous tasks
- **Confirmation timeout**: pending confirmations auto-cancel after 2 unrelated turns
- **Session expiry recovery**: if a session expires with a pending confirmation, the user gets a recovery prompt on next login
- **Circuit breaker** per tool (Redis-backed, min 10-call sample before opening)
- **PII redaction**: sensitive fields SHA256-hashed before audit log writes
- **Append-only audit log**: every tool execution logged with `tool_run_id`, redacted params, policy rule, and `pre_confirmed` flag
- **Prometheus metrics**: 11 metrics covering request latency, LLM calls, tool outcomes, policy decisions, circuit breaker state, and dependency health

---

## Project Structure

```
chatbot/
├── agents/
│   ├── llm/              # LLM client abstraction (OllamaClient live; Anthropic/Groq stubs)
│   ├── router/           # RouterAgent + versioned prompt
│   ├── planners/         # BasePlanner (ReAct loop) + FacilityPlanner + versioned prompt
│   └── harness/          # AgentHarness, PolicyEngine, PolicyStore, AuditLogger, ToolRegistry
├── tools/                # 5 facility tool implementations
├── state/                # Pydantic schemas + Redis StateManager
├── memory/               # User preferences (Postgres + Redis 24h cache)
├── observability/        # Prometheus metrics, structlog context, request middleware
├── routers/              # FastAPI routers: chat, auth, health, session, ui (embedded SPA)
├── services/             # ApiAdapter, MockServerAuth, ResponseSynthesizer
├── middleware/           # HMAC auth + IP rate limiting
├── db/                   # asyncpg pool + Alembic migrations
├── config.py             # All settings via Pydantic BaseSettings
└── main.py               # FastAPI app + lifespan (component wiring)
ops/
├── alerts/               # Prometheus alert rules
└── dashboards/           # Grafana dashboard JSON
scripts/
└── load_test_phase3.py   # 50-concurrent-session load test
tests/
├── unit/                 # 34 passing unit tests (Phases 1–2)
└── integration/
```

---

## Setup

### Prerequisites

- Python ≥3.11
- [uv](https://docs.astral.sh/uv/)
- Postgres 18 + Redis 7 (Homebrew: `brew install postgresql@18 redis`)
- [Ollama Cloud](https://ollama.com) API key
- The Anacity mock server (`node server.js` in the mock-server directory, runs on `http://localhost:3000`)

### Install

```bash
uv sync
```

### Environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `DATABASE_URL` | asyncpg connection string |
| `REDIS_URL` | Redis connection string |
| `SESSION_HMAC_SECRET` | Secret for HMAC-signed session tokens — change in production |
| `MOCK_SERVER_URL` | Anacity mock server base URL (default: `http://localhost:3000`) |
| `MOCK_SERVER_EMAIL` | Service account email for mock server auth |
| `MOCK_SERVER_PASSWORD` | Service account password |
| `OLLAMA_API_KEY` | Ollama Cloud Bearer token |
| `OLLAMA_BASE_URL` | Ollama API base URL (default: `https://ollama.com`) |
| `OLLAMA_MODEL` | Model to use (default: `gemma4:31b-cloud`) |

### Database

```bash
# Start Postgres and Redis
brew services start postgresql@18
brew services start redis

# Run migrations
uv run alembic upgrade head
```

### Run

```bash
uv run uvicorn chatbot.main:app --reload
```

The embedded chat UI is at `http://localhost:8000`.  
API docs at `http://localhost:8000/docs`.

### Railway Beta Deploy

Recommended Railway project shape:

- `web`: FastAPI app from this repo root
- `mock-server`: Node service from `mock-server/`
- managed PostgreSQL
- managed Redis

Recommended `web` service commands:

```bash
# Build command
uv sync

# Start command
bash scripts/start_web.sh
```

Recommended release command for `web`:

```bash
bash scripts/release_web.sh
```

Recommended `mock-server` service commands:

```bash
# Root directory
mock-server

# Install command
npm install

# Start command
npm start
```

Recommended `web` environment variables:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | Railway Postgres connection string |
| `REDIS_URL` | Railway Redis connection string |
| `SESSION_HMAC_SECRET` | long random secret |
| `MOCK_SERVER_URL` | Railway private URL for the mock-server service |
| `MOCK_SERVER_EMAIL` | `dn.user.a@gmail.com` |
| `MOCK_SERVER_PASSWORD` | `password` |
| `OLLAMA_API_KEY` | your Ollama Cloud key |
| `OLLAMA_BASE_URL` | `https://ollama.com` |
| `OLLAMA_MODEL` | `gemma4:31b-cloud` |

Notes:

- `web` exposes `/health` and `/health/ready`
- `mock-server` exposes `/health`
- use Railway private networking between `web` and `mock-server`
- redeploy after changing env vars

### AWS ECS/Fargate Beta Deploy

This repo is also ready for an AWS beta deployment using:

- ECS Fargate
- one task with two containers: `web` and `mock-server`
- RDS PostgreSQL
- ElastiCache node-based Valkey/Redis
- an ALB public URL instead of a custom domain

See:

- [AWS ECS beta deploy guide](docs/aws-ecs-beta-deploy.md)
- [AWS ECS deploy assets](ops/aws/README.md)

---

## API

### Auth
```
POST /auth/login          form: email, password → {token, session_id, recovery_message?}
```

### Chat (async 202/polling)
```
POST /chat/message        header: Authorization: Bearer <token>
                          body: {user_message: string}
                          → 202 {request_id, poll_url}

GET  /chat/status/{id}    → {status: "processing"} | {status: "done", reply, message_id, timestamp}
```

### Health
```
GET  /health              → {status: "ok"}
GET  /health/ready        → checks Redis + Postgres + mock server
GET  /metrics             → Prometheus-format metrics
```

---

## Testing

```bash
uv run pytest tests/unit -v
```

**Test status (as of 2026-06-04):**
- Phase 1 unit tests: 25/25 passed
- Phase 2 unit tests: 34/34 passed (cumulative)

---

## Implementation Phases

| Phase | Status | Scope |
|---|---|---|
| 0 — Foundation | ✅ Complete | Schemas, auth, DB migrations, ApiAdapter, StateManager, HMAC, 202/polling |
| 1 — Planner + Harness | ✅ Complete | End-to-end booking, full harness, policy engine, circuit breaker, preferences |
| 2 — Router + Intent Flows | ✅ Complete | All 10 intent types, switch/resume/stash, confirmation flow, slot correction |
| 3 — Observability | 🚧 In Progress | Metrics + logging done; load test + alert validation pending |

---

## Adding New Capabilities

The architecture is domain-agnostic. Adding complaint management, FAQ, payments, or visitor management requires only:

1. A new `{domain}_planner.py` (subclass of `BasePlanner`)
2. A new `{domain}_tools.py` wrapping `ApiAdapter`
3. Tool definitions added to `ToolRegistry`
4. Default policy rules for the domain

The router, harness, state manager, audit log, and all session logic are reused unchanged.

---

## License

MIT
