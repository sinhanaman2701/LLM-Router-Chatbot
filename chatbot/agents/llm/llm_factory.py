from __future__ import annotations

from chatbot.agents.llm.base_client import BaseLLMClient
from chatbot.agents.llm.ollama_client import OllamaClient
from chatbot.config import settings


class LLMFactory:
    @staticmethod
    def get_llm_client() -> BaseLLMClient:
        return OllamaClient(
            host=settings.OLLAMA_BASE_URL,
            api_key=settings.OLLAMA_API_KEY,
            model=settings.OLLAMA_MODEL,
        )
