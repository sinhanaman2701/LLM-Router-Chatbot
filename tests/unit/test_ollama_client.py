from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chatbot.agents.llm.ollama_client import OllamaClient


@pytest.fixture
def mock_response():
    msg = MagicMock()
    msg.content = "Hello from model"
    resp = MagicMock()
    resp.message = msg
    return resp


@pytest.mark.asyncio
async def test_chat_no_system_prompt(mock_response):
    with patch("chatbot.agents.llm.ollama_client.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)

        client = OllamaClient(host="https://ollama.com", api_key="key123", model="gemma4:31b-cloud")
        result = await client.chat([{"role": "user", "content": "hi"}])

        assert result == "Hello from model"
        call_args = instance.chat.call_args
        messages_sent = call_args.kwargs["messages"]
        assert messages_sent[0]["role"] == "user"
        assert len(messages_sent) == 1


@pytest.mark.asyncio
async def test_chat_with_system_prompt(mock_response):
    with patch("chatbot.agents.llm.ollama_client.ollama.AsyncClient") as MockClient:
        instance = MockClient.return_value
        instance.chat = AsyncMock(return_value=mock_response)

        client = OllamaClient(host="https://ollama.com", api_key="key123", model="gemma4:31b-cloud")
        await client.chat([{"role": "user", "content": "hi"}], system_prompt="You are an assistant.")

        messages_sent = instance.chat.call_args.kwargs["messages"]
        assert messages_sent[0]["role"] == "system"
        assert messages_sent[0]["content"] == "You are an assistant."
        assert messages_sent[1]["role"] == "user"


def test_bearer_auth_header():
    with patch("chatbot.agents.llm.ollama_client.ollama.AsyncClient") as MockClient:
        OllamaClient(host="https://ollama.com", api_key="mykey", model="gemma4:31b-cloud")
        call_kwargs = MockClient.call_args.kwargs
        assert call_kwargs["headers"]["Authorization"] == "Bearer mykey"
