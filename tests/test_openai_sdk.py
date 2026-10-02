"""Exercise the real optional SDK against an in-memory HTTP transport, never OpenAI."""

import json

import pytest

from keywordmoves.builtin.openai_llm import OpenAILLM
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import LLMRequest

openai = pytest.importorskip("openai", reason="Install keywordmoves[openai] for SDK transport tests")


def install_transport(monkeypatch, handler):
    # HTTPX2 is the HTTP transport used by the supported OpenAI 3.x SDK.
    import httpx2

    constructor = openai.OpenAI
    clients = []

    def make_client(**kwargs):
        http_client = httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False)
        clients.append(http_client)
        return constructor(http_client=http_client, **kwargs)

    monkeypatch.setattr(openai, "OpenAI", make_client)
    return clients


def test_real_sdk_serializes_request_and_parses_response(monkeypatch):
    import httpx2

    seen = []

    def respond(request):
        seen.append(request)
        return httpx2.Response(200, json={
            "id": "resp-transport-test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "gpt-4.1-mini-2025-04-14",
            "output": [{
                "id": "msg-transport-test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text", "text": "paper planes, midnight sky", "annotations": [],
                }],
            }],
            "usage": {
                "input_tokens": 20, "output_tokens": 8, "total_tokens": 28,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        })

    clients = install_transport(monkeypatch, respond)
    result = OpenAILLM().generate(LLMRequest(
        task="test", prompt="Find keywords.", max_new_tokens=256,
        options={"api_key": "sk-sdk-transport-test", "max_retries": 0},
    ))
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/responses"
    assert seen[0].headers["authorization"] == "Bearer sk-sdk-transport-test"
    assert seen[0].method == "POST"
    assert json.loads(seen[0].content) == {
        "model": "gpt-4.1-mini", "input": "Find keywords.",
        "max_output_tokens": 256, "store": False,
    }
    assert result.text == "paper planes, midnight sky"
    assert result.metadata["usage"]["total_tokens"] == 28
    assert all(client.is_closed for client in clients)


def test_real_sdk_authentication_error_is_sanitized(monkeypatch):
    import httpx2

    def respond(request):
        return httpx2.Response(401, json={
            "error": {
                "message": "Incorrect API key: sk-sdk-transport-test",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            },
        })

    clients = install_transport(monkeypatch, respond)
    with pytest.raises(ConfigurationError, match="authentication failed") as caught:
        OpenAILLM().generate(LLMRequest(
            task="test", prompt="Find keywords.",
            options={"api_key": "sk-sdk-transport-test", "max_retries": 0},
        ))
    assert "sk-sdk-transport-test" not in str(caught.value)
    assert all(client.is_closed for client in clients)
