"""Тесты дня 30: роуты шлюза локальной LLM как сетевого сервиса.

Офлайн: `httpx.MockTransport` вместо Ollama, tmp-каталог данных
(реальный `studio/data` не трогается), TestClient как «клиент из сети».
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main as main_mod
from agent import StudioAgent
from gateway import (GatewayConfig, GatewayService, OllamaTransport,
                     UsageStore, hash_key)
from kb import KnowledgeBase

MODELS = ["gemma3:27b", "qwen3-coder:30b"]

ANSWER = "Ответ локальной модели."


def _handler(captured=None, answer=ANSWER, status=200, fail_stream=False):
    """Мок Ollama: OpenAI-совместимый /v1/chat/completions (+ SSE)."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode() or "{}")
        if captured is not None:
            captured.append(body)
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        if body.get("stream"):
            if fail_stream:
                return httpx.Response(500, json={"error": "boom"})
            chunks = [
                {"id": "c1", "object": "chat.completion.chunk", "model":
                 body.get("model"),
                 "choices": [{"index": 0, "delta": {"role": "assistant",
                                                    "content": "При"}}]},
                {"id": "c1", "object": "chat.completion.chunk", "model":
                 body.get("model"),
                 "choices": [{"index": 0, "delta": {"content": "вет"}}]},
                {"id": "c1", "object": "chat.completion.chunk", "model":
                 body.get("model"), "choices": [],
                 "usage": {"prompt_tokens": 12, "completion_tokens": 4,
                           "total_tokens": 16}},
            ]
            text = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n"
                           for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text,
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "chatcmpl-1", "object": "chat.completion",
            "model": body.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 6,
                      "total_tokens": 17},
        })
    return handler


def _make_client(tmp_path, captured=None, **client_kw):
    """TestClient со шлюзом на моках; возвращает (client, plaintext_key)."""
    agent = StudioAgent(str(tmp_path))
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", **client_kw)
    http = httpx.Client(transport=httpx.MockTransport(_handler(captured)))
    svc = GatewayService(
        config=cfg,
        usage=UsageStore(tmp_path / "gateway_usage.json"),
        transport=OllamaTransport("http://ollama.test/v1"),
        models_provider=lambda: list(MODELS),
    )
    # Транспорт мока подменяем на httpx.MockTransport-совместимый.
    svc.transport = _MockTransport(_handler(captured))
    svc.summary_transport = svc.transport
    app = main_mod.create_app(agent=agent, kb=KnowledgeBase(str(tmp_path / "kb")),
                              gateway=svc)
    return TestClient(app), key


class _MockTransport:
    """Транспорт шлюза поверх httpx.MockTransport (те же вызовы chat/stream)."""

    def __init__(self, handler):
        self._client = httpx.Client(transport=httpx.MockTransport(handler))

    def chat(self, body, base_url=None, api_key=None):
        resp = self._client.post("http://ollama.test/v1/chat/completions",
                                 json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp.json()

    def stream(self, body, base_url=None, api_key=None):
        client = httpx.Client(
            transport=httpx.MockTransport(_handler(None)))
        req = client.build_request(
            "POST", "http://ollama.test/v1/chat/completions", json=body)
        resp = client.send(req, stream=True)
        if resp.status_code != 200:
            resp.close()
            client.close()
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp, client


def _auth(key):
    return {"Authorization": f"Bearer {key}"}


def _body(content="привет", **kw):
    body = {"model": "gemma3:27b",
            "messages": [{"role": "user", "content": content}]}
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

class TestAuth:
    def test_missing_key_401(self, tmp_path):
        client, _ = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body())
        assert r.status_code == 401
        err = r.json()["error"]
        assert err["code"] == "missing_api_key"
        assert "Authorization" in err["message"]

    def test_bad_key_401(self, tmp_path):
        client, _ = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(),
                        headers=_auth("sk-local-nope"))
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_api_key"

    def test_valid_key_200(self, tmp_path):
        client, key = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 200
        assert r.json()["choices"][0]["message"]["content"] == ANSWER

    def test_disabled_client_403(self, tmp_path):
        client, key = _make_client(tmp_path, enabled=False)
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "client_disabled"

    def test_error_shape_is_openai(self, tmp_path):
        client, _ = _make_client(tmp_path)
        err = client.post("/v1/chat/completions", json=_body()).json()["error"]
        assert set(err) >= {"message", "type", "code"}


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------

