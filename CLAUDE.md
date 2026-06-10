# Anacity Agentic Chatbot — Project Context

## What This Is

A community chatbot for Anacity that lets residents book and cancel facilities via a conversational UI. Built with FastAPI + LangGraph-style agent loop + Redis + Postgres. The embedded frontend is a single-page HTML app served directly from the FastAPI app — there is no separate frontend project.

The current scope is single-domain: facility booking. The architecture is generic and designed to support more capabilities (complaints, payments, etc.) later.

## Live Deployment

- **URL:** http://13.206.195.19:8000/
- **Platform:** AWS EC2, single instance (t3.small), ap-south-1 (Mumbai)
- **Instance name:** llm-router-chatbot-poc
- **Key pair:** llm-router-chatbot-key.pem (stored at ~/Downloads/ on the dev Mac)
- **Public IP:** 13.206.195.19 (not static — will change if the instance is stopped/started)

Everything runs as Docker containers on the EC2 instance via `docker-compose.ec2.yml`.

## AWS Account

- **Account ID:** 874041194383
- **IAM user:** namansinha
- **Region:** ap-south-1 (Mumbai)
- **ECR repos:**
  - `874041194383.dkr.ecr.ap-south-1.amazonaws.com/llm-router-chatbot-web`
  - `874041194383.dkr.ecr.ap-south-1.amazonaws.com/llm-router-chatbot-mock-server`

Note: RDS and ElastiCache instances were created during setup but are NOT used by the current deployment. The EC2 instance runs its own Postgres and Redis containers. Those managed services can be cleaned up to avoid charges.

## Stack on EC2

| Container | Image | Purpose |
|---|---|---|
| `chatbot-web` | `llm-router-chatbot-web:local` | FastAPI app on port 8000 |
| `chatbot-mock-server` | `llm-router-chatbot-mock-server:local` | In-memory mock backend on port 3000 |
| `chatbot-postgres` | `postgres:16` | Database |
| `chatbot-redis` | `redis:7` | Session state and task queue |

Images are built directly on the EC2 instance from the repo (not pulled from ECR) because the ECR images are ARM64 and the EC2 instance is x86_64.

## Deploying a Change

SSH into the instance and run:

```bash
ssh -i ~/Downloads/llm-router-chatbot-key.pem ubuntu@13.206.195.19
cd ~/LLM-Router-Chatbot
git pull origin <branch>
sudo docker build -t llm-router-chatbot-web:local .
sudo docker compose --env-file .env.ec2 -f docker-compose.ec2.yml up -d --no-deps web
```

If DB schema changed, also run migrations:

```bash
sudo docker compose --env-file .env.ec2 -f docker-compose.ec2.yml --profile ops run --rm migrate
```

To check status:

```bash
sudo docker compose --env-file .env.ec2 -f docker-compose.ec2.yml ps
sudo docker logs chatbot-web --tail=50
curl http://127.0.0.1:8000/health
```

## Environment Variables on EC2

Stored in `~/LLM-Router-Chatbot/.env.ec2` on the instance. Key values:

- `APP_ENV=production`
- `OLLAMA_MODEL=gemma4:31b-cloud` — the LLM used for routing and planning
- `MOCK_SERVER_URL=http://chatbot-mock-server:3000` — internal Docker network URL
- `DATABASE_URL` — points to the local Postgres container
- `REDIS_URL` — points to the local Redis container

Do not commit `.env.ec2` to the repo. Use `.env.ec2.example` as the template.

## GitHub Repo

- **URL:** https://github.com/sinhanaman2701/LLM-Router-Chatbot
- **Default branch:** `main` (only branch — all work goes here via PRs)

## Contribution Rules

- **Never push directly to `main`.** All changes must go through a feature branch and a pull request.
- **Every PR must be reviewed and merged by someone other than the author.** Do not self-merge.
- Branch naming: `feat/<description>`, `fix/<description>`, `chore/<description>`.

## LLM Setup

- **Provider:** Ollama Cloud
- **Model:** `gemma4:31b-cloud`
- **Used for:** router classification and planner ReAct loop
- **Latency:** ~5–15 seconds per message (two LLM calls per turn — one for routing, one for planning)

The frontend poll timeout is set to 3 minutes (120 attempts × 1.5s) to handle slow model responses.

## Recent Features and Fixes

- **Inline date picker** — shown immediately when date is not set (no pill first)
- **Inline time slot picker** — shown after date is confirmed; displays all slots with unavailable ones greyed out
- **Change date / Change time pills** — shown when both fields are filled; Change date appears inside the time picker header
- **Router fast path** — "Set date to YYYY-MM-DD", "Change date to YYYY-MM-DD", "Set time to HH:MM", "Change time to HH:MM" bypass the LLM entirely for instant response
- **Mock server persistence** — bookings now persist in memory across requests within a session; get_my_bookings returns real data
- **No rate limit** — the hourly booking cap was removed entirely; the only gate is the confirmation step before each booking
- **Welcome message** — shown on login: lists available facilities and what the bot can do
- **Booking visibility fix** — "what are my bookings?" now reads the correct response shape from the mock server
- **Booking ID lookup** — mentioning a booking ID like `bk_123` returns that booking's details
- **Task cleared on success** — after a booking or cancellation succeeds, the active task is cleared so stale UI affordances don't linger

## Known Limitations

- **Mock server is in-memory** — restarting `chatbot-mock-server` resets all booking data
- **EC2 public IP is not static** — if the instance is stopped and started, the IP changes; update the URL accordingly
- **Single instance** — no redundancy; if the EC2 box goes down, the app is down
- **No auth beyond the mock** — login credentials are hardcoded in the mock server (`dn.user.a@gmail.com` / `password`)
- **Only facility booking is wired** — the router and planner support one capability; other domains (complaints, payments) are not yet implemented

## Test Credentials

- **Email:** dn.user.a@gmail.com
- **Password:** password
