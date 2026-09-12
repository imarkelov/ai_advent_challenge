"""Простой агент с LLM-клиентом: одна модель, история на диске.

История диалога (cap 20 сообщений) хранится в JSON-файле (по умолчанию
history.json рядом со скриптом), загружается при создании агента и
атомарно записывается после каждого успешного ответа — после перезапуска
сервера диалог продолжается.

Настройки (system_prompt/model/temperature/max_tokens/reasoning) тоже хранятся
в памяти агента и сбрасываются к значениям по умолчанию при рестарте сервера.

Клиентские настройки (базовый URL и ключи) читаются из переменных окружения
лениво, в момент каждого запроса:
  GPUSTACK_BASE_URL — базовый URL API (обязательна);
  GPUSTACK_API_KEY / GPUSTACK_KEY_DEEPSEEK / GPUSTACK_KEY_GLM — ключ,
  выбирается по модели (см. MODEL_KEY_ENV).

Ошибки — только исключения RuntimeError (в production-коде нет print).
"""
import json
import os
import threading
import urllib.error
import urllib.request

DEFAULT_SYSTEM_PROMPT = "Ты — простой полезный ассистент. Отвечай на русском языке."

# Модель → env-переменная с API-ключом
MODEL_KEY_ENV = {
    "qwen3.8-27b": "GPUSTACK_API_KEY",
    "deepseek-v4-flash": "GPUSTACK_KEY_DEEPSEEK",
    "glm-5.3-flash": "GPUSTACK_KEY_GLM",
}

TIMEOUT = 300  # неконтролируемая генерация может занимать >120 с
HISTORY_CAP = 20  # держим последние N сообщений истории

# история на диске: по умолчанию рядом со скриптом
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# content может быть None (reasoning-модель): бюджет ушёл в рассуждения
NO_TEXT_PLACEHOLDER = "(модель не дала текста — бюджет ушёл в рассуждения)"

# маркер "не менять" для configure()
UNSET = object()

# Лимит контекста моделей (токены). Источник — спецификации моделей;
# GPustack context length не отдаёт. None → UI показывает «—».
CONTEXT_LIMITS = {
    "qwen3.8-27b": 32768,
    "deepseek-v4-flash": 128000,
    "glm-5.3-flash": 128000,
}


def list_models() -> list:
    """Доступные чат-модели + лимит контекста: [{"id": ..., "context_limit": N|null}, ...]."""
    return [
        {"id": m, "context_limit": CONTEXT_LIMITS.get(m)}
        for m in MODEL_KEY_ENV
    ]


