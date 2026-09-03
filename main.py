"""Day1: interactive LLM chat — CLI REPL (default) or Web chat (`main.py web`)."""
import http.server
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL_ENV = "GPUSTACK_BASE_URL"  # endpoint kept out of source (public repo)
MODEL = "qwen3.8-27b"  # exact id confirmed by Task 3 probe
KEY_ENV = "GPUSTACK_API_KEY"
TIMEOUT = 120
SYSTEM_PROMPT = "You are a helpful assistant. Always respond in English."  # force answer language

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Day1 Chat</title>
<style>
  body { font-family: sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }
  #log { border: 1px solid #ccc; padding: 1rem; min-height: 12rem; margin-bottom: 1rem; white-space: pre-wrap; }
  .user { color: #036; margin: .25rem 0; } .assistant { color: #060; margin: .25rem 0; }
  #row { display: flex; gap: .5rem; } textarea { flex: 1; }
</style>
</head>
<body>
<h1>Day1 Chat</h1>
<div id="log"></div>
<div id="row">
<textarea id="msg" rows="3" placeholder="Ваш вопрос..."></textarea>
<button id="send">Отправить</button>
</div>
<script>
const log = document.getElementById('log');
const msg = document.getElementById('msg');
function add(role, text) {
  const d = document.createElement('div');
  d.className = role;
  d.textContent = (role === 'user' ? 'Вы> ' : 'LLM> ') + text;
  log.appendChild(d);
}
async function send() {
  const message = msg.value.trim();
  if (!message) return;
  add('user', message);
  msg.value = '';
  try {
    const r = await fetch('/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message})
    });
    const j = await r.json();
    add('assistant', j.reply || ('Ошибка: ' + (j.error || r.status)));
  } catch (e) {
    add('assistant', 'Ошибка: ' + e);
  }
}
document.getElementById('send').onclick = send;
msg.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
</script>
</body>
</html>
"""

HISTORY = []  # server-side conversation history (web mode)


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def get_base_url():
    return os.environ[BASE_URL_ENV].strip().rstrip("/")


def send_messages(messages):
    """Send chat history to the LLM and return the reply text. Raises on any failure."""
    key = os.environ.get(KEY_ENV, "").strip()
    base = get_base_url()
    body = json.dumps({"model": MODEL, "messages": messages}).encode("utf-8")
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
        content = json.loads(raw)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"malformed response: {raw[:500]}") from None
    if not content:
        raise RuntimeError(f"malformed response: {raw[:500]}")
    return content


def run_cli():
    history = []
    print("Чат с LLM. Выход: exit / quit / Ctrl+C / Ctrl+D")
    while True:
        try:
            raw = input("Вы> ")
        except EOFError:
            print("Пока!")
            break
        except KeyboardInterrupt:
            print("Пока!")
            break
        raw = raw.strip()
        if raw.lower() in {"exit", "quit", "/exit", "/quit"}:
            break
        if not raw:
            continue
        history.append({"role": "user", "content": raw})
        try:
            reply = send_messages([{"role": "system", "content": SYSTEM_PROMPT}] + history)
        except Exception as e:
            print("Ошибка: " + str(e), file=sys.stderr)
            continue
        history.append({"role": "assistant", "content": reply})
        print(reply)


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/ask":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON body"})
            return
        message = payload.get("message") if isinstance(payload, dict) else None
        if not message:
            self._send_json(400, {"error": "missing 'message'"})
            return
        HISTORY.append({"role": "user", "content": message})
        try:
            reply = send_messages([{"role": "system", "content": SYSTEM_PROMPT}] + HISTORY)
        except Exception as e:
            self._send_json(502, {"error": str(e)})
            return
        HISTORY.append({"role": "assistant", "content": reply})
        self._send_json(200, {"reply": reply})


def run_web():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Чат: http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        fail(f"{KEY_ENV} is not set. Export it first, e.g. $env:{KEY_ENV}='...' (PowerShell)")
    if not os.environ.get(BASE_URL_ENV, "").strip():
        fail(f"{BASE_URL_ENV} is not set. Export it first, e.g. $env:{BASE_URL_ENV}='https://your-host/v1' (PowerShell)")
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        run_web()
    else:
        run_cli()


if __name__ == "__main__":
    main()
