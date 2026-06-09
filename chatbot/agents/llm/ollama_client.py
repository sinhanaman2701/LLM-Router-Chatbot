from __future__ import annotations

import ollama

from chatbot.agents.llm.base_client import BaseLLMClient


class OllamaClient(BaseLLMClient):
    def __init__(self, host: str, api_key: str, model: str) -> None:
        self._client = ollama.AsyncClient(
            host=host,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self._model = model

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
    ) -> str:
        full_messages: list[dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        response = await self._client.chat(model=self._model, messages=full_messages)
        return response.message.content
