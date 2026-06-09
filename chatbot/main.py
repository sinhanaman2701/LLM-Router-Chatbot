from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from chatbot.agents.harness.audit_logger import AuditLogger
from chatbot.agents.harness.harness import AgentHarness
from chatbot.agents.harness.policy_engine import PolicyEngine
from chatbot.agents.harness.policy_store import PolicyStore
from chatbot.agents.llm.llm_factory import LLMFactory
from chatbot.agents.planners.facility_planner import FacilityPlanner
from chatbot.agents.router.router_agent import RouterAgent
from chatbot.config import settings
from chatbot.db.connection import close_db_pool, create_db_pool
from chatbot.logging_config import configure_logging
from chatbot.memory.preferences import PreferencesManager
from chatbot.observability.metrics import ChatbotMetrics
from chatbot.observability.middleware import RequestContextMiddleware
from chatbot.routers import auth, chat, health, session, ui
from chatbot.services.api_adapter import ApiAdapter
from chatbot.services.mock_server_auth import MockServerAuth
from chatbot.services.response_synthesizer import ResponseSynthesizer
from chatbot.state.state_manager import StateManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    app.state.settings = settings
    app.state.db_pool = await create_db_pool()
    app.state.redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    app.state.metrics = ChatbotMetrics(namespace=settings.METRICS_NAMESPACE)
    app.state.mock_server_auth = MockServerAuth(app.state.redis, metrics=app.state.metrics)
    app.state.api_adapter = ApiAdapter(
        app.state.http_client,
        app.state.redis,
        app.state.mock_server_auth,
        metrics=app.state.metrics,
    )
    app.state.state_manager = StateManager(app.state.redis, metrics=app.state.metrics)

    llm_client = LLMFactory.get_llm_client()
    policy_store = PolicyStore(app.state.db_pool, app.state.redis)
    policy_engine = PolicyEngine(redis=app.state.redis, policy_store=policy_store, metrics=app.state.metrics)
    audit_logger = AuditLogger(db_pool=app.state.db_pool, metrics=app.state.metrics)
    harness = AgentHarness(
        policy_engine=policy_engine,
        audit_logger=audit_logger,
        api_adapter=app.state.api_adapter,
        redis=app.state.redis,
        metrics=app.state.metrics,
    )
    app.state.llm_client = llm_client
    app.state.audit_logger = audit_logger
    app.state.policy_store = policy_store
    app.state.harness = harness
    app.state.router_agent = RouterAgent(llm_client=llm_client, metrics=app.state.metrics)
    app.state.facility_planner = FacilityPlanner(llm_client, harness, app.state.state_manager)
    app.state.preferences_manager = PreferencesManager()
    app.state.synthesizer = ResponseSynthesizer()

    try:
        await app.state.mock_server_auth.get_cookie(app.state.http_client)
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.redis.aclose()
        await close_db_pool(app.state.db_pool)


app = FastAPI(title="Anacity Chatbot v2", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(ui.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(session.router)
app.include_router(health.router)
