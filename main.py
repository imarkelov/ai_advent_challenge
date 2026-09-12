"""Day8: локальный веб-сервер с SimpleAgent.

Браузер (index.html) -> этот сервер (127.0.0.1:8000) -> SimpleAgent -> GPustack.

Маршруты:
  GET    /                 — страница
  GET    /agent/config     — текущие настройки агента
  GET    /agent/models     — список доступных моделей + лимит контекста
  POST   /agent/ask        — {"message": "..."} -> {"reply", "reasoning", "usage"}
  POST   /agent/config     — применить настройки (включая reasoning: bool)
   GET    /agent/history    — история активного диалога (переживает перезапуск)
   DELETE /agent/history    — сброс активного диалога
     POST   /agent/dialogues  — закрыть текущий (архив) и открыть новый -> {"id"}
     GET    /agent/dialogues  — список диалогов (active_id + сводка)
     GET    /agent/dialogues/{id} — сообщения диалога (для просмотра)
     POST   /agent/dialogues/{id}/activate — открыть диалог для продолжения
              (он становится активным, предыдущий закрывается) -> {"id"};
              неизвестный id -> 404, повторный activate -> 200 (idempotent)
   GET    /agent/last-request — JSON последнего запроса к LLM ({"request": ...|null})
   GET    /agent/tokens       — подсчёт токенов (вся история + последний ответ)

Запуск:  python main.py
Открыть: http://127.0.0.1:8000
"""
import http.server
import json
import os
import sys

from agent import SimpleAgent, list_models

HOST = "127.0.0.1"  # только loopback: сервер не добавляет свою авторизацию
PORT = 8000
MAX_MESSAGE_LEN = 4000

BASE_URL_ENV = "GPUSTACK_BASE_URL"  # endpoint kept out of source (public repo)
KEY_ENV = "GPUSTACK_API_KEY"

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")
DOT_ENV = os.path.join(HERE, ".env")