class TestModels:
    def test_models_list_openai_format(self, tmp_path):
        client, _ = _make_client(tmp_path)
        j = client.get("/v1/models").json()
        assert j["object"] == "list"
        assert [m["id"] for m in j["data"]] == MODELS

    def test_unknown_model_404(self, tmp_path):
        client, key = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(model="nope:1b"),
                        headers=_auth(key))
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "model_not_found"

    def test_disallowed_model_403(self, tmp_path):
        client, key = _make_client(tmp_path, allow_models=["qwen3-coder:30b"])
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "model_not_allowed"

    def test_allowed_model_ok(self, tmp_path):
        client, key = _make_client(tmp_path, allow_models=["gemma3:*"])
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 200

    def test_missing_messages_400(self, tmp_path):
        client, key = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json={"model": "gemma3:27b"},
                        headers=_auth(key))
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_messages"


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_rpm_429_with_retry_after(self, tmp_path):
        client, key = _make_client(tmp_path, rpm=2)
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 200
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 200
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limit_exceeded"
        assert int(r.headers["retry-after"]) >= 1

    def test_tpm_429(self, tmp_path):
        client, key = _make_client(tmp_path, rpm=0, tpm=5)
        r = client.post("/v1/chat/completions",
                        json=_body("x" * 4000), headers=_auth(key))
        assert r.status_code == 429

    def test_rejection_recorded(self, tmp_path):
        client, key = _make_client(tmp_path, rpm=1)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        j = client.get("/api/gateway/usage").json()
        assert j["usage"]["phone"]["rejected"] == 1

    def test_rejection_log_lists_code(self, tmp_path):
        client, key = _make_client(tmp_path, rpm=1)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        log = client.get("/api/gateway/rejections").json()["rejections"]
        assert log and log[-1]["code"] == "rate_limit_exceeded"

    def test_stream_cannot_bypass_rate_limit(self, tmp_path):
        """Обойти лимит через stream: true нельзя."""
        client, key = _make_client(tmp_path, rpm=1)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        r = client.post("/v1/chat/completions", json=_body(stream=True),
                        headers=_auth(key))
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# Max context и компрессия
# ---------------------------------------------------------------------------

class TestContext:
    def test_compression_header_present(self, tmp_path):
        client, key = _make_client(tmp_path, max_context=100000)
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.headers["x-gateway-compressed"] == "false"
        assert r.headers["x-gateway-client"] == "phone"

    def test_gateway_block_in_response(self, tmp_path):
        client, key = _make_client(tmp_path)
        gw = client.post("/v1/chat/completions", json=_body(),
                         headers=_auth(key)).json()["gateway"]
        assert gw["client"] == "phone"
        assert gw["compressed"] is False
        assert gw["max_context"] == 8192
        assert gw["compress_rounds"] == 0

    def test_long_dialog_compressed(self, tmp_path):
        captured = []
        client, key = _make_client(tmp_path, captured, max_context=4000)
        msgs = []
        for i in range(30):
            msgs.append({"role": "user", "content": f"вопрос {i} " + "x" * 600})
            msgs.append({"role": "assistant",
                         "content": f"ответ {i} " + "y" * 600})
        msgs.append({"role": "user", "content": "итог?"})
        r = client.post("/v1/chat/completions",
                        json={"model": "gemma3:27b", "messages": msgs},
                        headers=_auth(key))
        assert r.status_code == 200
        j = r.json()
        assert j["gateway"]["compressed"] is True
        assert r.headers["x-gateway-compressed"] == "true"
        assert j["gateway"]["tokens_after_compression"] <= 4000
        assert j["gateway"]["tokens_before_compression"] > \
            j["gateway"]["tokens_after_compression"]
        # Последний вызов наверх — уже сжатый.
        assert len(captured[-1]["messages"]) < len(msgs)

    def test_compression_keeps_last_message(self, tmp_path):
        """Компрессия не должна терять последний вопрос пользователя."""
        captured = []
        client, key = _make_client(tmp_path, captured, max_context=4000)
        msgs = []
        for i in range(30):
            msgs.append({"role": "user", "content": f"вопрос {i} " + "x" * 600})
            msgs.append({"role": "assistant",
                         "content": f"ответ {i} " + "y" * 600})
        msgs.append({"role": "user", "content": "САМЫЙ ПОСЛЕДНИЙ ВОПРОС"})
        client.post("/v1/chat/completions",
                    json={"model": "gemma3:27b", "messages": msgs},
                    headers=_auth(key))
        sent = captured[-1]["messages"]
        assert sent[-1]["content"] == "САМЫЙ ПОСЛЕДНИЙ ВОПРОС"

    def test_413_when_tail_alone_exceeds_limit(self, tmp_path):
        """Если последние сообщения сами больше лимита — честный 413."""
        client, key = _make_client(tmp_path, max_context=100)
        msgs = [{"role": "user", "content": "x" * 4000},
                {"role": "assistant", "content": "y" * 4000},
                {"role": "user", "content": "z" * 4000}]
        r = client.post("/v1/chat/completions",
                        json={"model": "gemma3:27b", "messages": msgs},
                        headers=_auth(key))
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "context_length_exceeded"

    def test_413_when_uncompressible(self, tmp_path):
        client, key = _make_client(tmp_path, max_context=5)
        msgs = [{"role": "user", "content": "x" * 5000}]
        r = client.post("/v1/chat/completions",
                        json={"model": "gemma3:27b", "messages": msgs},
                        headers=_auth(key))
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "context_length_exceeded"

    def test_output_cap_applied(self, tmp_path):
        captured = []
        client, key = _make_client(tmp_path, captured, max_output_tokens=32)
        client.post("/v1/chat/completions", json=_body(max_tokens=9999),
                    headers=_auth(key))
        assert captured[-1]["max_tokens"] == 32

    def test_no_compression_for_short_dialog(self, tmp_path):
        captured = []
        client, key = _make_client(tmp_path, captured, max_context=100000)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert len(captured) == 1  # только сам запрос, без сводки


