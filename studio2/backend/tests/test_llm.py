import json

import httpx
import pytest

from config import DEFAULT_CONFIG
from llm import FakeProvider, LlmClient, LlmError


def _cfg():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    return cfg


def test_fake_chat_is_deterministic():
    client = LlmClient(_cfg())
    first = client.chat("researcher", [{"role": "user", "content": "изучи экран"}])
    second = client.chat("researcher", [{"role": "user", "content": "изучи экран"}])
    assert first == second
    assert json.loads(first)["status"] == "done"


def test_stream_yields_chunks():
    chunks = list(LlmClient(_cfg()).stream("reviewer", [{"role": "user", "content": "x"}]))
    assert len(chunks) > 1
    assert json.loads("".join(chunks))["status"] == "done"


def test_ollama_uses_openai_path_and_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "привет"}}]})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "qwen3.5:9b", "temperature": 0.2}
    client = LlmClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("researcher", [{"role": "user", "content": "hi"}]) == "привет"
    assert seen["url"] == "http://localhost:11435/v1/chat/completions"
    assert seen["model"] == "qwen3.5:9b"


def test_gpustack_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    cfg = _cfg()
    cfg["providers"]["gpustack"]["api_key"] = "secret-key"
    cfg["roles"]["researcher"] = {"provider": "gpustack", "model": "deepseek", "temperature": 0.2}
    client = LlmClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("researcher", [{"role": "user", "content": "hi"}]) == "ok"
    assert seen["auth"] == "Bearer secret-key"


def test_http_error_raises_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "m", "temperature": 0.2}
    with pytest.raises(LlmError):
        LlmClient(cfg, transport=httpx.MockTransport(handler)).chat(
            "researcher", [{"role": "user", "content": "hi"}]
        )


def test_list_models_for_fake():
    assert FakeProvider.models() == ["fake-1"]


def test_list_models_from_ollama():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "qwen3-coder:30b"}, {"id": "gemma4:12b"}]})

    client = LlmClient(_cfg(), transport=httpx.MockTransport(handler))
    assert client.list_models("ollama") == ["qwen3-coder:30b", "gemma4:12b"]


def test_test_model_reports_error_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    cfg = _cfg()
    cfg["roles"]["reviewer"]["provider"] = "ollama"
    result = LlmClient(cfg, transport=httpx.MockTransport(handler)).test_model("reviewer")
    assert result["ok"] is False
    assert result["provider"] == "ollama"


def test_reasoning_field_is_used_when_content_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "",
            "reasoning": '{"status": "done", "artifacts": [], "summary": "ok"}',
        }}]})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "m", "temperature": 0.2}
    reply = LlmClient(cfg, transport=httpx.MockTransport(handler)).chat(
        "researcher", [{"role": "user", "content": "hi"}]
    )
    assert json.loads(reply)["status"] == "done"


def test_payload_limits_max_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["max_tokens"] = json.loads(request.content).get("max_tokens")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "m", "temperature": 0.2}
    LlmClient(cfg, transport=httpx.MockTransport(handler)).chat(
        "researcher", [{"role": "user", "content": "hi"}]
    )
    assert isinstance(seen["max_tokens"], int) and seen["max_tokens"] > 0
