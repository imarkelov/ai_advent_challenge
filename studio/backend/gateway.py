"""День 30: локальная LLM как приватный сетевой сервис.

Шлюз между устройствами в локальной сети и Ollama. Наружу (в LAN)
смотрит только он — Ollama остаётся на loopback. Шлюз даёт то, чего у
голой Ollama нет: **аутентификацию по ключам клиентов**, **rate limit**
(RPM/TPM на клиента, скользящее окно), **ограничение контекста** с
автоматической **LLM-компрессией** старых ходов и **учёт
использования**.

Почему шлюз, а не открытая Ollama
---------------------------------
Ollama не имеет ни ключей, ни лимитов, ни учёта: открыть её в LAN —
значит разрешить любому устройству в сети бесконтрольно занимать GPU
и память под произвольный контекст. Шлюз — единственная точка, где
есть все три вещи, и он же — единственный вход из сети.

Почему компрессия, а не отказ при превышении контекста
-----------------------------------------------------
Жёсткий отказ (400/413) ломает основной сценарий — длинный диалог с
телефона, который не умеет сжимать историю сам. Поэтому `max_context`
работает как **триггер компрессии**: старые ходы сжимаются
LLM-сводкой (та же локальная модель), `system`-промпт и последние
`keep_recent` сообщений сохраняются целиком. Если и после сжатия вход
не влезает — тогда 413 с точными числами (никакой молчаливой обрезки).
Результат виден клиенту: блок `gateway` в ответе и заголовок
`x-gateway-compressed`.

Только stdlib + httpx; модуль импортируется без побочных эффектов
(сеть при импорте не трогается).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import httpx

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

#: Скользящее окно лимитов, секунды.
WINDOW_SEC = 60.0

#: Префикс ключей клиентов шлюза.
KEY_PREFIX = "sk-local-"

#: Сколько раундов компрессии допускается на один запрос.
MAX_COMPRESS_ROUNDS = 4

#: Сколько последних сообщений при компрессии сохраняются целиком.
DEFAULT_KEEP_RECENT = 4

#: Размер кольцевого лога отказов.
DEFAULT_REJECT_LOG = 100

#: Дефолтный LAN-порт шлюза.
DEFAULT_GATEWAY_PORT = 8110

#: Модели, предпочитаемые для служебной сводки (день 30). Выбор обоснован
#: замером дня 29: `qwen3-coder:30b` — MoE с 3B активных параметров,
#: заметно быстрее плотных моделей и без «размышлений», которые съедают
#: бюджет ответа на служебном вызове.
DEFAULT_SUMMARY_PREFERENCE = ("qwen3-coder:30b", "gemma3:27b")

#: Таймаут обращения к Ollama (первый вызов грузит веса с диска).
OLLAMA_TIMEOUT = 300.0

#: Промпт сводки. Требование «не терять факты молча» — из design.md:
#: сводка обязана сохранять цель, ограничения, решения, имена и числа.
SUMMARY_SYSTEM_PROMPT = (
    "Ты сжимаешь историю диалога в краткую сводку. Сохрани ОБЯЗАТЕЛЬНО: "
    "цель пользователя, все ограничения и требования, принятые решения, "
    "договорённости, имена, названия, числа, даты и технические детали. "
    "Отбрось любезности, повторы и воду. Пиши сжато, тезисами, на языке "
    "диалога. Не добавляй ничего от себя и не отвечай на вопросы — "
    "только сводка."
)

SUMMARY_MARKER = "[СЖАТАЯ ИСТОРИЯ ДИАЛОГА]"


# ---------------------------------------------------------------------------
# Ошибки (в формате OpenAI)
# ---------------------------------------------------------------------------

class GatewayError(Exception):
    """Ошибка шлюза с HTTP-статусом и кодом в формате OpenAI."""

    def __init__(self, message: str, status: int = 400,
                 code: str = "invalid_request_error",
                 type_: str = "invalid_request_error",
                 retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.type = type_
        self.retry_after = retry_after

    def to_body(self) -> dict:
        """Тело ответа в формате ошибки OpenAI."""
        return {"error": {"message": self.message, "type": self.type,
                          "code": self.code, "param": None}}


class AuthError(GatewayError):
    """401/403: ключ отсутствует, неизвестен или клиент выключен."""

    def __init__(self, message: str, status: int = 401,
                 code: str = "invalid_api_key"):
        super().__init__(message, status=status, code=code,
                         type_="invalid_request_error")


class RateLimitError(GatewayError):
    """429: превышен RPM/TPM клиента."""

    def __init__(self, message: str, retry_after: int = 1):
        super().__init__(message, status=429, code="rate_limit_exceeded",
                         type_="rate_limit_error", retry_after=retry_after)


# ---------------------------------------------------------------------------
# Ключи
# ---------------------------------------------------------------------------

def hash_key(key: str) -> str:
    """SHA-256 ключа: в конфиге хранится только хэш."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def mask_key(key: str | None) -> str:
    """Маскированный ключ для UI/API: `sk-local-…cdef`.

    Полный ключ показывается ровно один раз — при создании клиента.
    Короткие строки маскируются целиком, чтобы не утекал хвост.
    """
    if not key:
        return ""
    if len(key) <= 12:
        return "…"
    return f"{key[:9]}…{key[-4:]}"


def generate_key() -> str:
    """Новый ключ клиента: `sk-local-<32 hex>` (128 бит энтропии)."""
    return KEY_PREFIX + secrets.token_hex(16)


