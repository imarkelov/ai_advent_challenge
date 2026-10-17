# -*- coding: utf-8 -*-
"""Демонстрация дня 30: локальная LLM как сервис, доступный по локальной сети.

Поднимает uvicorn на 0.0.0.0:<port> (как в реальном развёртывании) и
показывает с другого «устройства» (отдельный HTTP-клиент по LAN-адресу):

  1. состояние сервиса и адрес для подключения;
  2. отказ анонимному запросу (401);
  3. создание клиента: ключ отдаётся один раз, на диске только SHA-256;
  4. реальный ответ локальной модели по LAN;
  5. rate limit (rpm) в действии;
  6. Ollama закрыта наружу — наружу смотрит только шлюз.

Запуск из корня репозитория:
    python scripts/demo_day30_service.py [--port 8112]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def req(path: str, base: str, body: dict | None = None, key: str | None = None,
        method: str | None = None) -> tuple[int, dict]:
    # Метод выводится из наличия тела: GET с телом urllib отправил бы как
    # GET, и такой запрос попал бы в SPA-фолбэк вместо API (он принимает
    # только GET). Явный method= всегда главнее.
    if method is None:
        method = "POST" if body is not None else "GET"
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = "Bearer " + key
    r = urllib.request.Request(base + path, data=data, headers=headers,
                               method=method)
    def _parse(raw: bytes, status: int) -> dict:
        text = (raw or b"").decode("utf-8", "replace").strip()
        if not text:
            return {"_raw": "", "_status": status}
        try:
            return json.loads(text)
        except ValueError:
            return {"_raw": text[:400], "_status": status}

    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            return resp.status, _parse(resp.read(), resp.status)
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read(), e.code)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8112)
    args = ap.parse_args()
    port = args.port

    key = "sk-local-demo-key"
    env = os.environ.copy()
    env["GATEWAY_CLIENT_KEY"] = key
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--app-dir", os.path.join("studio", "backend"),
         "--host", "0.0.0.0", "--port", str(port)],
        cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(80):
            time.sleep(0.5)
            if port_open(port):
                break
        else:
            print("Сервер не поднялся")
            return 1

        ip = lan_ip()
        lan = f"http://{ip}:{port}"
        local = f"http://127.0.0.1:{port}"

        # Прогрев: первый /api/gateway/status опрашивает модели (зонд
        # доступности каждой) и может занять десятки секунд на холодную.
        # Прогреваем по loopback, чтобы демонстрация LAN была отзывчивой.
        try:
            req("/api/gateway/status", local)
        except Exception:  # noqa: BLE001 — прогрев не критичен
            pass
        print("=== Демонстрация: локальная LLM как сервис в локальной сети ===")
        print(f"LAN-адрес машины: {ip}, порт: {port}\n")

        print("1) Состояние сервиса (запрошено по LAN-адресу, как с другого устройства):")
        st, j = req("/api/gateway/status", lan)
        print(f"   GET {lan}/api/gateway/status -> {st}")
        print(f"   chat_url : {j['chat_url']}")
        print(f"   models   : {', '.join(j['models'])}")
        print(f"   TLS      : {j['tls']}")
        print(f"   note     : {j['note']}\n")
        model = next((m for m in j["models"] if "qwen3-coder" in m), j["models"][0])

        print("2) Анонимный запрос из сети (без ключа):")
        st, j2 = req("/v1/chat/completions", lan,
                     {"model": model, "messages": [{"role": "user", "content": "привет"}]})
        err = j2.get("error") or {}
        print(f"   -> HTTP {st}: {err.get('code', j2)}\n")

        print("3) Создание клиента (ключ отдаётся один раз):")
        st, created = req("/api/gateway/clients", local,
                          {"name": "phone-demo", "rpm": 3, "tpm": 20000,
                           "max_context": 4096}, key=key)
        ckey = created["key"]
        print(f"   -> HTTP {st}, key={ckey}")
        print(f"   key_masked={created['key_masked']}")
        st, again = req("/api/gateway/clients", local, {"name": "phone-demo"}, key=key)
        print(f"   повторный запрос -> key={again['key']} (повторно не отдаётся)\n")

        cfg_path = os.path.join(REPO, "studio", "data", "gateway.json")
        raw = open(cfg_path, encoding="utf-8").read()
        stored = [c for c in json.loads(raw)["clients"] if c["name"] == "phone-demo"][0]
        print("4) Что лежит на диске (ключ не хранится открытым текстом):")
        print(f"   key_hash             : {stored['key_hash']}")
        print(f"   ключ есть в файле?   : {ckey in raw}\n")

        print("5) Реальный запрос с другого устройства по LAN (реальная модель):")
        st, ans = req("/v1/chat/completions", lan,
                      {"model": model, "messages": [
                          {"role": "user", "content": "Ответь одним словом: работает?"}]},
                      key=ckey)
        print(f"   POST {lan}/v1/chat/completions -> HTTP {st}")
        print(f"   модель : {ans.get('model')}")
        print(f"   ответ  : {ans['choices'][0]['message']['content'][:120]!r}")
        g = ans["gateway"]
        print(f"   gateway: client={g['client']} latency={g['latency_sec']}s "
              f"tokens_out={g['tokens_out']}\n")

        print("6) Rate limit (rpm=3) на живом сервере:")
        for i in range(5):
            st, _ = req("/v1/chat/completions", lan,
                        {"model": model, "messages": [{"role": "user", "content": "привет"}]},
                        key=ckey)
            print(f"   запрос {i + 1} -> HTTP {st}")
        print()

        print("7) Ollama закрыта наружу — наружу смотрит только шлюз:")
        print(f"   {ip}:11434 доступен с LAN? {port_open(11434, ip)}  (ожидается False)")
        print(f"   {ip}:{port} доступен с LAN?  {port_open(port, ip)}  (шлюз)")

        req("/api/gateway/clients/phone-demo", local, key=key, method="DELETE")
        req("/api/gateway/reset", local, {"name": "phone-demo"}, key=key, method="POST")
        print("\nУборка выполнена.")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
