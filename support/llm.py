"""LLM-клиент ассистента поддержки (день 33): OpenAI-совместимый /chat/completions.

Только stdlib (urllib). Совместим с GPustack (прод, ключи по моделям в .env) и
с Ollama (`/v1/chat/completions`). Поддерживает tool-calling — модель сама
запрашивает контекст тикета/пользователя через MCP:

    {"role": "assistant", "tool_calls": [{"id", "function": {"name", "arguments"}}]}
    -> {"role": "tool", "tool_call_id", "content": "<JSON результата>"}

`chat()` — один вызов (без стрима); `stream()` — генератор дельт для CLI.
Ошибки — LLMError с человекочитаемым RU-текстом (CLI показывает и продолжает).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://gpustack.data.lmru.tech/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
TIMEOUT = 180

# Модель -> переменная с ключом (порядок: .env репозитория дней 1–26).
_KEY_BY_MODEL = {
    "deepseek": "GPUSTACK_KEY_DEEPSEEK",
    "glm": "GPUSTACK_KEY_GLM",
    "qwen3.8": "GPUSTACK_API_KEY",
    "gpt-4o": "GPUSTACK_API_KEY",
}


class LLMError(RuntimeError):
    """Ошибка LLM (сеть / HTTP / пустой ответ) — RU-сообщение для CLI."""


def load_env_file(path: str) -> dict:
    """Простой .env-парсер (KEY=VALUE, # комментарии). Отсутствие — {}."""
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def key_for_model(model: str, env: dict) -> str:
    """Ключ API под модель: конкретный *_KEY_<МОДЕЛЬ>, иначе общий."""
    low = (model or "").lower()
    for prefix, var in _KEY_BY_MODEL.items():
        if low.startswith(prefix) and env.get(var):
            return env[var]
    return (env.get("GPUSTACK_API_KEY") or env.get("SUPPORT_API_KEY")
            or env.get("OPENAI_API_KEY") or "")


def _parse_arguments(raw) -> dict:
    """arguments приходят JSON-строкой (иногда объектом/битой) -> dict."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def normalize_tool_calls(raw) -> list:
    """tool_calls из сообщения модели -> [{id, name, arguments(dict)}]."""
    out = []
    for tc in raw or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        out.append({"id": tc.get("id") or "",
                    "name": str(name),
                    "arguments": _parse_arguments(fn.get("arguments"))})
    return out


class ChatClient:
    """Клиент /chat/completions (tools, stream)."""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None, timeout: int = TIMEOUT) -> None:
        self.base_url = (base_url or os.environ.get("GPUSTACK_BASE_URL")
                         or os.environ.get("SUPPORT_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("SUPPORT_MODEL") or DEFAULT_MODEL
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("SUPPORT_API_KEY") or "")
        self.timeout = timeout

    # --- транспорт ---

    def _request(self, payload: dict):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base_url + "/chat/completions",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise LLMError(f"LLM вернула HTTP {exc.code}: {body}") from None
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            raise LLMError(f"LLM недоступна ({self.base_url}): {exc}") from None

    # --- вызовы ---

    def chat(self, messages: list, tools: list | None = None,
             temperature: float = 0.2, max_tokens: int = 1400) -> dict:
        """Один вызов. Возврат: {"content", "tool_calls", "usage", "empty"}.

        empty=True — модель израсходовала лимит на reasoning и вернула
        пустой content без tool_calls (бывает у reasoning-моделей,
        deepseek-v4-flash: completion_tokens == max_tokens). Это не ошибка
        протокола: вызывающая сторона решает (сессия делает один повтор).
        """
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens,
                   "stream": False}
        if tools:
            payload["tools"] = tools
        with self._request(payload) as resp:
            try:
                data = json.load(resp)
            except ValueError:
                raise LLMError("LLM вернула не-JSON ответ") from None
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("LLM вернула пустой ответ (нет choices)")
        msg = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        content = msg.get("content") or ""
        calls = normalize_tool_calls(msg.get("tool_calls"))
        completion = int(usage.get("completion_tokens") or 0)
        return {"content": content, "tool_calls": calls, "usage": usage,
                "empty": (not content.strip() and not calls
                           and completion >= max_tokens)}

    def stream(self, messages: list, temperature: float = 0.2,
               max_tokens: int = 900):
        """Генератор текстовых дельт (SSE `data: {...}` / `[DONE]`)."""
        payload = {"model": self.model, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens,
                   "stream": True}
        with self._request(payload) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    return
                try:
                    data = json.loads(chunk)
                except ValueError:
                    continue
                for choice in data.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        yield delta

    def stream_text(self, messages: list, temperature: float = 0.2,
                    max_tokens: int = 900) -> str:
        """Стрим в строку (для e2e/скриптов без живого вывода)."""
        return "".join(self.stream(messages, temperature, max_tokens))


def strip_citation_garbage(text: str) -> str:
    """Убрать пустые маркеры источников из ответа модели (скобки без номера)."""
    return re.sub(r"\[\s*\]", "", text or "")