# Агент создаётся в main() после load_dotenv() и fail-fast проверки env.
AGENT = None


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def load_dotenv():
    """Загрузить переменные из .env рядом со скриптом (реальные env имеют приоритет)."""
    if not os.path.exists(DOT_ENV):
        return
    with open(DOT_ENV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Day8Agent/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        if code != 204:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        if code != 204:
            self.wfile.write(body)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        # Preflight-запрос браузера
        self._send(204, b"")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            try:
                with open(INDEX_HTML, "rb") as f:
                    body = f.read()
            except OSError as e:
                self._send_json(500, {"error": f"index.html not found: {e}"})
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path == "/agent/config":
            self._send_json(200, AGENT.get_config())
            return
        if self.path == "/agent/models":
            # Список моделей статичен (MODEL_KEY_ENV) — агент не нужен.
            self._send_json(200, {"models": list_models()})
            return
        if self.path == "/agent/history":
            self._send_json(200, {"messages": AGENT.get_history()})
            return
        if self.path.startswith("/agent/dialogues/"):
            # prefix-ветка (id) ставится ПЕРЕД точным "== /agent/dialogues":
            # точный путь без id не начинается с "/agent/dialogues/"
            dialogue_id = self.path[len("/agent/dialogues/"):].split("?", 1)[0].strip("/")
            messages = AGENT.get_dialogue_messages(dialogue_id)
            if messages is None:
                self._send_json(404, {"error": f"unknown dialogue {dialogue_id!r}"})
                return
            self._send_json(200, {"id": dialogue_id, "messages": messages})
            return
        if self.path == "/agent/dialogues":
            self._send_json(200, AGENT.get_dialogues())
            return
        if self.path == "/agent/last-request":
            # request может быть null (ask ещё не было)
            self._send_json(200, {"request": AGENT.get_last_request()})
            return
        if self.path == "/agent/tokens":
            # Эвристический подсчёт токенов (agent.count_tokens):
            # вся история активного диалога + последний ответ модели.
            self._send_json(200, AGENT.get_token_stats())
            return
        self._send_json(404, {"error": "not found", "hint": "GET / serves the page"})

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")

        if path == "/agent/ask":
            # Читает body и валидирует {"message": "<str>"} до обращения к агенту.
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
            except (ValueError, OSError):
                self._send_json(400, {"error": "invalid request body"})
                return
            try:
                data = json.loads(raw)
            except ValueError:
                self._send_json(400, {"error": "invalid JSON"})
                return
            message = data.get("message") if isinstance(data, dict) else None
            if not isinstance(message, str) or not message.strip():
                self._send_json(400, {"error": "message required"})
                return
            if len(message) > MAX_MESSAGE_LEN:
                self._send_json(400, {"error": "message too long"})
                return
            # RuntimeError — любой сбой LLM/сети в SimpleAgent (контракт agent.py).
            try:
                result = AGENT.ask(message)
            except RuntimeError as e:
                self._send_json(502, {"error": str(e)})
                return
            # ask() возвращает dict: reply (str) + reasoning (str|None) + usage (dict).
            self._send_json(200, {
                "reply": result["reply"],
                "reasoning": result["reasoning"],
                "usage": result["usage"],
            })
            return

        if path == "/agent/config":
            # Разбор и типизация body, как в /agent/ask; значения применяются
            # атомарно через agent.configure (валидация — RuntimeError -> 400).
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
            except (ValueError, OSError):
                self._send_json(400, {"error": "invalid request body"})
                return
            try:
                data = json.loads(raw)
            except ValueError:
                self._send_json(400, {"error": "invalid JSON"})
                return
            if not isinstance(data, dict):
                self._send_json(400, {"error": "body must be a JSON object"})
                return
            known = {}
            if "system_prompt" in data:
                if not isinstance(data["system_prompt"], str):
                    self._send_json(400, {"error": "system_prompt must be a string"})
                    return
                known["system_prompt"] = data["system_prompt"]
            if "model" in data:
                if not isinstance(data["model"], str):
                    self._send_json(400, {"error": "model must be a string"})
                    return
                known["model"] = data["model"]
            # null для temperature/max_tokens = «сбросить в None» (UI шлёт все 4
            # ключа, пустое поле → null); absent = «не менять».
            if "temperature" in data:
                t = data["temperature"]
                if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float))):
                    self._send_json(400, {"error": "temperature must be a number"})
                    return
                known["temperature"] = t
            if "max_tokens" in data:
                mt = data["max_tokens"]
                if mt is not None and (isinstance(mt, bool) or not isinstance(mt, int)):
                    self._send_json(400, {"error": "max_tokens must be an integer"})
                    return
                known["max_tokens"] = mt
            # reasoning: строго bool (true/false) — иначе 400; absent = «не менять».
            if "reasoning" in data:
                r = data["reasoning"]
                if not isinstance(r, bool):
                    self._send_json(400, {"error": "reasoning must be a boolean"})
                    return
                known["reasoning"] = r
            # Неизвестные ключи в body игнорируются.
            try:
                AGENT.configure(**known)
            except RuntimeError as e:
                self._send_json(400, {"error": str(e)})
                return
            self._send_json(200, {"ok": True})
            return

        # prefix-ветка ДО точной "/agent/dialogues" (без id она не попадает
        # в startswith+endswith, порядок безопасен в любом случае).
        if path.startswith("/agent/dialogues/") and path.endswith("/activate"):
            dialogue_id = path[len("/agent/dialogues/"):-len("/activate")].strip("/")
            result = AGENT.activate_dialogue(dialogue_id)
            if result is None:
                self._send_json(404, {"error": f"unknown dialogue {dialogue_id!r}"})
                return
            self._send_json(200, {"id": result})
            return

        if path == "/agent/dialogues":
            # Пустой body — не читаем (Content-Length может быть 0).
            self._send_json(200, {"id": AGENT.new_dialogue()})
            return

        # Прочие POST-пути — 404 (поведение day5)
        self._send_json(
            404,
            {"error": "not found", "hint": f"expected POST /agent/ask, got {path}"},
        )

    def do_DELETE(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/agent/history":
            AGENT.reset_history()
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"error": "not found"})


def main():
    global AGENT
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if not os.environ.get(KEY_ENV, "").strip():
        fail(f"{KEY_ENV} is not set in environment or .env")
    if not os.environ.get(BASE_URL_ENV, "").strip():
        fail(f"{BASE_URL_ENV} is not set in environment or .env")
    # Агент создаётся только после успешной fail-fast проверки env.
    AGENT = SimpleAgent()

    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"SimpleAgent server: http://{HOST}:{PORT}")
    print(f"Откройте в браузере: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
