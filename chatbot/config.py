from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChatbotConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="forbid",
    )

    MOCK_SERVER_URL: str = Field(default="http://localhost:3000")
    MOCK_SERVER_EMAIL: str = Field(default="dn.user.a@gmail.com")
    MOCK_SERVER_PASSWORD: str = Field(default="password")
    SESSION_HMAC_SECRET: str = Field(default="local-dev-hmac-secret-change-me")
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/anacity_chatbot"
    )
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    OLLAMA_API_KEY: str = Field(default="")
    OLLAMA_BASE_URL: str = Field(default="https://ollama.com")
    OLLAMA_MODEL: str = Field(default="gemma4:31b-cloud")

    FACILITY_PLANNER_MAX_ITERATIONS: int = 5

    PLANNER_MEMORY_MAX_ENTRIES: int = 10
    PLANNER_MEMORY_TOKEN_BUDGET_PCT: float = 0.60

    SESSION_INACTIVITY_TTL: int = 1800
    SESSION_HARD_TTL: int = 14400
    REQUEST_RESULT_TTL: int = 300

    FACILITY_LIST_CACHE_TTL: int = 300
    POLICY_CACHE_TTL: int = 300
    API_AUTH_COOKIE_TTL: int = 7200
    PREFERENCES_CACHE_TTL: int = 86400

    ROUTER_CONFIDENCE_HIGH: float = 0.85
    ROUTER_CONFIDENCE_LOW: float = 0.70

    HARNESS_MAX_RETRIES: int = 3
    HARNESS_RETRY_BASE_SECONDS: float = 1.0

    CB_FAILURE_THRESHOLD: float = 0.50
    CB_MIN_SAMPLE_SIZE: int = 10
    CB_OPEN_DURATION_SECONDS: int = 30

    USER_RATE_LIMIT_PER_MINUTE: int = 10
    IP_AUTH_FAILURE_LIMIT: int = 10
    IP_AUTH_BLOCK_SECONDS: int = 60

    CONFIRMATION_TIMEOUT_TURNS: int = 2
    MAX_CONSECUTIVE_UNCLEAR: int = 3

    METRICS_ENABLED: bool = Field(default=True)
    METRICS_NAMESPACE: str = Field(default="chatbot")
    ALERT_P95_MS_THRESHOLD: int = Field(default=5000)
    ALERT_TOOL_ERROR_RATE_THRESHOLD: float = Field(default=0.05)
    ALERT_LOW_CONFIDENCE_RATE_THRESHOLD: float = Field(default=0.20)
    LOAD_TEST_CONCURRENT_SESSIONS: int = Field(default=50)


settings = ChatbotConfig()