# ---------------------------------------------------------------------------
# Учёт
# ---------------------------------------------------------------------------

class TestUsage:
    def test_usage_counts_request(self, tmp_path):
        client, key = _make_client(tmp_path)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        j = client.get("/api/gateway/usage").json()
        assert j["usage"]["phone"]["requests"] == 1
        assert j["totals"]["requests"] == 1

    def test_usage_tokens_from_upstream(self, tmp_path):
        client, key = _make_client(tmp_path)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        u = client.get("/api/gateway/usage").json()["usage"]["phone"]
        assert u["tokens_in"] == 11 and u["tokens_out"] == 6
        assert u["tokens_total"] == 17

    def test_usage_survives_restart(self, tmp_path):
        client, key = _make_client(tmp_path)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        # Новый сервис на тех же файлах = рестарт процесса.
        client2, key2 = _make_client(tmp_path)
        assert key2 != key  # новый клиент, но usage-файл тот же
        j = client2.get("/api/gateway/usage").json()
        assert j["usage"]["phone"]["requests"] == 1

    def test_reset_clears_counters(self, tmp_path):
        client, key = _make_client(tmp_path)
        client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        client.post("/api/gateway/reset", json={"name": "phone"})
        j = client.get("/api/gateway/usage").json()
        # После сброса запись исчезает (нули по клиенту не хранятся).
        assert j["usage"].get("phone") is None
        assert j["totals"]["requests"] == 0

    def test_reset_requires_name(self, tmp_path):
        client, _ = _make_client(tmp_path)
        assert client.post("/api/gateway/reset", json={}).status_code == 400


# ---------------------------------------------------------------------------
# Управление клиентами
# ---------------------------------------------------------------------------

