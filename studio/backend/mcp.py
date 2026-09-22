"""MCP-клиент «Студии» (день 16): Model Context Protocol.

Два транспорта:
- stdio — локальный процесс (subprocess), JSON-RPC 2.0 строками
  (newline-delimited JSON) по stdin/stdout;
- http — удалённый streamable-http-сервер, JSON-RPC по httpx POST
  (ответ — application/json или SSE-стрим data: {json}).

Сценарий дня 16: подключение и перечисление инструментов (tools/list).
call_tool готов для будущего tool-loop; в тело LLM-запроса tools в этом
дне НЕ инжектятся.
"""
import json
import os
import queue
import re
import subprocess
import threading

import httpx

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_INFO = {"name": "studio", "version": "1.0"}

_ENV_REF = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MCPError(Exception):
    """Ошибка подключения/вызова MCP (RU-сообщение)."""


def _default_timeout() -> float:
    """Таймаут, сек (env MCP_CONNECT_TIMEOUT, default 60: холодный
    запуск npx-пакета может быть долгим)."""
    try:
        return max(5.0, float(os.environ.get("MCP_CONNECT_TIMEOUT", "60")))
    except ValueError:
        return 60.0


def _expand_env(text: str, env: dict) -> tuple:
    """Развернуть плейсхолдеры {VAR} из env; вернуть (текст, [missing])."""
    missing = []

    def _sub(m):
        v = env.get(m.group(1))
        if v is None:
            missing.append(m.group(1))
            return ""
        return str(v)

    return _ENV_REF.sub(_sub, text), missing


def _normalize_tools(raw) -> list:
    """tools из tools/list -> [{name, description, input_schema}];
    битые записи пропускаются, inputSchema/input_schema — оба формата."""
    out = []
    if not isinstance(raw, list):
        return out
    for t in raw:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        out.append({"name": str(t["name"]),
                    "description": str(t.get("description") or ""),
                    "input_schema": t.get("inputSchema")
                                    or t.get("input_schema") or {}})
    return out


def _default_launcher(command: list, env: dict):
    """Запустить stdio-сервер: stdout-пайп на строки, stderr — в никуда."""
    return subprocess.Popen(command, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, text=True, bufsize=1)


class _StdioSession:
    """Сессия MCP по stdio: reader-поток -> queue, JSON-RPC
    request/notify, ответ ищется по id запроса."""

    def __init__(self, proc, timeout: float):
        self._proc = proc
        self._timeout = timeout
        self._next_id = 0
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._read_loop,
                                        daemon=True)
        self._thread.start()

    def _read_loop(self):
        try:
            for line in self._proc.stdout:
                self._queue.put(line.strip())
        except Exception:
            pass
        self._queue.put(None)  # EOF

    def _write(self, msg: dict) -> None:
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def request(self, method: str, params: dict | None = None):
        """JSON-RPC-запрос; дождаться ответа по id (таймаут -> MCPError)."""
        with self._lock:
            self._next_id += 1
            msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
            if params is not None:
                msg["params"] = params
            self._write(msg)
            while True:
                try:
                    item = self._queue.get(timeout=self._timeout)
                except queue.Empty:
                    raise MCPError("Таймаут ответа MCP-сервера") from None
                if item is None:
                    raise MCPError("MCP-сервер закрыл соединение")
                if not item:
                    continue
                try:
                    data = json.loads(item)
                except ValueError:
                    continue
                if data.get("id") != self._next_id:
                    continue
                if isinstance(data.get("error"), dict):
                    raise MCPError(str(data["error"].get(
                        "message", data["error"])))
                return data.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        """JSON-RPC notification (без id, ответ не ждём)."""
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        if self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass


