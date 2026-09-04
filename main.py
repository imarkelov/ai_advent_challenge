"""Day3: локальный CORS-прокси.

Браузер (index.html) -> этот сервер (127.0.0.1:8000) -> GPustack (OpenAI-совместимый API).
CORS блокирует прямой запрос браузера к GPustack (preflight 405), поэтому запросы
идут через прокси: сервер-к-серверу CORS не существует.

Запуск:  python main.py
Открыть: http://127.0.0.1:8000
"""
import http.server
import json
import os
import sys
import urllib.error
import urllib.request

HOST = "127.0.0.1"  # только loopback: прокси не добавляет свою авторизацию
PORT = 8000
TIMEOUT = 120

BASE_URL_ENV = "GPUSTACK_BASE_URL"  # endpoint kept out of source (public repo)
KEY_ENV = "GPUSTACK_API_KEY"

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")
DOT_ENV = os.path.join(HERE, ".env")


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
    server_version = "Day3Proxy/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return

        key = os.environ.get(KEY_ENV, "").strip()
        base = os.environ[BASE_URL_ENV].strip().rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
        except (ValueError, OSError):
            self._send_json(400, {"error": "invalid request body"})
            return

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
                upstream = resp.read()
                upstream_type = resp.headers.get("Content-Type", "application/json")
                self._send(resp.status, upstream, upstream_type)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            self._send(e.code, detail.encode("utf-8"))
        except urllib.error.URLError as e:
            self._send_json(502, {"error": f"network error calling {base}: {e.reason}"})


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if not os.environ.get(KEY_ENV, "").strip():
        fail(f"{KEY_ENV} is not set in environment or .env")
    if not os.environ.get(BASE_URL_ENV, "").strip():
        fail(f"{BASE_URL_ENV} is not set in environment or .env")

    server = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"CORS-прокси: http://{HOST}:{PORT}  ->  {os.environ[BASE_URL_ENV].strip()}")
    print(f"Откройте в браузере: http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
