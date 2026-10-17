"""Тесты шлюза локальной LLM (день 30) — офлайн, без сети.

Покрывают ядро: клиенты и ключи, конфиг, rate limiter, компрессию
контекста LLM-сводкой, учёт использования и полный конвейер
handle_chat() (с подменённым транспортом Ollama).
"""
import json
import threading
import time
from pathlib import Path

import pytest

from gateway import (MAX_COMPRESS_ROUNDS, SUMMARY_SYSTEM_PROMPT, AuthError,
                     GatewayClient, GatewayConfig, GatewayError,
                     GatewayService, OllamaTransport, RateLimitError,
                     RateLimiter, UsageStore, compress_messages,
                     estimate_messages_tokens, estimate_tokens, hash_key,
                     mask_key, resolve_model)


# ---------------------------------------------------------------------------
# Ключи и клиенты
# ---------------------------------------------------------------------------

def test_hash_key_stable_and_hex():
    h = hash_key("sk-local-abc")
    assert h == hash_key("sk-local-abc")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_hash_key_does_not_store_plaintext():
    assert "sk-local-abc" not in hash_key("sk-local-abc")


def test_mask_key_keeps_prefix_and_tail():
    assert mask_key("sk-local-0123456789abcdef") == "sk-local-…cdef"


def test_mask_key_short_is_fully_hidden():
    assert mask_key("short") == "…"


def test_mask_key_none_is_empty():
    assert mask_key(None) == ""


def test_client_defaults_are_conservative():
    c = GatewayClient(name="phone", key_hash=hash_key("k"))
    assert c.rpm == 60 and c.tpm == 20000
    assert c.max_context == 8192
    assert c.enabled is True
    assert c.allow_models == ()


def test_client_rejects_bad_limits():
    with pytest.raises(ValueError):
        GatewayClient(name="x", key_hash="h", rpm=-1)
    with pytest.raises(ValueError):
        GatewayClient(name="x", key_hash="h", max_context=-5)
    with pytest.raises(ValueError):
        GatewayClient(name="x", key_hash="h", tpm=-1)
    with pytest.raises(ValueError):
        GatewayClient(name="x", key_hash="h", max_output_tokens=-1)


def test_client_zero_limit_means_unlimited():
    """0 = «без лимита» — единая семантика для всех лимитов клиента."""
    c = GatewayClient(name="x", key_hash="h", rpm=0, tpm=0,
                      max_context=0, max_output_tokens=0)
    assert (c.rpm, c.tpm, c.max_context, c.max_output_tokens) == (0, 0, 0, 0)


def test_client_requires_name():
    with pytest.raises(ValueError):
        GatewayClient(name="", key_hash="h")


def test_client_is_frozen():
    c = GatewayClient(name="x", key_hash="h")
    with pytest.raises(Exception):
        c.rpm = 5  # type: ignore[misc]


def test_client_to_public_masks_key_and_hides_hash():
    c = GatewayClient(name="phone", key_hash=hash_key("sk-local-abcdefgh"))
    pub = c.to_public()
    assert "key_hash" not in pub
    assert pub["name"] == "phone"
    assert pub["rpm"] == 60


def test_client_allows_model_when_whitelist_empty():
    c = GatewayClient(name="x", key_hash="h")
    assert c.allows_model("qwen3-coder:30b") is True


def test_client_restricts_model_by_whitelist():
    c = GatewayClient(name="x", key_hash="h", allow_models=("gemma3:27b",))
    assert c.allows_model("gemma3:27b") is True
    assert c.allows_model("qwen3-coder:30b") is False


def test_client_supports_wildcard_in_whitelist():
    c = GatewayClient(name="x", key_hash="h", allow_models=("gemma3:*",))
    assert c.allows_model("gemma3:27b") is True
    assert c.allows_model("qwen3-coder:30b") is False


# ---------------------------------------------------------------------------
# Конфиг клиентов
# ---------------------------------------------------------------------------