class _HttpSession:
    """Сессия MCP по streamable-http: POST JSON-RPC; ответ — JSON или
    SSE-стрим; заголовок Mcp-Session-Id (если сервер отдал)
    повторяется в следующих запросах."""

    def __init__(self, client, url: str, timeout: float):
        self._client = client
        self._url = url
        self._timeout = timeout
        self._session_id = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    @staticmethod
    def _result(data) -> object:
        if isinstance(data, list):  # SSE: несколько событий
            data = next((d for d in data if isinstance(d, dict)
                         and ("result" in d or "error" in d)), None)
            if data is None:
                raise MCPError("HTTP: не найден ответ JSON-RPC")
        if isinstance(data.get("error"), dict):
            raise MCPError(str(data["error"].get("message",
                                                  data["error"])))
        return data.get("result")

    def request(self, method: str, params: dict | None = None):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            msg["params"] = params
        r = self._client.post(self._url, json=msg, headers=self._headers(),
                              timeout=self._timeout)
        if r.status_code not in (200, 202):
            raise MCPError(f"MCP HTTP {r.status_code}")
        sid = r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        ctype = r.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("id") == 1:
                        return self._result(data)
            raise MCPError("HTTP: не найден ответ JSON-RPC в SSE")
        try:
            data = r.json()
        except ValueError:
            raise MCPError("HTTP: ответ не JSON") from None
        return self._result(data)

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        r = self._client.post(self._url, json=msg, headers=self._headers(),
                              timeout=self._timeout)
        if r.status_code >= 400:
            raise MCPError(f"MCP HTTP {r.status_code}")

    def close(self) -> None:
        pass  # httpx.Client закрывает владелец (MCPRegistry)


class MCPClient:
    """Клиент одного MCP-сервера (запись из реестра: {id?, name, type,
    command, url, env, enabled}). launcher/http_client инжектируются
    для офлайн-тестов."""

    def __init__(self, server: dict, launcher=None, http_client=None,
                 env: dict | None = None, timeout: float | None = None):
        self.server = dict(server)
        self._env = dict(env if env is not None else os.environ)
        self._launcher = launcher or _default_launcher
        self._http_client = http_client
        self._timeout = (timeout if timeout is not None
                         else _default_timeout())
        self._session = None
        self._owned_client = None  # httpx.Client, если создали сами

    def connect(self) -> list:
        """Подключиться: initialize -> notifications/initialized ->
        tools/list. Возвращает нормализованные инструменты."""
        if self.server.get("type") == "stdio":
            session = _StdioSession(self._launch(), self._timeout)
        else:
            if self._http_client is not None:
                client = self._http_client
            else:
                client = httpx.Client(timeout=self._timeout)
                self._owned_client = client
            session = _HttpSession(client, self.server.get("url", ""),
                                   self._timeout)
        try:
            session.request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": MCP_CLIENT_INFO})
            session.notify("notifications/initialized")
            result = session.request("tools/list", {}) or {}
        except (MCPError, OSError) as e:
            session.close()
            self._close_owned()
            raise MCPError(str(e)) from None
        self._session = session
        return _normalize_tools(result.get("tools"))

    def call_tool(self, name: str,
                  arguments: dict | None = None) -> dict:
        """tools/call (задел на tool-loop). MCPError если нет сессии."""
        if self._session is None:
            raise MCPError("Сервер не подключён: сначала connect()")
        result = self._session.request(
            "tools/call", {"name": name, "arguments": arguments or {}})
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
        self._close_owned()

    def _close_owned(self) -> None:
        if self._owned_client is not None:
            try:
                self._owned_client.close()
            except Exception:
                pass
            self._owned_client = None

    def _launch(self):
        """Собрать command (развернув {VAR}) и запустить процесс."""
        cmd = [str(a) for a in (self.server.get("command") or [])]
        if not cmd:
            raise MCPError("stdio-сервер без command")
        missing = []
        expanded = []
        for arg in cmd:
            a, m = _expand_env(arg, self._env)
            expanded.append(a)
            missing.extend(m)
        proc_env = dict(self._env)
        for k, v in (self.server.get("env") or {}).items():
            v2, m = _expand_env(str(v), self._env)
            proc_env[str(k)] = v2
            missing.extend(m)
        if missing:
            raise MCPError("Не заданы переменные окружения: "
                           + ", ".join(sorted(set(missing))))
        try:
            return self._launcher(expanded, proc_env)
        except OSError as e:
            raise MCPError(str(e)) from None
