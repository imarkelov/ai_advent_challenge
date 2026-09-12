"""Простой агент с LLM-клиентом: одна модель, история в памяти.

История диалога хранится в памяти (self.history, cap 20 сообщений) —
теряется при рестарте сервера. Персистентности нет.

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

# content может быть None (reasoning-модель): бюджет ушёл в рассуждения
NO_TEXT_PLACEHOLDER = "(модель не дала текста — бюджет ушёл в рассуждения)"


class SimpleAgent:
    """Агент с одной LLM-моделью и историей диалога в памяти.

    Публичный метод один — ask(user_input): собирает messages из
    system-промпта + истории + нового вопроса, шлёт POST
    {base}/chat/completions и возвращает текст ответа модели (str).
    При неудаче — RuntimeError; история в этом случае не меняется.
    """

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT, model: str = "qwen3.8-27b"):
        self.system_prompt = system_prompt
        self.model = model
        self.history = []  # [{"role": ..., "content": ...}, ...]
        self._lock = threading.Lock()

    def ask(self, user_input: str) -> str:
        """Отправить вопрос модели с учётом истории; вернуть текст ответа.

        Ответ добавляется в историю только после успешного ответа.
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
                content = choice["message"]["content"]  # может быть None (reasoning-модель)
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                raise RuntimeError(f"malformed response: {raw[:500]}") from None

            if content is None:
                content = NO_TEXT_PLACEHOLDER

            # история растёт только после успешного ответа
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
            self.history = self.history[-HISTORY_CAP:]
            return content
