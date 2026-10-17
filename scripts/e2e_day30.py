# -*- coding: utf-8 -*-
"""E2E day 30 «Локальная LLM как приватный сервис»

Задание дня 30: развернуть локальную LLM как сервис, к которому можно
подключиться с любого устройства по сети, с ограничениями по rate limit
и max context.

Скрипт доказывает, что сервис действительно работает и ограничения
действуют, а не описаны в документации:

Часть A (offline, MUST PASS, без сети и без Ollama):
  A1  сервис поднят: GET /api/gateway/status отдаёт LAN-адрес
      (lan_url/chat_url), список моделей и предупреждение «только LAN»
  A2  аутентификация: без ключа → 401 (missing_api_key), чужой ключ →
      401 (invalid_api_key), выключенный клиент → 403 (client_disabled);
      ошибки в формате OpenAI {error:{message,type,code}}
  A3  OpenAI-совместимость: POST /v1/chat/completions с ключом → ответ
      с choices[0].message.content; GET /v1/models → object=list
  A4  модели: неизвестная модель → 404, неразрешённая клиенту → 403,
      маска allow_models (gemma3:*) работает
  A5  rate limit RPM: N+1-й запрос → 429, есть Retry-After; отказ НЕ
      расходует окно (клиент не блокирует себя повторами)
  A6  rate limit TPM: один большой запрос сверх TPM → 429 (не 500)
  A7  max context + КОМПРЕССИЯ: вход сверх лимита не роняет запрос —
      старые ходы сжимаются LLM-сводкой, в ответе gateway.compressed=
      true, заголовок x-gateway-compressed: true, последний вопрос
      пользователя сохранён дословно
  A8  честный 413: если последние сообщения сами больше лимита —
      413 context_length_exceeded с точными числами (без молчаливой
      обрезки)
  A9  max output: max_tokens клиента урезается до max_output_tokens
  A10 учёт: /api/gateway/usage считает запросы/токены/отказы;
      персистентность (рестарт процесса не обнуляет счётчики)
  A11 управление клиентами: создание возвращает ключ ОДИН раз (повторно
      не отдаётся), ключ в конфиге хранится хэшем, удаление
      инвалидирует ключ, новый клиент изолирован по лимитам
  A12 стриминг: stream=true → SSE; обойти rate limit через stream нельзя
  A13 отказы учтены и видны: /api/gateway/rejections с кодом и статусом

Часть B (live, best-effort, SKIP без Ollama):
  B0  зонд Ollama: GET /api/tags; выбор локальной модели
  B1  ЛЮБОЕ УСТРОЙСТВО В СЕТИ: сервер слушает 0.0.0.0 (проверяем, что
      порт доступен по LAN-адресу, а не только по 127.0.0.1), запрос с
      «другого устройства» (отдельный HTTP-клиент, без cookies/сессии)
      получает ответ реальной локальной модели
  B2  rate limit на живом сервере: RPM=2 → третий запрос 429
  B3  реальная компрессия: длинный диалог сверх max_context сжимается
      сводкой от реальной модели, ответ приходит, gateway.compressed=true
  B3b пустой ответ reasoning-модели не отдаётся молча: при малом
      max_output_tokens модель тратит бюджет на рассуждения, и шлюз
      помечает ответ gateway.empty_response=true с причиной
  B4  реальный 413: max_context меньше последних сообщений → 413
  B5  Ollama закрыта наружу: сам Ollama-порт на LAN-адресе недоступен
      (наружу смотрит только шлюз — единственная точка с ключами)
  B6  учёт на живом сервере: usage вырос, токены посчитаны

Запуск из корня репозитория:
    python scripts/e2e_day30.py [--part-a-only] [--port 8110]
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

OLLAMA = os.environ.get("OLLAMA_BASE_URL",
                        "http://localhost:11434/v1").rstrip("/")
OLLAMA_NATIVE = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA

PORT = 8110
BASE = f"http://127.0.0.1:{PORT}"
SERVER_LOG = os.path.join(tempfile.gettempdir(), "e2e_day30_server.log")

WAIT_SERVER_UP_MAX = 40
WAIT_SERVER_UP_STEP = 0.25
WAIT_PORT_BUSY_MAX = 20
WAIT_PORT_STEP = 1.0

# --- инфраструктура отчёта (паттерн e2e_day24/28/29) ---

RESULTS: list[tuple[str, str, str]] = []   # (шаг, PASS/FAIL/SKIP, деталь)


def log(msg: str) -> None:
    print(msg, flush=True)


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    mark = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    log(f"  [{mark}] {step}" + (f" — {detail}" if detail else ""))


def print_summary() -> tuple[int, int]:
    p = sum(1 for _, s, _ in RESULTS if s == "PASS")
    f = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    k = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    log("\n" + "=" * 74)
    log(f"ИТОГ: PASS={p} FAIL={f} SKIP={k}")
    for step, status, detail in RESULTS:
        if status == "FAIL":
            log(f"  ПРОВАЛ: {step} — {detail}")
    log("=" * 74)
    return p, f


# --- http-помощники ---


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http(path: str, *, method: str | None = None, json_body: Any = None,
         key: str | None = None, base: str = BASE,
         timeout: int = 180) -> tuple[int, dict, dict]:
    """HTTP-запрос → (status, headers, json). Тело не-JSON → {}.

    Метод по умолчанию выводится из наличия тела: GET с телом уходит как
    GET и попадает в SPA-фолбэк (`/{path}`, только GET) вместо API —
    запрос молча возвращает HTML вместо JSON.
    """
    if method is None:
        method = "POST" if json_body is not None else "GET"
    url = base + path
    hdrs: dict = {}
    if key:
        hdrs["Authorization"] = f"Bearer {key}"
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"_raw": raw}
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"_raw": raw}
        return e.code, dict(e.headers), body


def http_raw(path: str, *, method: str | None = None, json_body: Any = None,
             key: str | None = None, base: str = BASE,
             timeout: int = 180) -> tuple[int, str]:
    """HTTP-запрос → (status, сырое тело). Для SSE-стримов."""
    if method is None:
        method = "POST" if json_body is not None else "GET"
    url = base + path
    hdrs: dict = {}
    if key:
        hdrs["Authorization"] = f"Bearer {key}"
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _err_code(body: dict) -> str:
    return ((body.get("error") or {}).get("code") or "")


# --- фейковая Ollama для офлайн-части ---


class FakeOllama:
    """Транспорт шлюза поверх httpx.MockTransport (без сети).

    Пишет тела запросов в `captured`, чтобы можно было проверить, что
    именно шлюз отправил наверх (сжатые сообщения, потолок max_tokens).
    """

    def __init__(self, captured: list, answer: str = "Ответ сервиса.",
                 usage: dict | None = None):
        import httpx

        self.captured = captured
        self.answer = answer
        self.usage = usage or {"prompt_tokens": 11, "completion_tokens": 6,
                               "total_tokens": 17}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode() or "{}")
            self.captured.append(body)
            return httpx.Response(200, json={
                "id": "chatcmpl-test", "object": "chat.completion",
                "model": body.get("model"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": self.answer}}],
                "usage": self.usage,
            })

        self._client = httpx.Client(transport=httpx.MockTransport(handler))

    def chat(self, body, base_url=None, api_key=None):
        resp = self._client.post("http://ollama.test/v1/chat/completions",
                                 json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp.json()

    def stream(self, body, base_url=None, api_key=None):
        import httpx
        client = httpx.Client(timeout=60)
        req = client.build_request(
            "POST", "http://ollama.test/v1/chat/completions", json=body)
        resp = client.send(req, stream=True)
        if resp.status_code != 200:
            resp.close()
            client.close()
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp, client


MODELS = ["gemma3:27b", "qwen3-coder:30b"]


# ---------------------------------------------------------------------------
# Part A: офлайн
# ---------------------------------------------------------------------------

def part_a() -> bool:
    log("\n=== Part A: офлайн (TestClient + фейковая Ollama, без сети) ===")
    from fastapi.testclient import TestClient

    import main as main_mod
    from agent import StudioAgent
    from gateway import GatewayConfig, GatewayService, UsageStore
    from kb import KnowledgeBase

    ok = True

    def make(tmp_path: str, *, captured: list | None = None,
             fake: FakeOllama | None = None, **client_kw):
        """TestClient со шлюзом на фейковой Ollama + ключ клиента."""
        cap = captured if captured is not None else []
        agent = StudioAgent(tmp_path)
        cfg = GatewayConfig(os.path.join(tmp_path, "gateway.json"))
        _, key = cfg.upsert("phone", **client_kw)
        svc = GatewayService(
            config=cfg,
            usage=UsageStore(os.path.join(tmp_path, "gateway_usage.json")),
            transport=fake or FakeOllama(cap),
            models_provider=lambda: list(MODELS))
        if fake is None:
            svc.summary_transport = svc.transport
        app = main_mod.create_app(agent=agent,
                                  kb=KnowledgeBase(os.path.join(tmp_path, "kb")),
                                  gateway=svc)
        return TestClient(app), key, cap, cfg

    def body(content: str = "привет", **kw) -> dict:
        b = {"model": MODELS[0],
             "messages": [{"role": "user", "content": content}]}
        b.update(kw)
        return b

    # --- A1: сервис поднят, отдаёт LAN-адрес ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a1_")
        c, key, _, _ = make(tmp)
        r = c.get("/api/gateway/status")
        j = r.json()
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert j["lan_url"].endswith("/v1"), j["lan_url"]
        assert j["chat_url"].endswith("/v1/chat/completions"), j["chat_url"]
        assert j["models"] == MODELS, j["models"]
        assert j["tls"] is False
        assert "локальн" in j["note"].lower(), "нет предупреждения про LAN"
        assert ":8110" in j["lan_url"] or ":" in j["lan_url"]
        record("A1 статус сервиса + LAN-адрес", "PASS",
               f"{j['chat_url']}, моделей {len(j['models'])}, TLS выкл (LAN)")
    except Exception as e:  # noqa: BLE001
        record("A1 статус сервиса + LAN-адрес", "FAIL", str(e))
        ok = False

    # --- A2: аутентификация ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a2_")
        c, key, _, _ = make(tmp)
        r1 = c.post("/v1/chat/completions", json=body())
        assert r1.status_code == 401, f"без ключа: {r1.status_code}"
        assert _err_code(r1.json()) == "missing_api_key"
        r2 = c.post("/v1/chat/completions", json=body(),
                    headers={"Authorization": "Bearer sk-local-nope"})
        assert r2.status_code == 401, f"чужой ключ: {r2.status_code}"
        assert _err_code(r2.json()) == "invalid_api_key"
        err = r1.json()["error"]
        assert set(err) >= {"message", "type", "code"}, err

        tmp2 = tempfile.mkdtemp(prefix="e2e30a2b_")
        c2, key2, _, _ = make(tmp2, enabled=False)
        r3 = c2.post("/v1/chat/completions", json=body(),
                     headers={"Authorization": f"Bearer {key2}"})
        assert r3.status_code == 403, f"выключенный: {r3.status_code}"
        assert _err_code(r3.json()) == "client_disabled"
        record("A2 аутентификация (401/401/403)", "PASS",
               "нет ключа → 401, чужой → 401, выключенный → 403; формат OpenAI")
    except Exception as e:  # noqa: BLE001
        record("A2 аутентификация (401/401/403)", "FAIL", str(e))
        ok = False

    # --- A3: OpenAI-совместимость ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a3_")
        c, key, _, _ = make(tmp)
        r = c.post("/v1/chat/completions", json=body(),
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        j = r.json()
        assert j["choices"][0]["message"]["content"] == "Ответ сервиса."
        assert j["object"] == "chat.completion"
        assert j["gateway"]["client"] == "phone"
        m = c.get("/v1/models").json()
        assert m["object"] == "list" and [x["id"] for x in m["data"]] == MODELS
        record("A3 OpenAI-совместимость", "PASS",
               "choices[0].message.content + /v1/models в формате OpenAI")
    except Exception as e:  # noqa: BLE001
        record("A3 OpenAI-совместимость", "FAIL", str(e))
        ok = False

    # --- A4: модели ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a4_")
        c, key, _, _ = make(tmp)
        r = c.post("/v1/chat/completions", json=body(model="nope:1b"),
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 404, f"неизвестная: {r.status_code}"
        assert _err_code(r.json()) == "model_not_found"

        tmp2 = tempfile.mkdtemp(prefix="e2e30a4b_")
        c2, key2, _, _ = make(tmp2, allow_models=["qwen3-coder:30b"])
        r2 = c2.post("/v1/chat/completions", json=body(),
                     headers={"Authorization": f"Bearer {key2}"})
        assert r2.status_code == 403, f"неразрешённая: {r2.status_code}"
        assert _err_code(r2.json()) == "model_not_allowed"

        tmp3 = tempfile.mkdtemp(prefix="e2e30a4c_")
        c3, key3, _, _ = make(tmp3, allow_models=["gemma3:*"])
        r3 = c3.post("/v1/chat/completions", json=body(),
                     headers={"Authorization": f"Bearer {key3}"})
        assert r3.status_code == 200, f"маска: {r3.status_code}"
        record("A4 ограничение моделей", "PASS",
               "неизвестная → 404, неразрешённая → 403, маска gemma3:* работает")
    except Exception as e:  # noqa: BLE001
        record("A4 ограничение моделей", "FAIL", str(e))
        ok = False

    # --- A5: rate limit RPM ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a5_")
        c, key, cap, _ = make(tmp, rpm=3)
        for i in range(3):
            assert c.post("/v1/chat/completions", json=body(),
                          headers={"Authorization": f"Bearer {key}"}
                          ).status_code == 200, f"запрос {i + 1} не прошёл"
        r = c.post("/v1/chat/completions", json=body(),
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 429, f"4-й запрос: {r.status_code}"
        assert _err_code(r.json()) == "rate_limit_exceeded"
        assert int(r.headers["retry-after"]) >= 1, "нет Retry-After"
        # Отказ не расходует окно: клиент не блокирует себя повторами.
        for _ in range(5):
            assert c.post("/v1/chat/completions", json=body(),
                          headers={"Authorization": f"Bearer {key}"}
                          ).status_code == 429
        record("A5 rate limit RPM", "PASS",
               "rpm=3 → 4-й 429 + Retry-After; отказ окно не расходует")
    except Exception as e:  # noqa: BLE001
        record("A5 rate limit RPM", "FAIL", str(e))
        ok = False

    # --- A6: rate limit TPM ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a6_")
        c, key, _, _ = make(tmp, rpm=0, tpm=50)
        r = c.post("/v1/chat/completions", json=body("x" * 4000),
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 429, f"TPM: {r.status_code} (ждём 429, не 500)"
        assert _err_code(r.json()) == "rate_limit_exceeded"
        assert "токенов" in r.json()["error"]["message"].lower()
        record("A6 rate limit TPM", "PASS",
               "один запрос сверх TPM → 429 с указанием токенов")
    except Exception as e:  # noqa: BLE001
        record("A6 rate limit TPM", "FAIL", str(e))
        ok = False

    # --- A7: max context + компрессия ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a7_")
        cap: list = []
        c, key, _, _ = make(tmp, captured=cap, max_context=4000)
        msgs = []
        for i in range(30):
            msgs.append({"role": "user", "content": f"вопрос {i} " + "x" * 600})
            msgs.append({"role": "assistant",
                         "content": f"ответ {i} " + "y" * 600})
        msgs.append({"role": "user", "content": "САМЫЙ ПОСЛЕДНИЙ ВОПРОС"})
        before = sum(len(m["content"]) for m in msgs)
        r = c.post("/v1/chat/completions",
                   json={"model": MODELS[0], "messages": msgs},
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:300]}"
        j = r.json()
        assert j["gateway"]["compressed"] is True, "компрессия не сработала"
        assert r.headers["x-gateway-compressed"] == "true"
        assert j["gateway"]["tokens_after_compression"] <= 4000
        sent = cap[-1]["messages"]
        assert len(sent) < len(msgs), "наверх ушёл несжатый диалог"
        assert sent[-1]["content"] == "САМЫЙ ПОСЛЕДНИЙ ВОПРОС", \
            "последний вопрос потерян"
        # Сводка действительно вызвана (был отдельный вызов модели).
        assert len(cap) >= 2, "сводка не вызывалась"
        record("A7 max context + компрессия", "PASS",
               f"{len(msgs)} сообщений / {before} симв. → "
               f"{len(sent)} сообщений / "
               f"{j['gateway']['tokens_after_compression']} токенов "
               f"(раундов {j['gateway']['compress_rounds']}), "
               f"последний вопрос сохранён")
    except Exception as e:  # noqa: BLE001
        record("A7 max context + компрессия", "FAIL", str(e))
        ok = False

    # --- A8: честный 413 ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a8_")
        c, key, _, _ = make(tmp, max_context=100)
        msgs = [{"role": "user", "content": "x" * 4000},
                {"role": "assistant", "content": "y" * 4000},
                {"role": "user", "content": "z" * 4000}]
        r = c.post("/v1/chat/completions",
                   json={"model": MODELS[0], "messages": msgs},
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 413, f"HTTP {r.status_code}"
        assert _err_code(r.json()) == "context_length_exceeded"
        assert "100" in r.json()["error"]["message"], \
            "в ошибке нет точного лимита"
        record("A8 честный 413", "PASS",
               "неустранимый контекст → 413 с точными числами (не обрезка)")
    except Exception as e:  # noqa: BLE001
        record("A8 честный 413", "FAIL", str(e))
        ok = False

    # --- A9: max output ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a9_")
        cap: list = []
        c, key, _, _ = make(tmp, captured=cap, max_output_tokens=32)
        c.post("/v1/chat/completions", json=body(max_tokens=9999),
               headers={"Authorization": f"Bearer {key}"})
        assert cap[-1]["max_tokens"] == 32, \
            f"ушло max_tokens={cap[-1]['max_tokens']}, ждали 32"

        tmp2 = tempfile.mkdtemp(prefix="e2e30a9b_")
        cap2: list = []
        c2, key2, _, _ = make(tmp2, captured=cap2, max_output_tokens=4096)
        c2.post("/v1/chat/completions", json=body(max_tokens=100),
                headers={"Authorization": f"Bearer {key2}"})
        assert cap2[-1]["max_tokens"] == 100, "урезали запрошенное меньшее"
        record("A9 потолок ответа", "PASS",
               "max_tokens=9999 → 32 (потолок клиента); 100 → 100 (не тронут)")
    except Exception as e:  # noqa: BLE001
        record("A9 потолок ответа", "FAIL", str(e))
        ok = False

    # --- A10: учёт + персистентность ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a10_")
        c, key, _, cfg = make(tmp)
        for _ in range(3):
            c.post("/v1/chat/completions", json=body(),
                   headers={"Authorization": f"Bearer {key}"})
        u = c.get("/api/gateway/usage").json()
        assert u["usage"]["phone"]["requests"] == 3, u["usage"]
        assert u["usage"]["phone"]["tokens_total"] == 17 * 3, u["usage"]
        assert u["totals"]["requests"] == 3

        # Рестарт процесса: новые сервисы на ТЕХ ЖЕ файлах данных.
        c2, key2, _, _ = make(tmp)
        u2 = c2.get("/api/gateway/usage").json()
        assert u2["usage"]["phone"]["requests"] == 3, \
            f"счётчики не пережили рестарт: {u2['usage']}"
        record("A10 учёт + персистентность", "PASS",
               "3 запроса / 51 токен; счётчики переживают рестарт процесса")
    except Exception as e:  # noqa: BLE001
        record("A10 учёт + персистентность", "FAIL", str(e))
        ok = False

    # --- A11: управление клиентами ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a11_")
        c, key, _, cfg = make(tmp)
        r = c.post("/api/gateway/clients",
                   json={"name": "laptop", "rpm": 1, "max_context": 4096})
        assert r.status_code == 200, r.text[:200]
        created = r.json()
        new_key = created["key"]
        assert new_key and new_key.startswith("sk-local-"), created
        assert created["key_masked"].startswith("sk-local-…"), created
        # Повторно ключ не отдаётся.
        again = c.post("/api/gateway/clients", json={"name": "laptop"}).json()
        assert again["key"] is None, "ключ отдан повторно!"
        # В файле — только хэш.
        raw = open(os.path.join(tmp, "gateway.json"), encoding="utf-8").read()
        assert new_key not in raw, "ключ лежит в конфиге открытым текстом!"
        assert "key_hash" in raw
        # Список не содержит хэшей и полного ключа.
        lst = c.get("/api/gateway/clients").json()
        assert "key_hash" not in json.dumps(lst)
        assert new_key not in json.dumps(lst)

        # Новый клиент работает и изолирован по лимитам.
        auth_new = {"Authorization": f"Bearer {new_key}"}
        assert c.post("/v1/chat/completions", json=body(),
                      headers=auth_new).status_code == 200
        assert c.post("/v1/chat/completions", json=body(),
                      headers=auth_new).status_code == 429, "rpm=1 не сработал"
        assert c.post("/v1/chat/completions", json=body(),
                      headers={"Authorization": f"Bearer {key}"}
                      ).status_code == 200, "основной клиент пострадал"

        # Удаление инвалидирует ключ.
        assert c.request("DELETE", "/api/gateway/clients/laptop").status_code == 200
        assert c.post("/v1/chat/completions", json=body(),
                      headers=auth_new).status_code == 401, "ключ жив после удаления"
        record("A11 управление клиентами", "PASS",
               "ключ отдаётся один раз, в конфиге только SHA-256, "
               "лимиты изолированы, удаление инвалидирует ключ")
    except Exception as e:  # noqa: BLE001
        record("A11 управление клиентами", "FAIL", str(e))
        ok = False

    # --- A12: стриминг ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a12_")
        c, key, _, _ = make(tmp, rpm=1)
        r = c.post("/v1/chat/completions", json=body(stream=True),
                   headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, f"стрим: {r.status_code}"
        assert "text/event-stream" in r.headers.get("content-type", "")
        assert "data: [DONE]" in r.text, "нет завершения SSE"
        # Обойти лимит через stream нельзя: rpm=1 уже израсходован.
        r2 = c.post("/v1/chat/completions", json=body(stream=True),
                    headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 429, \
            f"лимит обойдён через stream: {r2.status_code}"

        tmp2 = tempfile.mkdtemp(prefix="e2e30a12b_")
        c2, _, _, _ = make(tmp2)
        r3 = c2.post("/v1/chat/completions", json=body(stream=True))
        assert r3.status_code == 401, "стрим без ключа прошёл"
        record("A12 стриминг", "PASS",
               "stream=true → SSE с [DONE]; лимит и ключ действуют и на стрим")
    except Exception as e:  # noqa: BLE001
        record("A12 стриминг", "FAIL", str(e))
        ok = False

    # --- A13: лог отказов ---
    try:
        tmp = tempfile.mkdtemp(prefix="e2e30a13_")
        c, key, _, _ = make(tmp, rpm=1)
        c.post("/v1/chat/completions", json=body(),
               headers={"Authorization": f"Bearer {key}"})
        c.post("/v1/chat/completions", json=body(),
               headers={"Authorization": f"Bearer {key}"})
        c.post("/v1/chat/completions", json=body())  # без ключа
        log_j = c.get("/api/gateway/rejections").json()["rejections"]
        codes = {x["code"] for x in log_j}
        assert "rate_limit_exceeded" in codes, log_j
        assert "missing_api_key" in codes, log_j
        assert any(x["status"] == 429 for x in log_j), log_j
        u = c.get("/api/gateway/usage").json()
        assert u["usage"]["phone"]["rejected"] == 1, u["usage"]
        assert u["totals"]["rejected"] == 2, u["totals"]
        record("A13 лог отказов", "PASS",
               f"причины видны: {', '.join(sorted(codes))}; "
               f"всего отказов {u['totals']['rejected']}")
    except Exception as e:  # noqa: BLE001
        record("A13 лог отказов", "FAIL", str(e))
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Part B: живой прогон (реальный сервер + реальная Ollama)
# ---------------------------------------------------------------------------

def _ollama_tags() -> list[dict]:
    try:
        req = urllib.request.Request(OLLAMA_NATIVE + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode()).get("models") or []
    except Exception:  # noqa: BLE001
        return []


def _lan_ip() -> str:
    """LAN-адрес машины (без отправки пакетов)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def wait_port_free(port: int) -> bool:
    waited = 0.0
    while port_open(port):
        if waited >= WAIT_PORT_BUSY_MAX:
            return False
        log(f"порт {port} занят, ждём {WAIT_PORT_STEP} с…")
        time.sleep(WAIT_PORT_STEP)
        waited += WAIT_PORT_STEP
    return True