class TestClients:
    def test_list_clients_masks_key(self, tmp_path):
        client, key = _make_client(tmp_path)
        j = client.get("/api/gateway/clients").json()
        assert len(j["clients"]) == 1
        c = j["clients"][0]
        assert "key_hash" not in c
        assert key not in json.dumps(j)

    def test_create_client_returns_key_once(self, tmp_path):
        client, _ = _make_client(tmp_path)
        r = client.post("/api/gateway/clients",
                        json={"name": "laptop", "rpm": 30, "max_context": 4096})
        assert r.status_code == 200
        j = r.json()
        assert j["key"].startswith("sk-local-")
        assert j["rpm"] == 30 and j["max_context"] == 4096
        # Повторный запрос ключ НЕ возвращает.
        r2 = client.post("/api/gateway/clients", json={"name": "laptop"})
        assert r2.json()["key"] is None

    def test_new_client_can_chat(self, tmp_path):
        client, _ = _make_client(tmp_path)
        key = client.post("/api/gateway/clients",
                          json={"name": "laptop"}).json()["key"]
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 200

    def test_created_client_is_isolated(self, tmp_path):
        """У нового клиента свои лимиты и свой учёт."""
        client, main_key = _make_client(tmp_path)
        key = client.post("/api/gateway/clients",
                          json={"name": "laptop", "rpm": 1}).json()["key"]
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 200
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 429
        # Основной клиент не затронут.
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(main_key)).status_code == 200

    def test_delete_client(self, tmp_path):
        client, _ = _make_client(tmp_path)
        key = client.post("/api/gateway/clients",
                          json={"name": "laptop"}).json()["key"]
        assert client.delete("/api/gateway/clients/laptop").status_code == 200
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 401

    def test_delete_unknown_404(self, tmp_path):
        client, _ = _make_client(tmp_path)
        assert client.delete("/api/gateway/clients/nope").status_code == 404

    def test_create_without_name_400(self, tmp_path):
        client, _ = _make_client(tmp_path)
        assert client.post("/api/gateway/clients", json={}).status_code == 400

    def test_create_bad_limit_400(self, tmp_path):
        client, _ = _make_client(tmp_path)
        r = client.post("/api/gateway/clients",
                        json={"name": "x", "rpm": "много"})
        assert r.status_code == 400

    def test_rotate_key_invalidates_old(self, tmp_path):
        client, _ = _make_client(tmp_path)
        old = client.post("/api/gateway/clients",
                          json={"name": "laptop"}).json()["key"]
        new = client.post("/api/gateway/clients",
                          json={"name": "laptop", "rotate_key": True}).json()["key"]
        assert new != old
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(old)).status_code == 401
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(new)).status_code == 200


# ---------------------------------------------------------------------------
# Статус
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_has_lan_url(self, tmp_path):
        client, _ = _make_client(tmp_path)
        j = client.get("/api/gateway/status").json()
        assert j["lan_url"].endswith("/v1")
        assert j["chat_url"].endswith("/v1/chat/completions")
        assert j["models"] == MODELS
        assert j["tls"] is False

    def test_status_warns_about_lan_only(self, tmp_path):
        client, _ = _make_client(tmp_path)
        j = client.get("/api/gateway/status").json()
        assert "локальн" in j["note"].lower()


# ---------------------------------------------------------------------------
# Стриминг
# ---------------------------------------------------------------------------

class TestStreaming:
    def test_stream_returns_sse(self, tmp_path):
        client, key = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(stream=True),
                        headers=_auth(key))
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        assert "data: [DONE]" in r.text
        assert "При" in r.text and "вет" in r.text

    def test_stream_requires_key_401(self, tmp_path):
        client, _ = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(stream=True))
        assert r.status_code == 401

    def test_stream_has_compression_header(self, tmp_path):
        client, key = _make_client(tmp_path)
        r = client.post("/v1/chat/completions", json=_body(stream=True),
                        headers=_auth(key))
        assert r.headers["x-gateway-compressed"] == "false"

    def test_stream_records_usage(self, tmp_path):
        client, key = _make_client(tmp_path)
        client.post("/v1/chat/completions", json=_body(stream=True),
                    headers=_auth(key))
        u = client.get("/api/gateway/usage").json()["usage"]["phone"]
        assert u["requests"] == 1
        assert u["tokens_total"] == 16  # из финального кадра usage

    def test_stream_records_rejection_on_rate_limit(self, tmp_path):
        client, key = _make_client(tmp_path, rpm=1)
        client.post("/v1/chat/completions", json=_body(stream=True),
                    headers=_auth(key))
        r = client.post("/v1/chat/completions", json=_body(stream=True),
                        headers=_auth(key))
        assert r.status_code == 429
        u = client.get("/api/gateway/usage").json()["usage"]["phone"]
        assert u["rejected"] == 1