# ---------------------------------------------------------------------------
# Клиент и его лимиты
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GatewayClient:
    """Устройство/пользователь с ключом и набором лимитов.

    frozen: клиент — конфигурация, а не состояние. Изменение — через
    `GatewayConfig.upsert()` (создаёт новую запись).

    `rpm`/`tpm`/`max_context`/`max_output_tokens` == 0 означает
    «без лимита». `allow_models` пустой — разрешены все модели;
    поддерживается wildcard `gemma3:*`.
    """

    name: str
    key_hash: str
    rpm: int = 60
    tpm: int = 20000
    max_context: int = 8192
    max_output_tokens: int = 2048
    allow_models: tuple[str, ...] = ()
    enabled: bool = True
    note: str = ""
    created_at: float = 0.0

    def __post_init__(self):
        if not self.name or not str(self.name).strip():
            raise ValueError("Имя клиента не может быть пустым")
        if not self.key_hash:
            raise ValueError("У клиента должен быть ключ")
        for fname in ("rpm", "tpm", "max_context", "max_output_tokens"):
            value = getattr(self, fname)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"Поле {fname} должно быть целым числом")
            if value < 0:
                raise ValueError(f"Поле {fname} не может быть отрицательным")

    def allows_model(self, model: str) -> bool:
        """Разрешена ли модель этому клиенту (пустой список = все)."""
        if not self.allow_models:
            return True
        for pattern in self.allow_models:
            if pattern == model:
                return True
            if pattern.endswith("*") and model.startswith(pattern[:-1]):
                return True
        return False

    def to_public(self) -> dict:
        """Представление для API/UI: без хэша ключа, с маской."""
        data = asdict(self)
        data.pop("key_hash", None)
        data["allow_models"] = list(self.allow_models)
        data["key_masked"] = ""
        return data


# ---------------------------------------------------------------------------
# Конфигурация клиентов
# ---------------------------------------------------------------------------