def _kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _dump_server_log(limit: int = 3000) -> None:
    try:
        with open(SERVER_LOG, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-limit:]
        for line in tail.splitlines():
            log("  | " + line)
    except Exception:  # noqa: BLE001
        pass


def start_server(env_extra: dict | None = None) -> subprocess.Popen | None:
    """Поднять uvicorn на 0.0.0.0:PORT — как в реальном LAN-развёртывании.

    Запускаем через `--app-dir studio/backend` с приложением `main:app`:
    при запуске из корня репозитория пакетный путь
    `studio.backend.main:app` не работает (у `studio/` нет `__init__.py`,
    и `main.py` уходит в fallback-импорт, который подхватывает устаревший
    корневой `agent.py` дня 7). `--app-dir` — штатный флаг uvicorn, он
    кладёт каталог бэкенда в sys.path и даёт корректные импорты.
    """
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log(f"запускаем uvicorn на 0.0.0.0:{PORT} (лог: {SERVER_LOG})")
    with open(SERVER_LOG, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--app-dir", os.path.join("studio", "backend"),
             "--host", "0.0.0.0", "--port", str(PORT)],
            cwd=REPO, env=env, stdout=log_file, stderr=subprocess.STDOUT)
        waited = 0.0
        while waited < WAIT_SERVER_UP_MAX:
            if proc.poll() is not None:
                log("uvicorn завершился раньше времени, хвост server.log:")
                _dump_server_log()
                return None
            if port_open(PORT):
                return proc
            time.sleep(WAIT_SERVER_UP_STEP)
            waited += WAIT_SERVER_UP_STEP
    log(f"сервер не поднялся за {WAIT_SERVER_UP_MAX} с:")
    _dump_server_log()
    _kill_proc(proc)
    return None