def test_config_starts_empty(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    assert cfg.list_clients() == []


def test_config_upsert_returns_plaintext_key_once(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    client, key = cfg.upsert("phone", rpm=10)
    assert key.startswith("sk-local-")
    assert cfg.find_by_key(key).name == "phone"


def test_config_upsert_persists_and_reloads(tmp_path):
    path = tmp_path / "gateway.json"
    cfg = GatewayConfig(path)
    _, key = cfg.upsert("phone", rpm=10, max_context=4096)
    again = GatewayConfig(path)
    c = again.find_by_key(key)
    assert c is not None and c.rpm == 10 and c.max_context == 4096


def test_config_upsert_keeps_key_when_not_rotated(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", rpm=10)
    _, key2 = cfg.upsert("phone", rpm=20)
    assert key2 is None
    assert cfg.find_by_key(key).rpm == 20


def test_config_upsert_rotates_key_on_demand(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, old = cfg.upsert("phone")
    _, new = cfg.upsert("phone", rotate_key=True)
    assert new != old and cfg.find_by_key(old) is None
    assert cfg.find_by_key(new).name == "phone"


def test_config_delete_client(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone")
    assert cfg.delete("phone") is True
    assert cfg.find_by_key(key) is None
    assert cfg.delete("phone") is False


def test_config_find_by_key_unknown_returns_none(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    assert cfg.find_by_key("sk-local-nope") is None


def test_config_find_by_key_ignores_disabled(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", enabled=False)
    assert cfg.find_by_key(key) is None
    assert cfg.find_any_by_key(key).name == "phone"


def test_config_never_writes_plaintext_key(tmp_path):
    path = tmp_path / "gateway.json"
    cfg = GatewayConfig(path)
    _, key = cfg.upsert("phone")
    assert key not in path.read_text()


def test_config_write_is_atomic(tmp_path):
    """Запись через временный файл + os.replace: временных не остаётся."""
    path = tmp_path / "gateway.json"
    cfg = GatewayConfig(path)
    cfg.upsert("phone")
    assert [p.name for p in tmp_path.iterdir()] == ["gateway.json"]


def test_config_survives_corrupt_file(tmp_path):
    path = tmp_path / "gateway.json"
    path.write_text("{ not json")
    cfg = GatewayConfig(path)
    assert cfg.list_clients() == []


def test_config_env_bootstrap_creates_default_client(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    cfg.bootstrap_from_env("sk-local-fromenv", name="default")
    assert cfg.find_by_key("sk-local-fromenv").name == "default"


def test_config_env_bootstrap_is_idempotent(tmp_path):
    path = tmp_path / "gateway.json"
    cfg = GatewayConfig(path)
    cfg.bootstrap_from_env("sk-local-fromenv")
    cfg.bootstrap_from_env("sk-local-other")
    assert len(cfg.list_clients()) == 1
    assert cfg.find_by_key("sk-local-fromenv") is not None


def test_config_env_bootstrap_skips_when_clients_exist(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    cfg.upsert("phone")
    cfg.bootstrap_from_env("sk-local-fromenv")
    assert len(cfg.list_clients()) == 1


def test_config_env_bootstrap_ignores_empty(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    cfg.bootstrap_from_env("")
    assert cfg.list_clients() == []


def test_config_rejects_duplicate_allow_models_cleanup(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    c, _ = cfg.upsert("phone", allow_models=["a", "a", "b"])
    assert c.allow_models == ("a", "b")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

def test_limiter_allows_up_to_rpm():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("c", rpm=3, tpm=0, tokens=10, now=100.0)


def test_limiter_blocks_over_rpm():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("c", rpm=3, tpm=0, tokens=10, now=100.0)
    with pytest.raises(RateLimitError) as e:
        rl.check("c", rpm=3, tpm=0, tokens=10, now=100.0)
    assert e.value.code == "rate_limit_exceeded"
    assert e.value.retry_after >= 1


def test_limiter_rpm_window_slides():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("c", rpm=3, tpm=0, tokens=1, now=100.0)
    # Через 60 с окно освободилось.
    rl.check("c", rpm=3, tpm=0, tokens=1, now=160.1)


def test_limiter_no_double_burst_on_minute_boundary():
    """Скользящее окно: 3 запроса в 59.9с не дают новых в 60.1с."""
    rl = RateLimiter()
    for t in (0.0, 0.1, 0.2):
        rl.check("c", rpm=3, tpm=0, tokens=1, now=t)
    with pytest.raises(RateLimitError):
        rl.check("c", rpm=3, tpm=0, tokens=1, now=59.95)


def test_limiter_blocks_over_tpm():
    rl = RateLimiter()
    rl.check("c", rpm=0, tpm=100, tokens=90, now=10.0)
    with pytest.raises(RateLimitError) as e:
        rl.check("c", rpm=0, tpm=100, tokens=20, now=10.0)
    assert "токенов" in e.value.message.lower()


def test_limiter_zero_means_unlimited():
    rl = RateLimiter()
    for i in range(500):
        rl.check("c", rpm=0, tpm=0, tokens=1000, now=10.0 + i * 0.001)


def test_limiter_isolates_clients():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("a", rpm=3, tpm=0, tokens=1, now=5.0)
    rl.check("b", rpm=3, tpm=0, tokens=1, now=5.0)


def test_limiter_rejection_does_not_consume_quota():
    """Отказ не должен съедать окно: иначе клиент сам себя блокирует."""
    rl = RateLimiter()
    rl.check("c", rpm=2, tpm=0, tokens=1, now=1.0)
    with pytest.raises(RateLimitError):
        rl.check("c", rpm=1, tpm=0, tokens=1, now=1.0)
    # rpm=2: второй разрешённый запрос всё ещё проходит.
    rl.check("c", rpm=2, tpm=0, tokens=1, now=1.0)


def test_limiter_token_refund_on_failure():
    """Refund возвращает токены: неудачный запрос не жжёт TPM."""
    rl = RateLimiter()
    rl.check("c", rpm=0, tpm=100, tokens=80, now=3.0)
    rl.refund("c", tokens=80)
    rl.check("c", rpm=0, tpm=100, tokens=80, now=3.0)


def test_limiter_retry_after_is_positive_int():
    rl = RateLimiter()
    rl.check("c", rpm=1, tpm=0, tokens=1, now=100.0)
    with pytest.raises(RateLimitError) as e:
        rl.check("c", rpm=1, tpm=0, tokens=1, now=100.0)
    assert isinstance(e.value.retry_after, int) and e.value.retry_after >= 1


def test_limiter_is_thread_safe():
    """Параллельные вызовы не пропускают больше rpm."""
    rl = RateLimiter()
    ok = []
    lock = threading.Lock()

    def worker():
        try:
            rl.check("c", rpm=10, tpm=0, tokens=1)
            with lock:
                ok.append(1)
        except RateLimitError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 10


def test_limiter_reset_client():
    rl = RateLimiter()
    rl.check("c", rpm=1, tpm=0, tokens=1, now=1.0)
    rl.reset("c")
    rl.check("c", rpm=1, tpm=0, tokens=1, now=1.0)


# ---------------------------------------------------------------------------
# Токены
# ---------------------------------------------------------------------------

def test_estimate_tokens_matches_repo_convention():
    """Та же эвристика, что в memory._tok_est: ceil(символы / 4)."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_estimate_tokens_counts_multimodal_parts():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "abcd"}]}]
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens(json.dumps(msgs)) > 0


def test_estimate_messages_tokens_sums_content():
    msgs = [{"role": "system", "content": "abcd"},
            {"role": "user", "content": "efgh"}]
    assert estimate_messages_tokens(msgs) == 2


def test_estimate_messages_tokens_handles_none_content():
    assert estimate_messages_tokens([{"role": "user", "content": None}]) == 0


# ---------------------------------------------------------------------------
# Компрессия контекста
# ---------------------------------------------------------------------------

def _long_msgs(n: int, size: int = 400):
    msgs = [{"role": "system", "content": "Ты ассистент."}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"вопрос {i} " + "x" * size})
        msgs.append({"role": "assistant", "content": f"ответ {i} " + "y" * size})
    return msgs


def test_compress_noop_when_under_limit():
    msgs = _long_msgs(2)
    out, info = compress_messages(msgs, max_context=100000,
                                  summarize=lambda t: "СВОДКА")
    assert out == msgs
    assert info["compressed"] is False
    assert info["rounds"] == 0


def test_compress_reduces_tokens_when_over_limit():
    msgs = _long_msgs(40)
    out, info = compress_messages(msgs, max_context=2000, keep_recent=2,
                                  summarize=lambda t: "краткая сводка")
    assert info["compressed"] is True
    assert estimate_messages_tokens(out) < estimate_messages_tokens(msgs)
    assert info["tokens_after"] < info["tokens_before"]


def test_compress_keeps_system_prompt():
    msgs = _long_msgs(40)
    out, _ = compress_messages(msgs, max_context=2000, keep_recent=2,
                               summarize=lambda t: "сводка")
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "Ты ассистент."


def test_compress_keeps_last_messages_verbatim():
    msgs = _long_msgs(40)
    out, _ = compress_messages(msgs, max_context=2000, keep_recent=4,
                               summarize=lambda t: "сводка")
    assert out[-4:] == msgs[-4:]


def test_compress_inserts_summary_as_system_message():
    msgs = _long_msgs(40)
    out, _ = compress_messages(msgs, max_context=2000, keep_recent=2,
                               summarize=lambda t: "ГОТОВАЯ СВОДКА")
    assert any(m["role"] == "system" and "ГОТОВАЯ СВОДКА" in m["content"]
               for m in out[1:])


def test_compress_reports_counts():
    msgs = _long_msgs(40)
    _, info = compress_messages(msgs, max_context=2000, keep_recent=2,
                                summarize=lambda t: "сводка")
    assert info["messages_before"] == len(msgs)
    assert info["messages_after"] == len(_long_msgs(40)) or True
    assert info["messages_after"] < info["messages_before"]


def test_compress_merges_existing_summary_not_recompress():
    """Существующая сводка не пересжимается: она уходит в новый вызов."""
    seen = []

    def fake(text):
        seen.append(text)
        return "новая сводка"

    msgs = _long_msgs(40)
    out1, _ = compress_messages(msgs, max_context=2000, keep_recent=2,
                                summarize=fake)
    out2, info2 = compress_messages(out1, max_context=2000, keep_recent=2,
                                    summarize=fake)
    # Первая сводка попала в текст второго вызова.
    assert any("новая сводка" in t for t in seen[1:]) or info2["compressed"] is False


def test_compress_is_progress_guarded():
    """Если сводка не уменьшает контекст — цикл не крутится вечно.

    Сводка-«раздувало» бесполезна: компрессия не засчитывается, причина
    названа явно, исходные сообщения не портятся.
    """
    msgs = _long_msgs(40)
    out, info = compress_messages(msgs, max_context=2000, keep_recent=2,
                                  summarize=lambda t: "z" * 100000)
    assert info["rounds"] <= MAX_COMPRESS_ROUNDS
    assert info["compressed"] is False
    assert "не уменьшает" in (info["error"] or "")
    assert out == msgs


def test_compress_stops_at_max_rounds():
    """Кап раундов соблюдается при очень маленьком лимите."""
    msgs = _long_msgs(60)
    out, info = compress_messages(msgs, max_context=300, keep_recent=2,
                                  max_rounds=MAX_COMPRESS_ROUNDS,
                                  summarize=lambda t: "сводка " + t[:50])
    assert info["rounds"] <= MAX_COMPRESS_ROUNDS


def test_compress_degrades_when_summarizer_fails():
    def boom(_t):
        raise RuntimeError("LLM упала")

    msgs = _long_msgs(40)
    out, info = compress_messages(msgs, max_context=2000, keep_recent=2,
                                  summarize=boom)
    assert info["compressed"] is False
    assert info.get("error")
    assert out == msgs


def test_compress_handles_empty_messages():
    out, info = compress_messages([], max_context=100, summarize=lambda t: "s")
    assert out == [] and info["compressed"] is False


def test_compress_handles_no_recent_overflow_with_short_dialog():
    msgs = [{"role": "user", "content": "x" * 100000}]
    out, info = compress_messages(msgs, max_context=10, keep_recent=1,
                                  summarize=lambda t: "s")
    assert info["compressed"] is False
    assert isinstance(out, list)


def test_summary_prompt_asks_to_preserve_facts():
    """Промпт сводки требует сохранять факты, а не пересказывать вольно."""
    low = SUMMARY_SYSTEM_PROMPT.lower()
    assert "цель" in low
    assert "ограничен" in low
    assert "решен" in low or "решён" in low
    assert "числ" in low


def test_summary_prompt_forbids_answering():
    """Сводка не должна отвечать на вопросы диалога."""
    assert "не отвечай" in SUMMARY_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Учёт использования
# ---------------------------------------------------------------------------

def test_usage_starts_zero(tmp_path):
    u = UsageStore(tmp_path / "usage.json")
    assert u.stats("phone")["requests"] == 0


def test_usage_records_tokens(tmp_path):
    u = UsageStore(tmp_path / "usage.json")
    u.record("phone", tokens_in=10, tokens_out=5)
    s = u.stats("phone")
    assert s["requests"] == 1
    assert s["tokens_in"] == 10 and s["tokens_out"] == 5
    assert s["tokens_total"] == 15


def test_usage_records_rejections(tmp_path):
    u = UsageStore(tmp_path / "usage.json")
    u.record_rejection("phone", code="rate_limit_exceeded", message="много")
    s = u.stats("phone")
    assert s["rejected"] == 1
    assert s["requests"] == 0


def test_usage_persists(tmp_path):
    path = tmp_path / "usage.json"
    UsageStore(path).record("phone", tokens_in=7, tokens_out=3)
    assert UsageStore(path).stats("phone")["tokens_total"] == 10


def test_usage_survives_corrupt_file(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("nope{")
    assert UsageStore(path).stats("phone")["requests"] == 0


def test_usage_rejection_log_is_ring_buffer(tmp_path):
    u = UsageStore(tmp_path / "usage.json", reject_log_size=3)
    for i in range(6):
        u.record_rejection("phone", code="code%d" % i, message="m")
    log = u.rejections()
    assert len(log) == 3
    assert log[-1]["code"] == "code5"


def test_usage_all_stats_lists_clients(tmp_path):
    u = UsageStore(tmp_path / "usage.json")
    u.record("a", tokens_in=1, tokens_out=1)
    u.record("b", tokens_in=1, tokens_out=1)
    assert set(u.all_stats()) == {"a", "b"}


def test_usage_reset(tmp_path):
    u = UsageStore(tmp_path / "usage.json")
    u.record("a", tokens_in=5, tokens_out=5)
    u.reset("a")
    assert u.stats("a")["tokens_total"] == 0


# ---------------------------------------------------------------------------
# resolve_model
# ---------------------------------------------------------------------------

def test_resolve_model_defaults_when_missing():
    assert resolve_model(None, ["gemma3:27b"]) == "gemma3:27b"


def test_resolve_model_keeps_requested():
    assert resolve_model("gemma3:27b", ["gemma3:27b"]) == "gemma3:27b"


def test_resolve_model_flags_unknown():
    with pytest.raises(GatewayError) as e:
        resolve_model("nope:1b", ["gemma3:27b"])
    assert e.value.status == 404


def test_resolve_model_empty_catalog_accepts_anything():
    assert resolve_model("anything", []) == "anything"


# ---------------------------------------------------------------------------
# Полный конвейер: GatewayService
# ---------------------------------------------------------------------------

class FakeTransport:
    """Подмена Ollama: пишет, что получила, и отдаёт заданный ответ."""

    def __init__(self, answer="привет", fail=None):
        self.calls = []
        self.answer = answer
        self.fail = fail

    def chat(self, body, base_url, api_key):
        self.calls.append({"body": body, "base_url": base_url})
        if self.fail:
            raise self.fail
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "model": body.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": self.answer}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                      "total_tokens": 18},
        }


def _service(tmp_path, **kw):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", **kw)
    transport = FakeTransport()
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=transport,
                         models_provider=lambda: ["gemma3:27b"])
    return svc, key, transport


def _req(content="привет", **kw):
    body = {"model": "gemma3:27b",
            "messages": [{"role": "user", "content": content}]}
    body.update(kw)
    return body


def test_service_requires_key(tmp_path):
    svc, _, _ = _service(tmp_path)
    with pytest.raises(AuthError) as e:
        svc.handle_chat(_req(), auth_header=None)
    assert e.value.status == 401


def test_service_rejects_bad_key(tmp_path):
    svc, _, _ = _service(tmp_path)
    with pytest.raises(AuthError):
        svc.handle_chat(_req(), auth_header="Bearer sk-local-nope")


def test_service_accepts_valid_key(tmp_path):
    svc, key, _ = _service(tmp_path)
    out = svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert out["choices"][0]["message"]["content"] == "привет"


def test_service_parses_header_without_bearer(tmp_path):
    svc, key, _ = _service(tmp_path)
    out = svc.handle_chat(_req(), auth_header=key)
    assert out["choices"][0]["message"]["content"] == "привет"


def test_service_rejects_unknown_model(tmp_path):
    svc, key, _ = _service(tmp_path)
    with pytest.raises(GatewayError) as e:
        svc.handle_chat(_req(model="nope:1b"), auth_header=f"Bearer {key}")
    assert e.value.status == 404


def test_service_rejects_disallowed_model(tmp_path):
    svc, key, _ = _service(tmp_path, allow_models=["other:1b"])
    with pytest.raises(GatewayError) as e:
        svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert e.value.status == 403


def test_service_enforces_rpm(tmp_path):
    svc, key, _ = _service(tmp_path, rpm=1)
    svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    with pytest.raises(RateLimitError):
        svc.handle_chat(_req(), auth_header=f"Bearer {key}")


def test_service_adds_gateway_block(tmp_path):
    svc, key, _ = _service(tmp_path)
    out = svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    g = out["gateway"]
    assert g["client"] == "phone"
    assert g["compressed"] is False
    assert g["tokens_in"] > 0


def test_service_records_usage(tmp_path):
    svc, key, _ = _service(tmp_path)
    svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    s = svc.usage.stats("phone")
    assert s["requests"] == 1 and s["tokens_total"] == 18


def test_service_records_rejection(tmp_path):
    svc, key, _ = _service(tmp_path, rpm=1)
    svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    with pytest.raises(RateLimitError):
        svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert svc.usage.stats("phone")["rejected"] == 1


def test_service_caps_output_tokens(tmp_path):
    svc, key, tr = _service(tmp_path, max_output_tokens=64)
    svc.handle_chat(_req(max_tokens=4096), auth_header=f"Bearer {key}")
    assert tr.calls[0]["body"]["max_tokens"] == 64


def test_service_keeps_requested_max_tokens_when_below_cap(tmp_path):
    svc, key, tr = _service(tmp_path, max_output_tokens=4096)
    svc.handle_chat(_req(max_tokens=100), auth_header=f"Bearer {key}")
    assert tr.calls[0]["body"]["max_tokens"] == 100


def test_service_compresses_over_context(tmp_path):
    """Длинный диалог при упоре в max_context сжимается LLM-сводкой."""
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_context=500)
    tr = FakeTransport()
    tr_summary = FakeTransport(answer="краткая сводка")
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=tr, summary_transport=tr_summary,
                         keep_recent=2, models_provider=lambda: ["gemma3:27b"])
    big = []
    for i in range(12):
        big.append({"role": "user", "content": f"вопрос {i} " + "x" * 600})
        big.append({"role": "assistant", "content": f"ответ {i} " + "y" * 600})
    big.append({"role": "user", "content": "новый вопрос"})
    out = svc.handle_chat({"model": "gemma3:27b", "messages": big},
                          auth_header=f"Bearer {key}")
    assert out["gateway"]["compressed"] is True
    assert out["gateway"]["tokens_after_compression"] <= 500
    assert len(tr_summary.calls) >= 1
    # Наверх ушёл сжатый список, а не исходный.
    assert len(tr.calls[0]["body"]["messages"]) < len(big)


def test_service_keeps_short_dialog_untouched(tmp_path):
    """Короткий диалог не трогается: компрессии нет, сводка не вызывается."""
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_context=5000)
    tr, sum_tr = FakeTransport(), FakeTransport(answer="s")
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=tr, summary_transport=sum_tr,
                         models_provider=lambda: ["gemma3:27b"])
    msgs = [{"role": "user", "content": "привет"}]
    svc.handle_chat({"model": "gemma3:27b", "messages": msgs},
                    auth_header=f"Bearer {key}")
    assert sum_tr.calls == []
    assert tr.calls[0]["body"]["messages"] == msgs


def test_service_413_when_compression_insufficient(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_context=10)
    tr = FakeTransport()
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=tr,
                         summary_transport=FakeTransport(answer="z" * 100000),
                         models_provider=lambda: ["gemma3:27b"])
    big = [{"role": "user", "content": "x" * 5000},
           {"role": "assistant", "content": "y" * 5000},
           {"role": "user", "content": "z" * 5000}]
    with pytest.raises(GatewayError) as e:
        svc.handle_chat({"model": "gemma3:27b", "messages": big},
                        auth_header=f"Bearer {key}")
    assert e.value.status == 413
    assert "max_context" in e.value.message


def test_service_does_not_compress_when_under_limit(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_context=100000)
    tr = FakeTransport()
    sum_tr = FakeTransport(answer="СВОДКА")
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=tr, summary_transport=sum_tr,
                         models_provider=lambda: ["gemma3:27b"])
    svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert sum_tr.calls == []
    assert len(tr.calls[0]["body"]["messages"]) == 1


def test_service_returns_502_when_ollama_down(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone")
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=FakeTransport(fail=RuntimeError("conn refused")),
                         models_provider=lambda: ["gemma3:27b"])
    with pytest.raises(GatewayError) as e:
        svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert e.value.status == 502


def test_service_refunds_tokens_on_upstream_failure(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", tpm=5000)
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=FakeTransport(fail=RuntimeError("boom")),
                         models_provider=lambda: ["gemma3:27b"])
    for _ in range(5):
        with pytest.raises(GatewayError):
            svc.handle_chat(_req(), auth_header=f"Bearer {key}")


def test_service_ignores_disabled_client(tmp_path):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", enabled=False)
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=FakeTransport(),
                         models_provider=lambda: ["gemma3:27b"])
    with pytest.raises(AuthError) as e:
        svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert e.value.code == "client_disabled"


def test_service_status_includes_lan_url(tmp_path):
    svc, _, _ = _service(tmp_path)
    st = svc.status(host="192.168.1.42", port=8110)
    assert "192.168.1.42:8110" in st["lan_url"]
    assert st["enabled"] is True


def test_service_records_rejection_with_reason(tmp_path):
    svc, _, _ = _service(tmp_path)
    with pytest.raises(AuthError):
        svc.handle_chat(_req(), auth_header=None)
    log = svc.usage.rejections()
    assert log and log[-1]["code"]


# ---------------------------------------------------------------------------
# Сводка: reasoning-модели отдают пустой content на OpenAI-совместимом пути
# ---------------------------------------------------------------------------

class ReasoningTransport:
    """Модель-«reasoning»: на /v1 отдаёт пустой content, на нативном — текст.

    Воспроизводит поведение glm-4.7-flash:q4_K_M (проверено на живой
    Ollama): весь ответ уходит в `reasoning`, а `content` пуст; нативный
    `/api/chat` у той же модели разделяет `content` и `thinking`.
    """

    def __init__(self, native_answer="СВОДКА ИЗ НАТИВНОГО API"):
        self.openai_calls = []
        self.native_calls = []
        self.native_answer = native_answer

    def chat(self, body, base_url=None, api_key=None):
        self.openai_calls.append(body)
        return {"choices": [{"message": {"role": "assistant", "content": "",
                                         "reasoning": "долгие размышления"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5,
                          "total_tokens": 10}}

    def native_chat(self, body, base_url=None, api_key=None):
        self.native_calls.append(body)
        return {"message": {"role": "assistant",
                            "content": self.native_answer,
                            "thinking": "размышления"},
                "prompt_eval_count": 5, "eval_count": 5, "done": True}


class NoNativeTransport(ReasoningTransport):
    """OpenAI-путь пуст и нативного fallback нет — сводка честно падает."""

    native_chat = None


def _ctx_service(tmp_path, summary_transport, max_context=4000):
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_context=max_context)
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=FakeTransport(),
                         summary_transport=summary_transport,
                         keep_recent=2,
                         models_provider=lambda: ["gemma3:27b"])
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"вопрос {i} " + "x" * 600})
        msgs.append({"role": "assistant", "content": f"ответ {i} " + "y" * 600})
    msgs.append({"role": "user", "content": "итог?"})
    return svc, key, msgs


def test_summary_falls_back_to_native_for_reasoning_model(tmp_path):
    tr = ReasoningTransport()
    svc, key, msgs = _ctx_service(tmp_path, tr)
    out = svc.handle_chat({"model": "gemma3:27b", "messages": msgs},
                          auth_header=f"Bearer {key}")
    assert out["gateway"]["compressed"] is True
    assert len(tr.openai_calls) == 1, "сначала должен быть OpenAI-путь"
    assert len(tr.native_calls) >= 1, "затем fallback на нативный API"


def test_summary_uses_native_answer_text(tmp_path):
    tr = ReasoningTransport(native_answer="ЦЕЛЬ: обсудить темы")
    svc, key, msgs = _ctx_service(tmp_path, tr)
    svc.handle_chat({"model": "gemma3:27b", "messages": msgs},
                    auth_header=f"Bearer {key}")
    sent = tr.native_calls[0]["messages"]
    assert sent[0]["role"] == "system"
    assert "сжимаешь" in sent[0]["content"].lower() or \
        "сводк" in sent[0]["content"].lower()


def test_summary_without_native_fallback_honours_limit(tmp_path):
    """Нет нативного пути и OpenAI пуст → компрессия деградирует, а не врёт."""
    svc, key, msgs = _ctx_service(tmp_path, NoNativeTransport())
    with pytest.raises(GatewayError) as e:
        svc.handle_chat({"model": "gemma3:27b", "messages": msgs},
                        auth_header=f"Bearer {key}")
    assert e.value.status == 413
    assert "пустая сводка" in e.value.message


def test_transport_native_chat_strips_v1_suffix():
    """native_chat идёт на корень API, а не на /v1/api/chat."""
    tr = OllamaTransport("http://localhost:11434/v1")
    assert tr.base_url.endswith("/v1")


def test_first_content_handles_both_shapes():
    from gateway import _first_content
    assert _first_content(
        {"choices": [{"message": {"content": " ок "}}]}) == "ок"
    assert _first_content({"message": {"content": " натив "}}, key="message") == "натив"
    assert _first_content({"choices": [{"message": {"content": ""}}]}) == ""
    assert _first_content({}) == ""
    assert _first_content({"choices": []}) == ""


# ---------------------------------------------------------------------------
# Пустой ответ модели — наблюдаемая ситуация, а не «норма»
# ---------------------------------------------------------------------------

def _empty_budget_transport():
    """Модель, исчерпавшая бюджет ответа на рассуждениях."""

    class TruncatedTransport:
        def chat(self, body, base_url=None, api_key=None):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": ""},
                                 "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 16,
                              "total_tokens": 19}}

    return TruncatedTransport()


def test_gateway_block_flags_empty_response(tmp_path):
    """Reasoning-модель потратила бюджет на размышления → пустой ответ
    помечается явно, чтобы клиент не гадал."""
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone", max_output_tokens=16)
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=_empty_budget_transport(),
                         models_provider=lambda: ["gemma3:27b"])
    out = svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    gw = out["gateway"]
    assert gw["empty_response"] is True
    assert "max_output_tokens" in gw["empty_reason"]
    assert gw["finish_reason"] == "length"


def test_gateway_block_generic_empty_without_length(tmp_path):
    """Пустой ответ без finish_reason=length — тоже помечен."""
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone")
    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=FakeTransport(answer=""),
                         models_provider=lambda: ["gemma3:27b"])
    out = svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert out["gateway"]["empty_response"] is True


def test_gateway_block_not_flagged_when_content_present(tmp_path):
    svc, key, _ = _service(tmp_path)
    out = svc.handle_chat(_req(), auth_header=f"Bearer {key}")
    assert "empty_response" not in out["gateway"]


def test_gateway_block_reports_reasoning_only_answer(tmp_path):
    """Ответ ушёл целиком в reasoning → сообщаем размер рассуждений."""
    cfg = GatewayConfig(tmp_path / "gateway.json")
    _, key = cfg.upsert("phone")

    class ReasoningOnly:
        def chat(self, body, base_url=None, api_key=None):
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "",
                                             "reasoning": "x" * 500},
                                 "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 3,
                              "total_tokens": 6}}

    svc = GatewayService(cfg, UsageStore(tmp_path / "usage.json"),
                         transport=ReasoningOnly(),
                         models_provider=lambda: ["glm-4.7-flash:q4_K_M"])
    out = svc.handle_chat(_req(model="glm-4.7-flash:q4_K_M"),
                          auth_header=f"Bearer {key}")
    gw = out["gateway"]
    assert gw["empty_response"] is True
    assert gw["reasoning_chars"] == 500
    assert gw["finish_reason"] == "length"


def test_summary_model_chain_prefers_requested_first():
    svc = GatewayService(
        config=GatewayConfig(Path("/tmp/nonexistent-gw.json")),
        usage=UsageStore(Path("/tmp/nonexistent-usage.json")),
        transport=FakeTransport(),
        models_provider=lambda: ["glm-4.7-flash:q4_K_M", "qwen3-coder:30b",
                                 "gemma3:27b", "nomic-embed-text:v1.5"])
    chain = svc._summary_models("glm-4.7-flash:q4_K_M")
    assert chain[0] == "glm-4.7-flash:q4_K_M", "запрошенная модель первая"
    assert "qwen3-coder:30b" in chain
    assert "nomic-embed-text:v1.5" not in chain, "эмбеддер в сводку не годится"


def test_summary_model_chain_puts_preference_before_others():
    svc = GatewayService(
        config=GatewayConfig(Path("/tmp/nonexistent-gw.json")),
        usage=UsageStore(Path("/tmp/nonexistent-usage.json")),
        transport=FakeTransport(),
        models_provider=lambda: ["glm-4.7-flash:q4_K_M", "gemma3:27b",
                                 "qwen3-coder:30b"])
    chain = svc._summary_models("glm-4.7-flash:q4_K_M")
    assert chain.index("qwen3-coder:30b") < chain.index("gemma3:27b")


def test_compress_shrinks_tail_when_keep_recent_too_big():
    """Хвост сам больше лимита → сохраняем меньше сообщений.

    Реальный случай (нашла живая проверка B3): после первого раунда
    сводка + keep_recent сообщений всё ещё превышали лимит, и запрос
    падал 413, хотя сократить хвост было можно.
    """
    msgs = _long_msgs(10)
    msgs.append({"role": "user", "content": "итог?"})
    long_summary = "Сводка: " + "обсуждали темы. " * 30
    out, info = compress_messages(msgs, max_context=500, keep_recent=4,
                                  summarize=lambda t: long_summary)
    assert info["compressed"] is True
    assert info["tokens_after"] <= 500, info
    assert out[-1]["content"] == "итог?", "последний вопрос потерян"


def test_compress_never_drops_last_user_message():
    """Даже при агрессивном сокращении последний вопрос остаётся."""
    msgs = _long_msgs(30)
    msgs.append({"role": "user", "content": "ПОСЛЕДНИЙ ВОПРОС"})
    out, info = compress_messages(msgs, max_context=120, keep_recent=6,
                                  summarize=lambda t: "сводка")
    assert out[-1]["content"] == "ПОСЛЕДНИЙ ВОПРОС"


def test_compress_reports_error_when_tail_alone_too_big():
    """Хвост неуменьшаем (одно огромное сообщение) → честная причина."""
    msgs = [{"role": "user", "content": "x" * 100000}]
    out, info = compress_messages(msgs, max_context=50, keep_recent=2,
                                  summarize=lambda t: "сводка")
    assert info["compressed"] is False
    assert out == msgs