class SimpleAgent:
    """Агент с одной LLM-моделью и историей диалога на диске (JSON-файл).

    Публичный API:
      ask(user_input) -> dict — собирает messages из system-промпта + истории
      + нового вопроса, шлёт POST {base}/chat/completions и возвращает
      {"reply": str, "reasoning": str|None, "usage": {...}} (см. ask()).
      При неудаче — RuntimeError; история не меняется.
      configure(...) — атомарно меняет настройки (system_prompt, model,
      temperature, max_tokens, reasoning); аргумент по умолчанию (UNSET) =
      «не менять», None для temperature/max_tokens = сброс в None;
      валидация — RuntimeError, при неудаче настройки не меняются.
      get_config() -> dict — текущие настройки.
      get_history() -> list — копия текущей истории
      [{"role": ..., "content": ...}, ...].
      reset_history() — очистить историю и файл (новый диалог).
    """

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT, model: str = "qwen3.8-27b",
                 temperature=None, max_tokens=None, history_file=None, reasoning: bool = True):
        self.system_prompt = system_prompt
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning = reasoning  # включено ли рассуждение (thinking) модели
        self.history_file = history_file or HISTORY_FILE
        self._lock = threading.Lock()
        self.history = self._load_history()  # [{"role": ..., "content": ...}, ...]

    def configure(self, system_prompt=UNSET, model=UNSET, temperature=UNSET, max_tokens=UNSET,
                  reasoning=UNSET):
        """Атомарно изменить настройки: сначала валидация всех переданных
        (не-UNSET) значений, потом применение (все или ничего).

        Семантика аргументов:
          UNSET (значение по умолчанию) — параметр не менять;
          temperature / max_tokens: None — сбросить в None; иначе — валидное
          число (0 <= t <= 2) / положительный int;
          system_prompt / model: None — ошибка (агент обязан иметь промпт
          и известную модель);
          reasoning: строго bool (int не проходит) — включать/выключать
          рассуждение (thinking) модели.

        Ошибки валидации — RuntimeError; при неудаче настройки не меняются.
        """
        with self._lock:
            if system_prompt is not UNSET:
                if not isinstance(system_prompt, str) or not system_prompt.strip():
                    raise RuntimeError("system_prompt must be a non-empty string")
            if model is not UNSET:
                if not isinstance(model, str) or model not in MODEL_KEY_ENV:
                    raise RuntimeError(f"unknown model {model!r} (доступные: {', '.join(MODEL_KEY_ENV)})")
            if temperature is not UNSET and temperature is not None:
                if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) \
                        or not (0 <= temperature <= 2):
                    raise RuntimeError("temperature must be a number between 0 and 2")
            if max_tokens is not UNSET and max_tokens is not None:
                if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
                    raise RuntimeError("max_tokens must be a positive integer")
            if reasoning is not UNSET:
                # bool — подтип int, но здесь нужен именно bool (1/0 не проходят)
                if not isinstance(reasoning, bool):
                    raise RuntimeError("reasoning must be a boolean")

            if system_prompt is not UNSET:
                self.system_prompt = system_prompt
            if model is not UNSET:
                self.model = model
            if temperature is not UNSET:
                self.temperature = temperature
            if max_tokens is not UNSET:
                self.max_tokens = max_tokens
            if reasoning is not UNSET:
                self.reasoning = reasoning

    def get_config(self) -> dict:
        """Текущие настройки: system_prompt, model, temperature, max_tokens, reasoning."""
        with self._lock:
            return {
                "system_prompt": self.system_prompt,
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "reasoning": self.reasoning,
            }

    def _load_history(self) -> list:
        """Загрузить историю из файла. Файла нет / битый JSON / чужой формат — пустая."""
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        clean = [
            {"role": m["role"], "content": m["content"]}
            for m in data
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]
        return clean[-HISTORY_CAP:]

    def _save_history(self) -> None:
        """Атомарная запись истории: tmp-файл + os.replace (нет полубитого файла).

        Вызывать ТОЛЬКО под self._lock (в ask / reset_history).
        """
        tmp = self.history_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.history_file)

    def get_history(self) -> list:
        """Копия текущей истории: [{"role": ..., "content": ...}, ...]."""
        with self._lock:
            return [dict(m) for m in self.history]

    def reset_history(self) -> None:
        """Очистить историю (и файл) — новый диалог."""
        with self._lock:
            self.history = []
            self._save_history()

    def ask(self, user_input: str) -> dict:
        """Отправить вопрос модели с учётом истории; вернуть результат ответа.

        Возвращаемый dict:
          {"reply": str,                        # текст ответа (или плейсхолдер)
           "reasoning": str | None,             # текст рассуждения, если модель дала
           "usage": {
               "prompt_tokens": int | None,
               "completion_tokens": int | None,
               "total_tokens": int | None,
               "reasoning_tokens": int | None,  # None, если API не отдал detail
           }}

        Рассуждение включается в запрос через
        chat_template_kwargs: {"enable_thinking": bool(self.reasoning)}
        (проверено на всех трёх моделях; выключенный thinking — reasoning null).

        Ответ добавляется в историю только после успешного ответа —
        только content (user+assistant), reasoning в историю не попадает.
        Ошибки API/сети/разбора — RuntimeError с описанием.
        """
        with self._lock:
            messages = [{"role": "system", "content": self.system_prompt}]
            messages += self.history
            messages.append({"role": "user", "content": user_input})

            # env-переменные читаются лениво — в момент запроса
            base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
            key_env = MODEL_KEY_ENV.get(self.model)
            if key_env is None:
                raise RuntimeError(f"unknown model {self.model!r} (доступные: {', '.join(MODEL_KEY_ENV)})")
            key = os.environ.get(key_env, "").strip()
            if not key:
                raise RuntimeError(f"API key not set: env {key_env} required for model {self.model!r}")

            payload = {"model": self.model, "messages": messages}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            if self.max_tokens is not None:
                payload["max_tokens"] = self.max_tokens
            # оба состояния отправляются всегда: поведение проверено на 3 моделях
            payload["chat_template_kwargs"] = {"enable_thinking": bool(self.reasoning)}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"Network error calling {base}: {e.reason}") from e

            try:
                data = json.loads(raw)
                choice = data["choices"][0]
                message = choice["message"]
                content = message.get("content")  # может быть None (reasoning-модель)
                reasoning = message.get("reasoning")  # str или None — как в ответе
                usage = data.get("usage") or {}
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                raise RuntimeError(f"malformed response: {raw[:500]}") from None

            if content is None:
                content = NO_TEXT_PLACEHOLDER

            # completion_tokens_details может отсутствовать целиком (GLM)
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")

            # история растёт только после успешного ответа (только content)
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
            self.history = self.history[-HISTORY_CAP:]
            self._save_history()
            return {
                "reply": content,
                "reasoning": reasoning,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "reasoning_tokens": reasoning_tokens,
                },
            }
