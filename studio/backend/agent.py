"""Агент приложения «Студия».

Собирает запрос к LLM (системный промпт + блоки памяти + история диалога),
стримит ответ по SSE, ведёт журнал запросов (requests.json) и считает
сессионные токены (in-memory, обнуляются при рестарте).
"""
import json
import os
import threading
from datetime import datetime

import httpx

from memory import MemoryStore, atomic_write_json, read_json

# Ограничения контекста известных моделей; неизвестной — 32768.
CONTEXT_LIMITS = {
    "qwen3.8-27b": 32768,
    "deepseek-v4-flash": 16384,
    "glm-5.3-flash": 16384,
}
DEFAULT_CONTEXT_LIMIT = 32768

DEFAULT_CONFIG = {
    "model": "qwen3.8-27b",
    "temperature": 0.7,
    "max_tokens": 2048,
    "system_prompt": ("Ты — ассистент в обучающем приложении «Студия». "
                      "Отвечай по делу и кратко; если пользователь пишет по-русски — "
                      "отвечай на русском."),
}

JOURNAL_CAP = 100  # максимум записей в журнале (FIFO)


def _now() -> str:
    """Текущее время в формате 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StudioAgent:
    """Агент-обёртка над LLM API (OpenAI-совместимое) с памятью и журналом."""

    def __init__(self, data_dir: str, base_url: str = None, api_key: str = None, client=None):
        """Создаёт агента.

        data_dir — каталог данных (MemoryStore + config.json + requests.json);
        base_url/api_key — по умолчанию из окружения GPUSTACK_BASE_URL/GPUSTACK_API_KEY;
        client — httpx.Client (в тестах — с MockTransport).
        """
        self.store = MemoryStore(data_dir)
        self.base_url = (base_url or os.environ.get("GPUSTACK_BASE_URL",
                        "https://gpustack.data.lmru.tech/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("GPUSTACK_API_KEY", "")
        self._client = client or httpx.Client(timeout=120)
        self._lock = threading.Lock()  # только для журнала requests.json
        self._session = {"prompt": 0, "completion": 0, "total": 0}  # in-memory
        self._last_usage = None
        self._p_config = os.path.join(data_dir, "config.json")
        self._p_requests = os.path.join(data_dir, "requests.json")

    # ---------- конфиг ----------

    def get_config(self) -> dict:
        """Текущий конфиг (дефолты + сохранённое; битый файл -> дефолты)."""
        c = read_json(self._p_config, None)
        cfg = dict(DEFAULT_CONFIG)
        if isinstance(c, dict):
            for k in DEFAULT_CONFIG:
                if k in c:
                    cfg[k] = c[k]
        return cfg

    def set_config(self, updates: dict) -> dict:
        """Частично обновить конфиг и вернуть актуальный.

        Неизвестный ключ, неверный тип или значение вне диапазона ->
        ValueError (на русском), конфиг не меняется.
        """
        if not isinstance(updates, dict):
            raise ValueError("Конфиг должен быть объектом (dict)")
        cfg = self.get_config()
        for key, value in updates.items():
            if key == "model":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Поле model должно быть непустой строкой")
                cfg["model"] = value
            elif key == "temperature":
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not (0 <= value <= 2)):
                    raise ValueError("Поле temperature должно быть числом от 0 до 2")
                cfg["temperature"] = value
            elif key == "max_tokens":
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("Поле max_tokens должно быть целым числом больше 0")
                cfg["max_tokens"] = value
            elif key == "system_prompt":
                if not isinstance(value, str):
                    raise ValueError("Поле system_prompt должно быть строкой")
                cfg["system_prompt"] = value
            else:
                raise ValueError(f"Неизвестное поле конфига: {key}")
        atomic_write_json(self._p_config, cfg)
        return cfg

    # ---------- построение запроса ----------

    def build_payload(self, dialogue_id: str) -> list:
        """Список сообщений для LLM: [system (промпт + блоки памяти)] + история диалога."""
        cfg = self.get_config()
        system = cfg["system_prompt"] + self.store.build_memory_blocks(dialogue_id)
        return [{"role": "system", "content": system}] + self.store.get_messages(dialogue_id)

    # ---------- стриминг ответа ----------

    def ask_stream(self, dialogue_id: str, message: str):
        """Синхронный генератор: отправить сообщение в LLM и стримить события.

        События: {"type": "delta", "text"}, затем {"type": "done", "answer",
        "usage", "request_id"}; при любой ошибке — {"type": "error", "message"}
        (исключение наружу не бросается).
        """
        self.store.append_message(dialogue_id, "user", message)
        cfg = self.get_config()
        body = {
            "model": cfg["model"],
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": self.build_payload(dialogue_id),
        }
        usage = None
        parts = []
        error = None
        try:
            with self._client.stream(
                "POST", self.base_url + "/chat/completions", json=body,
                headers={"Authorization": "Bearer " + self.api_key}) as resp:
                if resp.status_code != 200:
                    error = f"Модель вернула ошибку HTTP {resp.status_code}"
                else:
                    for line in resp.iter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        choices = chunk.get("choices") or [{}]
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            parts.append(delta)
                            yield {"type": "delta", "text": delta}
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
        except httpx.HTTPError as e:
            error = f"Ошибка обращения к модели: {e}"
        except Exception as e:  # битый JSON чанка и пр.
            error = f"Непредвиденная ошибка стрима: {e}"

        if error is not None:
            self._append_request_log(cfg["model"], body, None, error)
            yield {"type": "error", "message": error}
            return

        answer = "".join(parts)
        self.store.append_message(dialogue_id, "assistant", answer)
        if usage:
            self._session["prompt"] += usage.get("prompt_tokens", 0)
            self._session["completion"] += usage.get("completion_tokens", 0)
            self._session["total"] += usage.get("total_tokens", 0)
            self._last_usage = usage
        rid = self._append_request_log(cfg["model"], body, usage, None)
        yield {"type": "done", "answer": answer, "usage": usage, "request_id": rid}

    # ---------- журнал запросов (requests.json) ----------

    def _append_request_log(self, model: str, body: dict, usage, error) -> int:
        """Добавить запись в журнал (cap 100, FIFO) и вернуть её id."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
            nid = max((e.get("id", 0) for e in entries if isinstance(e, dict)), default=0) + 1
            entries.append({"id": nid, "ts": _now(), "model": model,
                            "request": body, "usage": usage, "error": error})
            if len(entries) > JOURNAL_CAP:
                entries = entries[-JOURNAL_CAP:]
            atomic_write_json(self._p_requests, entries)
            return nid

    def requests_list(self) -> list:
        """Журнал без тел запросов: [{id, ts, model, total_tokens, error}]."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
        out = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            usage = e.get("usage")
            out.append({
                "id": e.get("id"),
                "ts": e.get("ts"),
                "model": e.get("model"),
                "total_tokens": usage.get("total_tokens") if isinstance(usage, dict) else None,
                "error": e.get("error"),
            })
        return out

    def requests_get(self, request_id: int):
        """Полная запись журнала по id (с телом запроса) или None."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
        for e in entries:
            if isinstance(e, dict) and e.get("id") == request_id:
                return e
        return None

    def requests_clear(self) -> None:
        """Очистить журнал запросов."""
        with self._lock:
            atomic_write_json(self._p_requests, [])

    # ---------- сессионные токены ----------

    def session_tokens(self) -> dict:
        """Суммарные токены текущей сессии {prompt, completion, total} (in-memory)."""
        return dict(self._session)

    def last_usage(self):
        """usage последнего успешного запроса (dict) или None."""
        return self._last_usage

    # ---------- список моделей ----------

    def list_models(self) -> list:
        """Список моделей API: [{id, context_limit}].

        Неизвестной модели — DEFAULT_CONTEXT_LIMIT; при недоступном API
        бросает httpx.HTTPError (роут вернёт 502).
        """
        resp = self._client.get(self.base_url + "/models",
                                headers={"Authorization": "Bearer " + self.api_key})
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Модель вернула ошибку HTTP {resp.status_code}",
                request=resp.request, response=resp)
        data = resp.json().get("data") or []
        return [{"id": m.get("id"),
                 "context_limit": CONTEXT_LIMITS.get(m.get("id"), DEFAULT_CONTEXT_LIMIT)}
                for m in data if isinstance(m, dict) and m.get("id")]