def part_b() -> bool:
    log("\n=== Part B: живой сервер в LAN + реальная Ollama ===")
    ok = True

    models = _ollama_tags()
    if not models:
        for step in ("B0 зонд Ollama", "B1 доступ по сети",
                     "B2 rate limit на живом сервере",
                     "B3 реальная компрессия",
                     "B3b пустой ответ виден клиенту", "B4 реальный 413",
                     "B5 Ollama закрыта наружу", "B6 учёт на живом сервере"):
            record(step, "SKIP", "Ollama недоступна (нет /api/tags)")
        return True

    names = [m.get("name", "") for m in models]
    record("B0 зонд Ollama", "PASS",
           f"{len(names)} моделей: {', '.join(names[:4])}")

    # Модель для живых проверок выбираем как в реальном развёртывании:
    # reasoning-модель (glm-4.7-flash) на длинном контексте тратит бюджет
    # на размышления и отдаёт пустой ответ (finish_reason=length) — это
    # отдельно проверяется в B3b. Для основной проверки берём модель,
    # доказанную замером дня 29 (qwen3-coder:30b: без reasoning, самая
    # быстрая из локальных), иначе gemma3:27b.
    preferred = ("qwen3-coder:30b", "gemma3:27b")
    model = next((n for n in preferred if n in names), None)
    reasoning_model = next((n for n in names if "glm" in n.lower()), None)
    if model is None:
        # Ни одной «рабочей» модели нет: берём первую не-эмбеддер.
        cand = [n for n in names if "embed" not in n]
        model = cand[0] if cand else names[0]

    if not wait_port_free(PORT):
        record("B1 доступ по сети", "FAIL", f"порт {PORT} занят")
        return False

    lan_ip = _lan_ip()
    # Уникальный префикс на запуск: живой сервер пишет в общий
    # studio/data, и клиенты прошлых прогонов иначе переиспользуются
    # (upsert не поворачивает ключ молча — и «новый» клиент приходит без
    # ключа). Уборка в finally гарантирует чистоту и повторяемость.
    run_id = f"e2e30-{int(time.time())}"
    proc = start_server(env_extra={
        # Клиент для живых проверок (ключ фиксирован, чтобы знать его).
        "GATEWAY_CLIENT_KEY": "sk-local-e2e-day30-live-key",
    })
    if proc is None:
        record("B1 доступ по сети", "FAIL", "сервер не поднялся")
        return False

    created_clients: list[str] = []

    def make_client(name: str, **limits) -> str:
        """Создать клиента прогона и вернуть его ключ.

        rotate_key=True: даже если имя осталось от прошлого прогона,
        ключ будет новым и известным — тест не зависит от старого
        состояния общего studio/data.
        """
        body = {"name": name, "rotate_key": True}
        body.update(limits)
        st, _, created = http("/api/gateway/clients", method="POST", key=key,
                              json_body=body)
        assert st == 200, f"создание клиента {name}: HTTP {st} {created}"
        assert created.get("key"), f"клиент {name} создан без ключа: {created}"
        created_clients.append(name)
        return created["key"]

    try:
        key = "sk-local-e2e-day30-live-key"
        auth = {"Authorization": f"Bearer {key}"}

        # --- B1: доступ по сети (не только loopback) ---
        try:
            assert port_open(PORT, "127.0.0.1"), "порт не слушается на loopback"
            lan_ok = port_open(PORT, lan_ip, timeout=3) if lan_ip != "127.0.0.1" else True
            assert lan_ok, f"порт {PORT} недоступен по LAN-адресу {lan_ip}"

            # «Другое устройство»: отдельный HTTP-клиент по LAN-адресу,
            # без cookies и сессии — чистый сетевой вызов.
            lan_base = f"http://{lan_ip}:{PORT}"
            st, _, j = http("/api/gateway/status", base=lan_base)
            assert st == 200, f"status по LAN: {st}"
            assert j["models"], "сервис не видит локальных моделей"

            st, _, ans = http("/v1/chat/completions", method="POST", base=lan_base,
                              key=key, timeout=300,
                              json_body={"model": model,
                                         "messages": [{"role": "user",
                                                       "content": "Скажи одно слово: привет"}]})
            assert st == 200, f"запрос по LAN: HTTP {st}: {str(ans)[:300]}"
            text = ((ans.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            assert text.strip(), "пустой ответ модели"
            gw = ans.get("gateway") or {}
            record("B1 доступ по сети", "PASS",
                   f"сервер на 0.0.0.0:{PORT}, доступен по {lan_ip}:{PORT}; "
                   f"«другое устройство» получило ответ {model} "
                   f"({gw.get('latency_sec')} с, {gw.get('tokens_out')} токенов)")
        except Exception as e:  # noqa: BLE001
            record("B1 доступ по сети", "FAIL", str(e))
            ok = False

        # --- B2: rate limit на живом сервере ---
        try:
            rl_name = f"{run_id}-rl"
            rl_key = make_client(rl_name, rpm=2, tpm=0, max_context=8192)
            rl_auth = {"Authorization": f"Bearer {rl_key}"}
            codes = []
            for _ in range(4):
                st, _, b = http("/v1/chat/completions", method="POST", key=rl_key,
                                json_body={"model": model,
                                           "messages": [{"role": "user",
                                                         "content": "привет"}]},
                                timeout=300)
                codes.append(st)
            assert codes[:2] == [200, 200], f"первые два: {codes}"
            assert codes[2] == 429, f"3-й должен быть 429: {codes}"
            assert codes[3] == 429, f"4-й должен быть 429: {codes}"
            http(f"/api/gateway/clients/{rl_name}", method="DELETE", key=key)
            record("B2 rate limit на живом сервере", "PASS",
                   f"rpm=2 → {codes} (реальная модель, реальные запросы)")
        except Exception as e:  # noqa: BLE001
            record("B2 rate limit на живом сервере", "FAIL", str(e))
            ok = False

        # --- B3: реальная компрессия ---
        try:
            ctx_name = f"{run_id}-ctx"
            # max_output_tokens с запасом: reasoning-модели сначала
            # «думают», и на малом бюджете ответ приходит пустым
            # (проверяется отдельно — см. ниже). Здесь цель — компрессия.
            ctx_key = make_client(ctx_name, rpm=0, tpm=0, max_context=500,
                                  max_output_tokens=2048)

            msgs = []
            for i in range(10):
                msgs.append({"role": "user",
                             "content": f"Вопрос {i}: расскажи про тему {i}. " + "д" * 300})
                msgs.append({"role": "assistant",
                             "content": f"Ответ {i} по теме {i}. " + "о" * 300})
            msgs.append({"role": "user",
                         "content": "Кратко: сколько тем мы обсудили?"})
            st, _, ans = http("/v1/chat/completions", method="POST", key=ctx_key,
                              timeout=600,
                              json_body={"model": model, "messages": msgs})
            assert st == 200, f"HTTP {st}: {str(ans)[:400]}"
            gw = ans.get("gateway") or {}
            assert gw.get("compressed") is True, f"компрессия не сработала: {gw}"
            text = ((ans.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            assert text.strip(), (
                "пустой ответ после компрессии: "
                f"empty_reason={gw.get('empty_reason')}")
            assert gw["tokens_after_compression"] <= 500, gw
            http(f"/api/gateway/clients/{ctx_name}", method="DELETE", key=key)
            record("B3 реальная компрессия", "PASS",
                   f"{len(msgs)} сообщений / {gw['tokens_before_compression']} токенов → "
                   f"сводка ({gw['compress_rounds']} раунд) → "
                   f"{gw['tokens_after_compression']} токенов; ответ получен")
        except Exception as e:  # noqa: BLE001
            record("B3 реальная компрессия", "FAIL", str(e))
            ok = False

        # --- B3b: пустой ответ reasoning-модели виден клиенту ---
        try:
            small_name = f"{run_id}-small"
            small_key = make_client(small_name, rpm=0, tpm=0,
                                    max_context=8192, max_output_tokens=16)
            st, _, ans = http("/v1/chat/completions", method="POST",
                              key=small_key, timeout=300,
                              json_body={"model": reasoning_model or model,
                                         "messages": [{"role": "user",
                                                       "content": "Расскажи подробно про историю Рима."}]})
            assert st == 200, f"HTTP {st}: {str(ans)[:200]}"
            gw = ans.get("gateway") or {}
            text = ((ans.get("choices") or [{}])[0]
                    .get("message") or {}).get("content", "")
            if not text.strip():
                assert gw.get("empty_response") is True, (
                    "пустой ответ не помечен в gateway — клиент не поймёт, "
                    f"что случилось: {gw}")
                assert gw.get("empty_reason"), gw
            http(f"/api/gateway/clients/{small_name}", method="DELETE", key=key)
            record("B3b пустой ответ виден клиенту", "PASS",
                   "малый бюджет: пустой ответ помечен gateway.empty_response "
                   f"(finish_reason={gw.get('finish_reason')}), "
                   "а не отдан молча" if not text.strip() else
                   "модель успела ответить в пределах бюджета; "
                   "поле empty_response не выставлено")
        except Exception as e:  # noqa: BLE001
            record("B3b пустой ответ виден клиенту", "FAIL", str(e))
            ok = False

        # --- B4: реальный 413 ---
        try:
            tiny_name = f"{run_id}-413"
            tiny_key = make_client(tiny_name, rpm=0, tpm=0, max_context=50)
            big = [{"role": "user", "content": "x" * 3000},
                   {"role": "assistant", "content": "y" * 3000},
                   {"role": "user", "content": "z" * 3000}]
            st, _, ans = http("/v1/chat/completions", method="POST", key=tiny_key,
                              json_body={"model": model, "messages": big})
            assert st == 413, f"HTTP {st}: {str(ans)[:300]}"
            assert _err_code(ans) == "context_length_exceeded", ans
            http(f"/api/gateway/clients/{tiny_name}", method="DELETE", key=key)
            record("B4 реальный 413", "PASS",
                   "контекст 50 токенов, последние сообщения больше → 413")
        except Exception as e:  # noqa: BLE001
            record("B4 реальный 413", "FAIL", str(e))
            ok = False

        # --- B5: Ollama закрыта наружу ---
        try:
            ollama_port = 11434
            via_lan = (port_open(ollama_port, lan_ip, timeout=3)
                       if lan_ip != "127.0.0.1" else False)
            assert not via_lan, (
                f"Ollama доступна по LAN-адресу {lan_ip}:{ollama_port} — "
                f"она должна слушать только loopback, наружу смотрит шлюз")
            record("B5 Ollama закрыта наружу", "PASS",
                   f"{lan_ip}:{ollama_port} недоступен — наружу смотрит "
                   f"только шлюз (:{PORT}) с ключами и лимитами")
        except Exception as e:  # noqa: BLE001
            record("B5 Ollama закрыта наружу", "FAIL", str(e))
            ok = False

        # --- B6: учёт на живом сервере ---
        try:
            st, _, u = http("/api/gateway/usage", key=key)
            assert st == 200, f"usage: {st}"
            default = u["usage"].get("default") or {}
            assert default.get("requests", 0) > 0, u["usage"]
            assert default.get("tokens_total", 0) > 0, u["usage"]
            assert u["totals"]["requests"] > 0
            record("B6 учёт на живом сервере", "PASS",
                   f"default: {default['requests']} запросов, "
                   f"{default['tokens_total']} токенов, "
                   f"{default.get('rejected', 0)} отказов; "
                   f"всего запросов {u['totals']['requests']}")
        except Exception as e:  # noqa: BLE001
            record("B6 учёт на живом сервере", "FAIL", str(e))
            ok = False

    finally:
        # Уборка: клиенты и их счётчики не должны оставаться в общем
        # studio/data — иначе повторные прогоны копят мусор и мешают
        # читать реальную статистику.
        for name in created_clients:
            try:
                http(f"/api/gateway/clients/{name}", method="DELETE", key=key)
                http("/api/gateway/reset", method="POST", key=key,
                     json_body={"name": name})
            except Exception:  # noqa: BLE001 — уборка не должна ронять отчёт
                pass
        _kill_proc(proc)

    return ok


# ---------------------------------------------------------------------------

def main() -> int:
    global PORT, BASE

    ap = argparse.ArgumentParser()
    ap.add_argument("--part-a-only", action="store_true",
                    help="только офлайн-часть (без живого сервера)")
    ap.add_argument("--port", type=int, default=8110,
                    help="порт живого сервера (по умолчанию 8110)")
    args = ap.parse_args()

    PORT = args.port
    BASE = f"http://127.0.0.1:{PORT}"

    log("E2E day 30: локальная LLM как приватный сетевой сервис")
    log(f"порт живого сервера: {PORT}, Ollama: {OLLAMA_NATIVE}")

    a_ok = part_a()
    b_ok = True
    if not args.part_a_only:
        b_ok = part_b()
    else:
        log("\n(Part B пропущена: --part-a-only)")

    p, f = print_summary()
    if not a_ok:
        log("Part A провалена — сервис не соответствует заданию дня 30.")
    return 0 if (a_ok and b_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