class TestUpstreamFailure:
    def test_502_when_ollama_down(self, tmp_path):
        agent = StudioAgent(str(tmp_path))
        cfg = GatewayConfig(tmp_path / "gateway.json")
        _, key = cfg.upsert("phone")
        svc = GatewayService(
            config=cfg, usage=UsageStore(tmp_path / "u.json"),
            transport=_FailingTransport(),
            models_provider=lambda: list(MODELS))
        app = main_mod.create_app(agent=agent,
                                  kb=KnowledgeBase(str(tmp_path / "kb")),
                                  gateway=svc)
        client = TestClient(app)
        r = client.post("/v1/chat/completions", json=_body(), headers=_auth(key))
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "upstream_error"

    def test_502_does_not_burn_tpm(self, tmp_path):
        """Упавший вызов не жжёт TPM: токены не были потрачены.

        RPM при этом расходуется — запрос действительно пришёл в шлюз;
        иначе сбойная модель позволяла бы слать бесконечный поток
        запросов без ограничения частоты.
        """
        agent = StudioAgent(str(tmp_path))
        cfg = GatewayConfig(tmp_path / "gateway.json")
        _, key = cfg.upsert("phone", rpm=0, tpm=100)
        svc = GatewayService(
            config=cfg, usage=UsageStore(tmp_path / "u.json"),
            transport=_FailingTransport(), models_provider=lambda: list(MODELS))
        app = main_mod.create_app(agent=agent,
                                  kb=KnowledgeBase(str(tmp_path / "kb")),
                                  gateway=svc)
        client = TestClient(app)
        big = "x" * 300  # ~75 токенов на запрос
        for _ in range(3):
            r = client.post("/v1/chat/completions", json=_body(big),
                            headers=_auth(key))
            assert r.status_code == 502
        # TPM=100: если бы упавшие вызовы жгли токены, тут был бы 429.
        assert client.post("/v1/chat/completions", json=_body(big),
                           headers=_auth(key)).status_code == 502

    def test_502_counts_against_rpm(self, tmp_path):
        agent = StudioAgent(str(tmp_path))
        cfg = GatewayConfig(tmp_path / "gateway.json")
        _, key = cfg.upsert("phone", rpm=1)
        svc = GatewayService(
            config=cfg, usage=UsageStore(tmp_path / "u.json"),
            transport=_FailingTransport(), models_provider=lambda: list(MODELS))
        app = main_mod.create_app(agent=agent,
                                  kb=KnowledgeBase(str(tmp_path / "kb")),
                                  gateway=svc)
        client = TestClient(app)
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 502
        assert client.post("/v1/chat/completions", json=_body(),
                           headers=_auth(key)).status_code == 429

    def test_stream_upstream_failure_emits_sse_error(self, tmp_path):
        agent = StudioAgent(str(tmp_path))
        cfg = GatewayConfig(tmp_path / "gateway.json")
        _, key = cfg.upsert("phone")
        svc = GatewayService(
            config=cfg, usage=UsageStore(tmp_path / "u.json"),
            transport=_FailingTransport(), models_provider=lambda: list(MODELS))
        # Стриминговый транспорт, который падает на старте.
        svc.transport = _FailingStreamTransport()
        app = main_mod.create_app(agent=agent,
                                  kb=KnowledgeBase(str(tmp_path / "kb")),
                                  gateway=svc)
        client = TestClient(app)
        r = client.post("/v1/chat/completions", json=_body(stream=True),
                        headers=_auth(key))
        assert r.status_code == 200
        assert "upstream_error" in r.text
        assert "data: [DONE]" in r.text


class _FailingTransport:
    def chat(self, body, base_url=None, api_key=None):
        raise RuntimeError("Connection refused")

    def stream(self, body, base_url=None, api_key=None):
        raise RuntimeError("Connection refused")


class _FailingStreamTransport(_FailingTransport):
    pass


class TestStatusPort:
    def test_status_reflects_actual_port(self, tmp_path):
        """Адрес для клиентов должен указывать на реально занятый порт.

        Регрессия: при запуске на нестандартном порту status отдавал
        DEFAULT_GATEWAY_PORT, и клиент получал неверный URL.
        """
        client, _ = _make_client(tmp_path)
        # TestClient по умолчанию ходит на http://testserver (порт 80/None)
        # — используем base_url с явным портом.
        r = client.get("/api/gateway/status",
                       headers={"host": "192.168.1.5:8123"})
        assert r.status_code == 200
        assert ":8123" in r.json()["chat_url"], r.json()["chat_url"]

    def test_status_env_port_overrides(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GATEWAY_PORT", "9999")
        client, _ = _make_client(tmp_path)
        j = client.get("/api/gateway/status").json()
        assert ":9999" in j["chat_url"], j["chat_url"]

    def test_status_lan_host_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GATEWAY_HOST", "10.0.0.7")
        client, _ = _make_client(tmp_path)
        j = client.get("/api/gateway/status").json()
        assert "10.0.0.7" in j["chat_url"], j["chat_url"]