@dataclass
class GatewayConfig:
    """Хранилище клиентов (`gateway.json`) с hot-reload.

    Файл читается на каждое обращение (клиентов мало, файл крошечный),
    поэтому новый клиент действует **без рестарта** процесса. Запись —
    атомарная (временный файл + `os.replace`), чтобы падение в момент
    сохранения не оставило битый конфиг.
    """

    path: Path

    def __post_init__(self):
        self.path = Path(self.path)
        self._lock = threading.Lock()

    # --- чтение ---------------------------------------------------------

    def _read(self) -> dict:
        if not self.path.exists():
            return {"clients": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Битый конфиг не должен ронять сервис: считаем пустым.
            return {"clients": []}
        if not isinstance(data, dict):
            return {"clients": []}
        clients = data.get("clients")
        return {"clients": clients if isinstance(clients, list) else []}

    def _client_from(self, raw: dict) -> GatewayClient | None:
        try:
            return GatewayClient(
                name=raw["name"],
                key_hash=raw["key_hash"],
                rpm=int(raw.get("rpm", 60)),
                tpm=int(raw.get("tpm", 20000)),
                max_context=int(raw.get("max_context", 8192)),
                max_output_tokens=int(raw.get("max_output_tokens", 2048)),
                allow_models=tuple(raw.get("allow_models") or ()),
                enabled=bool(raw.get("enabled", True)),
                note=str(raw.get("note") or ""),
                created_at=float(raw.get("created_at") or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            # Некорректная запись пропускается, а не роняет весь конфиг.
            return None

    def list_clients(self) -> list[GatewayClient]:
        """Все валидные клиенты."""
        out = []
        for raw in self._read()["clients"]:
            if isinstance(raw, dict):
                c = self._client_from(raw)
                if c is not None:
                    out.append(c)
        return out

    def get(self, name: str) -> GatewayClient | None:
        for c in self.list_clients():
            if c.name == name:
                return c
        return None

    def find_any_by_key(self, key: str) -> GatewayClient | None:
        """Клиент по ключу **без** учёта enabled (для различения 401/403)."""
        if not key:
            return None
        target = hash_key(key)
        for c in self.list_clients():
            # compare_digest: сравнение не течёт по времени.
            if hmac.compare_digest(c.key_hash, target):
                return c
        return None

    def find_by_key(self, key: str) -> GatewayClient | None:
        """Активный (enabled) клиент по ключу; иначе None."""
        c = self.find_any_by_key(key)
        return c if (c is not None and c.enabled) else None

    # --- запись ---------------------------------------------------------

    def _write(self, clients: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "clients": clients}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def _raw_clients(self) -> list[dict]:
        return [c for c in self._read()["clients"] if isinstance(c, dict)]

    def upsert(self, name: str, *, rpm: int | None = None,
               tpm: int | None = None, max_context: int | None = None,
               max_output_tokens: int | None = None,
               allow_models: list[str] | tuple[str, ...] | None = None,
               enabled: bool | None = None, note: str | None = None,
               rotate_key: bool = False,
               key: str | None = None) -> tuple[GatewayClient, str | None]:
        """Создать или обновить клиента.

        Возвращает `(клиент, plaintext_key)`. Ключ возвращается **только
        когда он создан или повёрнут** — иначе `None` (в конфиге лежит
        лишь хэш, восстановить ключ невозможно).
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Имя клиента не может быть пустым")
        with self._lock:
            raws = self._raw_clients()
            existing = None
            for raw in raws:
                if raw.get("name") == name:
                    existing = raw
                    break

            plaintext: str | None = None
            if existing is None or rotate_key or key:
                plaintext = key or generate_key()
                key_hash = hash_key(plaintext)
            else:
                key_hash = existing["key_hash"]

            base = dict(existing or {})
            base["name"] = name
            base["key_hash"] = key_hash
            base.setdefault("created_at", time.time())
            if rpm is not None:
                base["rpm"] = rpm
            if tpm is not None:
                base["tpm"] = tpm
            if max_context is not None:
                base["max_context"] = max_context
            if max_output_tokens is not None:
                base["max_output_tokens"] = max_output_tokens
            if allow_models is not None:
                # Дубли в белом списке не нужны, порядок сохраняем.
                seen: list[str] = []
                for m in allow_models:
                    m = str(m).strip()
                    if m and m not in seen:
                        seen.append(m)
                base["allow_models"] = seen
            if enabled is not None:
                base["enabled"] = enabled
            if note is not None:
                base["note"] = note

            base.setdefault("rpm", 60)
            base.setdefault("tpm", 20000)
            base.setdefault("max_context", 8192)
            base.setdefault("max_output_tokens", 2048)
            base.setdefault("allow_models", [])
            base.setdefault("enabled", True)
            base.setdefault("note", "")

            client = self._client_from(base)
            if client is None:  # pragma: no cover — защита от мусора
                raise ValueError("Некорректные параметры клиента")

            if existing is not None:
                raws = [r for r in raws if r.get("name") != name]
            raws.append(asdict(client))
            self._write(raws)
            return client, plaintext

    def delete(self, name: str) -> bool:
        """Удалить клиента; False — если такого не было."""
        with self._lock:
            raws = self._raw_clients()
            kept = [r for r in raws if r.get("name") != name]
            if len(kept) == len(raws):
                return False
            self._write(kept)
            return True

    def bootstrap_from_env(self, key: str, name: str = "default",
                           **kw) -> GatewayClient | None:
        """Создать дефолтного клиента из `.env`, если клиентов ещё нет.

        Идемпотентно: при повторном старте ничего не меняет, поэтому
        сервис доступен сразу после первого запуска, а ключ не
        «переезжает» между перезапусками.
        """
        key = (key or "").strip()
        if not key:
            return None
        if self.list_clients():
            return None
        client, _ = self.upsert(name, key=key, **kw)
        return client


# ---------------------------------------------------------------------------
# Rate limiter (скользящее окно)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Ограничитель RPM/TPM по **скользящему** окну 60 с.

    Фиксированная минута даёт двойной залп на стыке минут (классический
    баг лимитеров): клиент отправляет `rpm` запросов в 59.9 с и ещё
    столько же в 60.1 с. Скользящее окно это исключает.

    Потокобезопасен: FastAPI выполняет sync-хендлеры в пуле потоков,
    поэтому все обращения идут под одним `Lock`.
    """

    def __init__(self, window: float = WINDOW_SEC):
        self.window = window
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune(self, dq: deque, now: float) -> None:
        cutoff = now - self.window
        while dq and dq[0][0] <= cutoff:
            dq.popleft()

    def check(self, client: str, *, rpm: int, tpm: int, tokens: int,
              now: float | None = None) -> None:
        """Проверить и **зафиксировать** запрос.

        Бросает `RateLimitError`, если запрос не проходит. Отказ окно
        **не расходует** (иначе клиент блокировал бы сам себя
        повторами), поэтому проверка идёт до записи события.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._events.setdefault(client, deque())
            self._prune(dq, now)

            if rpm:
                # Каждое событие в окне — один запрос (токены во втором
                # поле кортежа).
                count = len(dq)
                if count + 1 > rpm:
                    retry = self._retry_after(dq, now)
                    raise RateLimitError(
                        f"Превышен лимит запросов: {rpm} в минуту. "
                        f"Повторите через {retry} с.", retry_after=retry)

            if tpm:
                used = sum(w for _, w in dq)
                if used + tokens > tpm:
                    # Ждём, пока из окна уйдёт столько событий, чтобы
                    # запрос поместился; пустое окно (первый же запрос
                    # больше лимита) — ждать нечего, но окно всё равно
                    # должно быть непустым для расчёта.
                    retry = self._retry_after_tokens(dq, now, used, tokens, tpm)
                    raise RateLimitError(
                        f"Превышен лимит токенов: {tpm} в минуту "
                        f"(запрошено ещё {tokens}). Повторите через "
                        f"{retry} с.", retry_after=retry)

            dq.append((now, max(0, int(tokens))))

    def _retry_after(self, dq: deque, now: float) -> int:
        """Через сколько секунд самое старое событие покинет окно."""
        if not dq:
            return 1
        return max(1, math.ceil(dq[0][0] + self.window - now))

    def _retry_after_tokens(self, dq: deque, now: float, used: int,
                            tokens: int, tpm: int) -> int:
        """Через сколько секунд в окне освободится место под запрос.

        Идём от старых событий к новым: как только освобождённого
        объёма хватает, момент ухода этого события и есть время
        ожидания. Пустое окно — запрос больше лимита сам по себе, ждать
        бессмысленно, но возвращаем положительное число.
        """
        if not dq:
            return 1
        freed = 0
        for ts, w in dq:
            freed += w
            if used + tokens - freed <= tpm:
                return max(1, math.ceil(ts + self.window - now))
        return self._retry_after(dq, now)

    def refund(self, client: str, *, tokens: int) -> None:
        """Вернуть токены неудачного запроса (упавший вызов не жжёт TPM)."""
        if not tokens:
            return
        with self._lock:
            dq = self._events.get(client)
            if not dq:
                return
            for i in range(len(dq) - 1, -1, -1):
                ts, w = dq[i]
                if w >= tokens:
                    dq[i] = (ts, w - tokens)
                    return

    def reconcile(self, client: str, *, reserved: int, actual: int) -> None:
        """Уточнить расход по факту: `reserved` → `actual`.

        До вызова доступны только оценка токенов промпта и неизвестный
        размер ответа, поэтому в окно резервируется оценка. После ответа
        Ollama сообщает фактические токены — разницу возвращаем (или
        довзыскиваем), чтобы TPM отражал реальное потребление, а не
        оценку. Отрицательный остаток не создаём.
        """
        delta = int(actual) - int(reserved)
        if delta == 0:
            return
        if delta < 0:
            self.refund(client, tokens=-delta)
            return
        with self._lock:
            dq = self._events.get(client)
            if not dq:
                return
            ts, w = dq[-1]
            dq[-1] = (ts, w + delta)

    def reset(self, client: str) -> None:
        with self._lock:
            self._events.pop(client, None)

    def snapshot(self, client: str, *, now: float | None = None) -> dict:
        """Текущее использование окна (для UI)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._events.get(client)
            if not dq:
                return {"requests": 0, "tokens": 0}
            self._prune(dq, now)
            return {"requests": len(dq), "tokens": sum(w for _, w in dq)}


# ---------------------------------------------------------------------------
# Токены
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Оценка токенов: `ceil(символы / 4)`.

    Та же эвристика, что в `memory._tok_est` (дни 8–29): без загрузки
    токенизатора конкретной модели иначе не обойтись, а согласованность
    с остальным проектом важнее абсолютной точности. Числа
    сопоставимы между учётом, лимитом и UI.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def _content_text(content: Any) -> str:
    """Текст из `content`: строка, список частей (мультимодальный) или None."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or ""))
            else:
                parts.append(str(p))
        return "".join(parts)
    return str(content)


def _first_content(resp: dict, key: str = "choices") -> str:
    """Текст первого ответа модели из ответа API (OpenAI или нативного).

    OpenAI-совместимый формат: `choices[0].message.content`; нативный
    Ollama: `message.content`. Возвращает пустую строку, если контента
    нет (reasoning-модель потратила бюджет на размышления).
    """
    if not isinstance(resp, dict):
        return ""
    if key == "message":
        msg = resp.get("message") or {}
        return _content_text(msg.get("content")).strip()
    choices = resp.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    return _content_text(msg.get("content")).strip()


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Суммарная оценка токенов списка сообщений."""
    total = 0
    for m in messages or []:
        if isinstance(m, dict):
            total += estimate_tokens(_content_text(m.get("content")))
    return total


# ---------------------------------------------------------------------------
# Компрессия контекста
# ---------------------------------------------------------------------------

def _render_for_summary(messages: list[dict]) -> str:
    """Текст старых ходов для сводки: с ролями, чтобы не потерять смысл."""
    labels = {"user": "Пользователь", "assistant": "Ассистент",
              "system": "Система", "tool": "Инструмент"}
    lines = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = labels.get(str(m.get("role")), str(m.get("role")))
        text = _content_text(m.get("content")).strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _summary_message(summary: str) -> dict:
    """Сводка как system-сообщение с явной пометкой.

    Пометка нужна, чтобы модель не приняла сжатую историю за текущий
    запрос пользователя.
    """
    return {"role": "system",
            "content": f"{SUMMARY_MARKER}\n{summary.strip()}"}


def compress_messages(
    messages: list[dict],
    *,
    max_context: int,
    summarize: Callable[[str], str],
    keep_recent: int = DEFAULT_KEEP_RECENT,
    max_rounds: int = MAX_COMPRESS_ROUNDS,
) -> tuple[list[dict], dict]:
    """Сжать старые ходы диалога LLM-сводкой, если вход не влезает.

    Возвращает `(messages, info)`, где `info` описывает, что произошло
    (`compressed`, раунды, сообщения и токены до/после, `error`).

    Инварианты:

    * `system`-промпт (первое сообщение) сохраняется всегда;
    * последние `keep_recent` сообщений сохраняются **целиком** —
      сжимается только «голова» диалога;
    * уже существующая сводка не пересжимается повторно, а уходит
      входом в следующий вызов сводки;
    * цикл защищён от отсутствия прогресса: если сводка не уменьшает
      контекст, раунды прекращаются (иначе — бесконечный цикл);
    * падение сводки не роняет запрос: компрессия деградирует
      (`compressed=False` + `error`), вызывающий код решает, 413 это
      или нет.
    """
    original = list(messages or [])
    before = estimate_messages_tokens(original)
    info = {
        "compressed": False, "rounds": 0,
        "messages_before": len(original), "messages_after": len(original),
        "tokens_before": before, "tokens_after": before,
        "error": None,
    }

    if not original or max_context <= 0 or before <= max_context:
        return original, info

    # system-промпт отделяем: он неприкосновенен, но именно он может
    # быть причиной переполнения (огромный системный контекст RAG).
    head = original[0] if original and original[0].get("role") == "system" else None
    body = original[1:] if head is not None else original

    system_tokens = estimate_tokens(_content_text(head.get("content"))) if head else 0
    # Если один только system-промпт больше лимита — сжатие диалога не
    # поможет, сообщаем честно.
    if system_tokens > max_context:
        info["error"] = ("system-промпт больше лимита контекста "
                         f"({system_tokens} > {max_context})")
        return original, info

    current = list(body)
    current_summary_msg: dict | None = None
    rounds = 0

    def assemble(summary_msg, tail):
        return ([head] if head else []) + \
               ([summary_msg] if summary_msg else []) + list(tail)

    def last_user_index(msgs):
        """Индекс последнего сообщения пользователя — его терять нельзя."""
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                return i
        return len(msgs) - 1 if msgs else 0

    while rounds < max_rounds:
        assembled = assemble(current_summary_msg, current)
        if estimate_messages_tokens(assembled) <= max_context:
            break

        # Сколько сообщений можно оставить «как есть». Сначала пробуем
        # keep_recent, но если хвост сам больше лимита — уменьшаем его,
        # никогда не выбрасывая последнее сообщение пользователя.
        cut = len(current) - keep_recent
        min_keep = len(current) - last_user_index(current)
        if cut <= 0:
            # Хвост уже минимален: сжимать больше нечего.
            if rounds == 0:
                info["error"] = info["error"] or (
                    "Сжать не удалось: достигнут минимум сохраняемых "
                    "сообщений")
            break

        old_block = current[:cut]
        keep_block = current[cut:]
        # Старая сводка участвует во входе сводки, но не сжимается дважды.
        payload = ([current_summary_msg] if current_summary_msg else []) + old_block

        try:
            new_summary = summarize(_render_for_summary(payload))
        except Exception as exc:  # noqa: BLE001 — деградация, а не падение
            info["error"] = f"Сводка недоступна: {exc}"
            return original, info

        new_summary = (new_summary or "").strip()
        if not new_summary:
            info["error"] = "Сводка пуста"
            return original, info

        candidate_summary = _summary_message(new_summary)
        candidate = assemble(candidate_summary, keep_block)
        # Защита от отсутствия прогресса: сводка не должна увеличивать
        # контекст (иначе цикл не сойдётся). Сводка, которая больше
        # сжимаемого блока, бесполезна — прекращаем и сообщаем причину.
        if estimate_messages_tokens(candidate) >= estimate_messages_tokens(assembled):
            info["error"] = ("Сводка не уменьшает контекст "
                             f"({estimate_messages_tokens(candidate)} ≥ "
                             f"{estimate_messages_tokens(assembled)} токенов)")
            break

        current_summary_msg = candidate_summary
        current = keep_block
        rounds += 1

        # Одного раунда мало: вход всё ещё больше лимита, а хвост можно
        # сократить (не выбрасывая последний вопрос пользователя) —
        # сохраняем ещё меньше сообщений и сжимаем их следующим раундом.
        while (estimate_messages_tokens(assemble(current_summary_msg, current))
               > max_context and len(current) - 1 > min_keep):
            current = current[1:]

    if rounds == 0:
        # Либо уже влезает, либо сжать нельзя — возвращаем исходное.
        return original, info

    result = assemble(current_summary_msg, current)
    after = estimate_messages_tokens(result)
    info.update({
        "compressed": True,
        "rounds": rounds,
        "messages_after": len(result),
        "tokens_after": after,
    })
    return result, info


# ---------------------------------------------------------------------------
# Учёт использования
# ---------------------------------------------------------------------------

@dataclass
class UsageStore:
    """Счётчики запросов/токенов/отказов по клиентам + лог отказов.

    Персистентно (`usage.json`): переживает рестарт процесса, иначе
    учёт использования теряет смысл. Битый файл не роняет сервис —
    считается пустым.
    """

    path: Path
    reject_log_size: int = DEFAULT_REJECT_LOG

    def __post_init__(self):
        self.path = Path(self.path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"clients": {}, "rejections": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"clients": {}, "rejections": []}
        if not isinstance(data, dict):
            return {"clients": {}, "rejections": []}
        if not isinstance(data.get("clients"), dict):
            data["clients"] = {}
        if not isinstance(data.get("rejections"), list):
            data["rejections"] = []
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def record(self, client: str, *, tokens_in: int = 0, tokens_out: int = 0,
               latency: float | None = None, compressed: bool = False) -> None:
        """Учесть успешный запрос."""
        with self._lock:
            data = self._read()
            entry = data["clients"].setdefault(client, {})
            entry["requests"] = int(entry.get("requests", 0)) + 1
            entry["tokens_in"] = int(entry.get("tokens_in", 0)) + int(tokens_in)
            entry["tokens_out"] = int(entry.get("tokens_out", 0)) + int(tokens_out)
            if compressed:
                entry["compressed"] = int(entry.get("compressed", 0)) + 1
            entry["last_seen"] = time.time()
            if latency is not None:
                prev = float(entry.get("last_latency") or 0.0)
                entry["last_latency"] = round(float(latency), 3)
                entry["avg_latency"] = round(
                    (prev + float(latency)) / 2 if prev else float(latency), 3)
            self._write(data)

    def record_rejection(self, client: str, *, code: str, message: str,
                         status: int = 0) -> None:
        """Учесть отклонённый запрос (кольцевой лог + счётчик)."""
        with self._lock:
            data = self._read()
            name = client or "(аноним)"
            entry = data["clients"].setdefault(name, {})
            entry["rejected"] = int(entry.get("rejected", 0)) + 1
            entry["last_seen"] = time.time()
            log = data["rejections"]
            log.append({"ts": time.time(), "client": name, "code": code,
                        "status": status, "message": message[:300]})
            if len(log) > self.reject_log_size:
                del log[:-self.reject_log_size]
            self._write(data)

    def stats(self, client: str) -> dict:
        """Счётчики клиента (нули, если записей нет)."""
        with self._lock:
            entry = dict(self._read()["clients"].get(client) or {})
        entry.setdefault("requests", 0)
        entry.setdefault("tokens_in", 0)
        entry.setdefault("tokens_out", 0)
        entry.setdefault("rejected", 0)
        entry.setdefault("compressed", 0)
        entry["tokens_total"] = entry["tokens_in"] + entry["tokens_out"]
        entry["client"] = client
        return entry

    def all_stats(self) -> dict[str, dict]:
        """Счётчики всех клиентов, у которых есть активность."""
        with self._lock:
            names = list(self._read()["clients"].keys())
        return {n: self.stats(n) for n in names}

    def rejections(self, limit: int = 50) -> list[dict]:
        """Последние отклонённые запросы (свежие — в конце)."""
        with self._lock:
            log = list(self._read()["rejections"])
        return log[-limit:]

    def reset(self, client: str) -> None:
        """Сбросить счётчики клиента."""
        with self._lock:
            data = self._read()
            data["clients"].pop(client, None)
            self._write(data)


# ---------------------------------------------------------------------------
# Транспорт к Ollama
# ---------------------------------------------------------------------------

class OllamaTransport:
    """HTTP-транспорт к Ollama (OpenAI-совместимый `/v1`)."""

    def __init__(self, base_url: str, api_key: str = "ollama",
                 timeout: float = OLLAMA_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, body: dict, base_url: str | None = None,
             api_key: str | None = None) -> dict:
        """Не-стриминговый вызов; бросает исключение при ошибке/не-200."""
        url = (base_url or self.base_url).rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {api_key or self.api_key}"})
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp.json()

    def native_chat(self, body: dict, base_url: str | None = None,
                    api_key: str | None = None) -> dict:
        """Нативный Ollama `/api/chat` (нужен для reasoning-моделей).

        На этом эндпоинте `content` и `thinking` разделены, тогда как
        OpenAI-совместимый путь у таких моделей отдаёт пустой `content`.
        """
        base = (base_url or self.base_url).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(base + "/api/chat", json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp.json()

    def stream(self, body: dict, base_url: str | None = None,
               api_key: str | None = None):
        """Стриминговый вызов: контекст-менеджер httpx с `iter_lines`."""
        url = (base_url or self.base_url).rstrip("/") + "/chat/completions"
        client = httpx.Client(timeout=self.timeout)
        req = client.build_request(
            "POST", url, json=body,
            headers={"Authorization": f"Bearer {api_key or self.api_key}"})
        resp = client.send(req, stream=True)
        if resp.status_code != 200:
            resp.close()
            client.close()
            raise RuntimeError(f"Ollama вернула HTTP {resp.status_code}")
        return resp, client


def resolve_model(requested: str | None, available: list[str]) -> str:
    """Проверить запрошенную модель; пусто — первая доступная.

    Неизвестная модель → 404: клиент должен понимать, что опечатался,
    а не получать ответ от «какой-то другой» модели. Пустой каталог
    (Ollama недоступна) не блокирует запрос — его отловит транспорт.
    """
    if not requested:
        if available:
            return available[0]
        raise GatewayError("Не указана модель и список моделей пуст",
                           status=400, code="model_required")
    if available and requested not in available:
        raise GatewayError(
            f"Модель «{requested}» недоступна. Доступны: "
            f"{', '.join(available)}", status=404, code="model_not_found")
    return requested


# ---------------------------------------------------------------------------
# Сервис шлюза
# ---------------------------------------------------------------------------

@dataclass
class GatewayService:
    """Полный конвейер обработки запроса клиента.

    Порядок важен: сначала аутентификация (не тратим ресурсы на
    анонимов), затем модель, затем rate limit (не даём занять GPU), и
    только потом — компрессия, которая сама стоит LLM-вызова.
    """

    config: GatewayConfig
    usage: UsageStore
    transport: Any
    summary_transport: Any | None = None
    limiter: RateLimiter = field(default_factory=RateLimiter)
    models_provider: Callable[[], list[str]] = field(default=lambda: [])
    keep_recent: int = DEFAULT_KEEP_RECENT
    enabled: bool = True
    #: Предпочтительные модели для служебной сводки (по порядку). День 29
    #: показал, что qwen3-coder:30b — самая быстрая и послушная локальная
    #: модель; reasoning-модели (glm) на сводке отдают пустой content
    #: или падают 500. Список применяется, только если модель доступна.
    summary_preference: tuple[str, ...] = DEFAULT_SUMMARY_PREFERENCE

    def __post_init__(self):
        if self.summary_transport is None:
            self.summary_transport = self.transport

    # --- вспомогательное ------------------------------------------------

    def available_models(self) -> list[str]:
        try:
            return list(self.models_provider() or [])
        except Exception:  # noqa: BLE001 — каталог не критичен для запроса
            return []

    @staticmethod
    def _parse_key(auth_header: str | None) -> str:
        """Ключ из `Authorization`; поддерживается и голый ключ."""
        if not auth_header:
            return ""
        value = auth_header.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return value

    def authenticate(self, auth_header: str | None) -> GatewayClient:
        """Клиент по заголовку авторизации; иначе AuthError."""
        key = self._parse_key(auth_header)
        if not key:
            raise AuthError("Не передан ключ API. Заголовок: "
                            "Authorization: Bearer <ключ>", status=401,
                            code="missing_api_key")
        client = self.config.find_any_by_key(key)
        if client is None:
            raise AuthError("Неизвестный ключ API", status=401,
                            code="invalid_api_key")
        if not client.enabled:
            raise AuthError(f"Клиент «{client.name}» отключён", status=403,
                            code="client_disabled")
        return client

    def status(self, *, host: str = "127.0.0.1",
               port: int = DEFAULT_GATEWAY_PORT) -> dict:
        """Состояние сервиса + адрес для подключения с других устройств."""
        host = (host or "127.0.0.1").strip()
        return {
            "enabled": self.enabled,
            "host": host,
            "port": port,
            "lan_url": f"http://{host}:{port}/v1",
            "chat_url": f"http://{host}:{port}/v1/chat/completions",
            "models": self.available_models(),
            "clients": len(self.config.list_clients()),
            "tls": False,
            "note": ("Только локальная сеть: трафик идёт по HTTP, ключ "
                     "передаётся открытым текстом. Наружу не выставлять."),
        }

    # --- основной конвейер ----------------------------------------------

    def _authorize(self, body: dict, auth_header: str | None,
                   client: GatewayClient | None = None) -> tuple:
        """Общая для stream/non-stream часть: ключ → модель → лимит → контекст.

        Возвращает `(client, model, messages, upstream_body, tokens_in,
        compress_info)`. Rate limit фиксируется здесь, поэтому
        стриминговый и обычный пути лимитируются **одинаково** — нельзя
        обойти лимит, попросив `stream: true`.

        `client` можно передать уже аутентифицированным: вызывающий код
        делает это, чтобы узнать имя клиента для учёта отказов ещё до
        проверки лимитов.
        """
        client = client or self.authenticate(auth_header)

        if not self.enabled:
            raise GatewayError("Сервис выключен", status=503,
                               code="service_disabled")

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            raise GatewayError("Поле messages обязательно и не может "
                               "быть пустым", status=400,
                               code="invalid_messages")

        model = resolve_model(body.get("model"), self.available_models())
        if not client.allows_model(model):
            raise GatewayError(
                f"Модель «{model}» не разрешена клиенту "
                f"«{client.name}»", status=403,
                code="model_not_allowed")

        tokens_in = estimate_messages_tokens(messages)

        # Rate limit: проверка+фиксация до всего остального. Отказ окно
        # не расходует (см. RateLimiter.check).
        self.limiter.check(client.name, rpm=client.rpm, tpm=client.tpm,
                           tokens=tokens_in)

        messages, compress_info = self._apply_context_limit(
            client, messages, model)
        upstream = self._build_upstream_body(body, messages, model, client)
        return client, model, messages, upstream, tokens_in, compress_info

    def handle_chat(self, body: dict, *, auth_header: str | None,
                    stream: bool = False, request_host: str | None = None,
                    request_port: int | None = None) -> dict:
        """Обработать `POST /v1/chat/completions`.

        Бросает `GatewayError` (в т.ч. `AuthError`/`RateLimitError`);
        роут превращает её в ответ в формате OpenAI.
        """
        client_name = ""
        try:
            # Аутентификация отдельно от конвейера: имя клиента нужно
            # раньше проверки лимитов, иначе отказ по лимиту запишется
            # анонимно и клиент не увидит своих отказов.
            auth_client = self.authenticate(auth_header)
            client_name = auth_client.name
            client, model, messages, out_body, tokens_in, compress_info = \
                self._authorize(body, auth_header, client=auth_client)
            client_name = client.name

            started = time.monotonic()
            try:
                upstream = self.transport.chat(out_body, None, None)
            except Exception as exc:  # noqa: BLE001 — 502 наружу
                # Упавший вызов не жжёт TPM (токены не потрачены), но
                # RPM расходуется: запрос действительно пришёл в шлюз,
                # иначе сбойная модель давала бы неограниченный поток.
                self.limiter.refund(client.name, tokens=tokens_in)
                raise GatewayError(f"Локальная модель недоступна: {exc}",
                                   status=502, code="upstream_error")
            latency = time.monotonic() - started

            usage = upstream.get("usage") or {}
            tokens_out = int(usage.get("completion_tokens") or 0)
            # Для учёта предпочитаем фактические токены промпта от Ollama:
            # локальная оценка нужна была только для лимита ДО вызова.
            tokens_in_actual = int(usage.get("prompt_tokens") or 0) \
                or compress_info["tokens_after"]
            actual_total = int(usage.get("total_tokens") or 0) \
                or (tokens_in_actual + tokens_out)
            # TPM уточняем по факту: до вызова был резерв-оценка.
            self.limiter.reconcile(client.name, reserved=tokens_in,
                                   actual=actual_total)

            self.usage.record(client.name, tokens_in=tokens_in_actual,
                              tokens_out=tokens_out, latency=latency,
                              compressed=compress_info["compressed"])

            upstream["gateway"] = self._gateway_block(
                client, model, compress_info, tokens_in, tokens_out,
                latency, request_host, request_port, upstream)
            upstream.setdefault("model", model)
            return upstream

        except GatewayError as exc:
            # Отказы тоже учитываются — иначе лимиты невидимы.
            self.usage.record_rejection(client_name, code=exc.code,
                                        message=exc.message, status=exc.status)
            raise

    def _gateway_block(self, client: GatewayClient, model: str,
                       compress_info: dict, tokens_in: int, tokens_out: int,
                       latency: float, host: str | None,
                       port: int | None, upstream: dict | None = None) -> dict:
        """Блок `gateway` в ответе: что шлюз сделал с запросом."""
        block = {
            "client": client.name,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_after_compression": compress_info["tokens_after"],
            "tokens_before_compression": compress_info["tokens_before"],
            "max_context": client.max_context,
            "compressed": compress_info["compressed"],
            "compress_rounds": compress_info["rounds"],
            "messages_before": compress_info["messages_before"],
            "messages_after": compress_info["messages_after"],
            "compress_error": compress_info.get("error"),
            "latency_sec": round(latency, 3),
            "limits": {"rpm": client.rpm, "tpm": client.tpm},
            "usage": self.limiter.snapshot(client.name),
        }
        # Пустой ответ — отдельная наблюдаемая ситуация, а не «норма».
        # Reasoning-модели (glm-4.7-flash) сначала «думают»: при малом
        # max_output_tokens бюджет кончается до текста, и клиент получает
        # пустоту с finish_reason=length. Молчать об этом нельзя.
        if upstream is not None:
            choices = upstream.get("choices") or []
            content = ""
            finish = None
            if choices:
                content = _content_text(
                    (choices[0].get("message") or {}).get("content")).strip()
                finish = choices[0].get("finish_reason")
            if not content:
                block["empty_response"] = True
                block["empty_reason"] = (
                    "Модель израсходовала бюджет ответа на рассуждения "
                    "(finish_reason=length). Увеличьте max_output_tokens "
                    f"клиента «{client.name}» (сейчас "
                    f"{client.max_output_tokens or '∞'}) или выберите модель "
                    "без режима рассуждений."
                    if finish == "length" else
                    "Модель вернула пустой ответ.")
                block["finish_reason"] = finish
            # Модель могла отдать текст в поле рассуждений (OpenAI-путь
            # некоторых reasoning-моделей): сообщаем, что он непустой.
            msg = (choices[0].get("message") or {}) if choices else {}
            if not content and msg.get("reasoning"):
                block["reasoning_chars"] = len(str(msg.get("reasoning")))
        return block

    def _build_upstream_body(self, body: dict, messages: list[dict],
                             model: str, client: GatewayClient) -> dict:
        """Тело запроса к Ollama: чистим клиентские поля, ставим потолок.

        `max_tokens` клиента уважается, но не может превысить
        `max_output_tokens` клиента — иначе один запрос займёт GPU
        надолго и обойдёт лимит.
        """
        upstream: dict[str, Any] = {}
        # Пробрасываем только осмысленные для Ollama поля.
        for key in ("temperature", "top_p", "seed", "stop", "tools",
                    "tool_choice", "response_format", "presence_penalty",
                    "frequency_penalty", "chat_template_kwargs"):
            if key in body and body[key] is not None:
                upstream[key] = body[key]

        requested = body.get("max_tokens")
        if requested is None:
            requested = body.get("max_completion_tokens")
        try:
            requested_int = int(requested) if requested is not None else None
        except (TypeError, ValueError):
            requested_int = None

        if client.max_output_tokens:
            upstream["max_tokens"] = (min(requested_int,
                                          client.max_output_tokens)
                                      if requested_int
                                      else client.max_output_tokens)
        elif requested_int:
            upstream["max_tokens"] = requested_int

        upstream["model"] = model
        upstream["messages"] = messages
        upstream["stream"] = False
        return upstream

    def _apply_context_limit(self, client: GatewayClient,
                             messages: list[dict],
                             model: str) -> tuple[list[dict], dict]:
        """Применить max_context: пройти как есть или сжать сводкой.

        Если и после компрессии вход не влезает — 413 с точными
        числами: молчаливой обрезки не бывает.
        """
        before = estimate_messages_tokens(messages)
        limit = client.max_context
        noop = {"compressed": False, "rounds": 0,
                "messages_before": len(messages),
                "messages_after": len(messages),
                "tokens_before": before, "tokens_after": before,
                "error": None}
        if not limit or before <= limit:
            return messages, noop

        def summarize(text: str) -> str:
            return self._summarize(text, model, client)

        compressed, info = compress_messages(
            messages, max_context=limit, summarize=summarize,
            keep_recent=self.keep_recent)

        if info["tokens_after"] > limit:
            hint = ""
            if not info["compressed"]:
                hint = (" (компрессия не удалась: "
                        f"{info.get('error') or 'недостаточно сообщений'})")
            raise GatewayError(
                f"Контекст превышает лимит: {info['tokens_after']} > "
                f"{limit} токенов{hint}. Сократите историю или увеличьте "
                f"max_context клиента «{client.name}».",
                status=413, code="context_length_exceeded")
        return compressed, info

    def _summary_models(self, model: str) -> list[str]:
        """Кандидаты моделей для сводки, лучший — первым.

        Сводка — служебная операция, и не всякая локальная модель её
        тянет. Проверено на живом Ollama (M3 Max, 48 ГБ):

        * `glm-4.7-flash:q4_K_M` (MoE + reasoning) на большом контексте
          либо возвращает пустой `content` (весь ответ уходит в
          `reasoning`), либо падает HTTP 500 — бюджет тратится на
          размышления, до текста сводки дело не доходит;
        * `qwen3-coder:30b` отдаёт готовую сводку сразу (743 символа на
          том же входе).

        Поэтому по умолчанию предпочитаем модель, выбранную для диалога
        (она уже прогрета в VRAM — это дешевле всего), а затем —
        «рабочие» модели без reasoning из доступных. Заданная клиентом
        модель всегда первая: подменять её молча нельзя, это отражено в
        конфигурации.
        """
        chain = [model]
        for candidate in self.summary_preference:
            if candidate not in chain and candidate in self.available_models():
                chain.append(candidate)
        # Остальные локальные модели — последним шансом (кроме embedding-овых).
        for candidate in self.available_models():
            if candidate in chain or "embed" in candidate:
                continue
            chain.append(candidate)
        return chain

    def _summarize(self, text: str, model: str,
                   client: GatewayClient) -> str:
        """LLM-сводка старых ходов: цепочка моделей × два пути вызова.

        Итог — первый непустой ответ. Перебираются:

        1. модели-кандидаты (`_summary_models`), потому что не каждая
           локальная модель умеет сводку (reasoning-модели тратят бюджет
           на размышления и отдают пустой `content`);
        2. два пути вызова: OpenAI-совместимый `/v1/chat/completions` и
           нативный `/api/chat`, где `content` и `thinking` разделены.

        Только если всё пусто — ошибка; компрессия деградирует, и
        вызывающий код честно отдаёт 413 (а не молча теряет историю).
        """
        errors: list[str] = []
        for candidate in self._summary_models(model):
            for fn, body in self._summary_attempts(candidate, text):
                try:
                    resp = fn(body)
                except Exception as exc:  # noqa: BLE001 — пробуем дальше
                    errors.append(f"{candidate}: {exc}")
                    continue
                content = _first_content(resp, key=body.get("_key", "choices"))
                if content:
                    return content
                errors.append(f"{candidate}: пустой content")
        raise RuntimeError("пустая сводка (" + "; ".join(errors[:3]) + ")")

    def _summary_attempts(self, model: str, text: str):
        """Пары (вызов, тело) для сводки: OpenAI-путь, затем нативный."""
        messages = [{"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": text}]
        yield (lambda b: self.summary_transport.chat(b, None, None),
               {"model": model, "temperature": 0.2, "max_tokens": 512,
                "messages": messages, "_key": "choices"})
        native = getattr(self.summary_transport, "native_chat", None)
        if native is not None:
            # num_predict больше: reasoning-моделям нужен запас, иначе
            # весь бюджет уходит на размышления и content пуст.
            yield (lambda b: native({k: v for k, v in b.items()
                                     if k != "_key"}),
                   {"model": model, "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 1024},
                    "messages": messages, "_key": "message"})

    # --- стриминг --------------------------------------------------------

    def prepare(self, body: dict, *, auth_header: str | None
                ) -> "PreparedStream":
        """Прогнать конвейер шлюза ДО начала стрима.

        Аутентификация, лимиты и компрессия выполняются здесь, поэтому
        ошибки (401/403/413/429) отдаются обычным JSON-ответом — клиент
        видит причину, а не оборванный SSE-поток.
        """
        auth_client: GatewayClient | None = None
        try:
            auth_client = self.authenticate(auth_header)
            client, model, messages, upstream, tokens_in, compress_info = \
                self._authorize(body, auth_header, client=auth_client)
        except GatewayError as exc:
            # Отказы на входе в стрим учитываются так же, как в обычном
            # пути: иначе лимиты обходились бы через stream: true.
            self.usage.record_rejection(
                auth_client.name if auth_client else "",
                code=exc.code, message=exc.message, status=exc.status)
            raise
        upstream["stream"] = True
        # include_usage: последний кадр приносит фактические токены —
        # без него стриминговый запрос нельзя честно учесть по TPM.
        upstream["stream_options"] = {"include_usage": True}
        return PreparedStream(service=self, client=client, model=model,
                              body=upstream, tokens_in=tokens_in,
                              compress_info=compress_info)

    def stream(self, prepared: "PreparedStream"):
        """Проксировать SSE от Ollama, попутно считая токены.

        Кадры пересылаются как есть (клиент получает ровно то, что
        отдала Ollama). Из финального кадра с `usage` берутся
        фактические токены: они идут в учёт, а TPM уточняется по факту.
        """
        started = time.monotonic()
        tokens_out = 0
        usage: dict = {}
        try:
            resp, client = self.transport.stream(prepared.body, None, None)
        except Exception as exc:  # noqa: BLE001 — 502 в тело потока
            self.limiter.refund(prepared.client.name, tokens=prepared.tokens_in)
            self.usage.record_rejection(prepared.client.name,
                                        code="upstream_error",
                                        message=str(exc), status=502)
            # Поток уже начался (роут не может вернуть JSON) — отдаём
            # ошибку в формате SSE, чтобы клиент её распознал.
            yield ("data: " + json.dumps(
                {"error": {"message": f"Локальная модель недоступна: {exc}",
                           "type": "upstream_error",
                           "code": "upstream_error"}}, ensure_ascii=False)
                + "\n\n").encode("utf-8")
            yield b"data: [DONE]\n\n"
            return

        try:
            for raw in resp.iter_lines():
                line = (raw or "").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
                    if payload and payload != "[DONE]":
                        try:
                            chunk = json.loads(payload)
                        except ValueError:
                            chunk = None
                        if isinstance(chunk, dict):
                            u = chunk.get("usage")
                            if isinstance(u, dict) and u:
                                usage = u
                            # Некоторые провайдеры не шлют usage: считаем
                            # дельты сами, чтобы учёт не остался пустым.
                            if not usage:
                                for ch in (chunk.get("choices") or []):
                                    delta = (ch.get("delta") or {})
                                    tokens_out += estimate_tokens(
                                        _content_text(delta.get("content")))
                yield (line + "\n\n").encode("utf-8")
        finally:
            try:
                resp.close()
            finally:
                client.close()

        latency = time.monotonic() - started
        tokens_in_actual = int(usage.get("prompt_tokens") or 0) \
            or prepared.compress_info["tokens_after"]
        tokens_out_actual = int(usage.get("completion_tokens") or 0) \
            or tokens_out
        actual_total = int(usage.get("total_tokens") or 0) \
            or (tokens_in_actual + tokens_out_actual)
        self.limiter.reconcile(prepared.client.name,
                               reserved=prepared.tokens_in,
                               actual=actual_total)
        self.usage.record(prepared.client.name, tokens_in=tokens_in_actual,
                          tokens_out=tokens_out_actual, latency=latency,
                          compressed=prepared.compress_info["compressed"])


@dataclass
class PreparedStream:
    """Результат `prepare()`: запрос прошёл конвейер, готов к стриму.

    Держит всё, что нужно для учёта после завершения потока, — сам поток
    отдаётся генератором `stream()`.
    """

    service: GatewayService
    client: GatewayClient
    model: str
    body: dict
    tokens_in: int
    compress_info: dict

    @property
    def headers(self) -> dict:
        """Заголовки SSE-ответа: видно, сжал ли шлюз контекст."""
        return {
            "x-gateway-client": self.client.name,
            "x-gateway-compressed": ("true" if self.compress_info["compressed"]
                                     else "false"),
            "x-gateway-max-context": str(self.client.max_context),
        }
