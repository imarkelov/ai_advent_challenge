#!/usr/bin/env python3
"""День 26: проверка локальной LLM (Ollama) — CLI и HTTP API.

Три запроса разной сложности:
  1. простой    — фактический вопрос в одно слово;
  2. средний    — генерация кода с требованием;
  3. сложный    — рассуждение/проектирование с ограничениями.

Запускает каждый запрос двумя путями:
  * CLI  — `ollama run <model> <prompt>`;
  * HTTP — POST <OLLAMA_BASE_URL>/chat/completions (OpenAI-совместимый).

Печатает ответы, время и токены; в конце — сводка. Код возврата 1,
если хотя бы один запрос не дал непустой ответ.

Запуск:
    python scripts/day26_local_llm_check.py [--model qwen3-coder:30b]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Три запроса разной сложности.
QUERIES = [
    {
        "level": "1/3 простой",
        "name": "Факт",
        "prompt": "Назови столицу Франции. Ответь одним словом.",
        "max_tokens": 512,
    },
    {
        "level": "2/3 средний",
        "name": "Код",
        "prompt": (
            "Напиши на Python функцию is_palindrome(s: str) -> bool, которая "
            "игнорирует регистр и пробелы. Верни только код функции."
        ),
        "max_tokens": 1024,
    },
    {
        "level": "3/3 сложный",
        "name": "Рассуждение",
        "prompt": (
            "В сервисе 3 реплики, каждая падает с вероятностью 1%. Сервис "
            "доступен, если жива хотя бы одна реплика. Посчитай доступность "
            "и объясни в 3 предложениях, что даст 4-я реплика."
        ),
        "max_tokens": 1536,
    },
]


def strip_ansi(text: str) -> str:
    """Убирает ANSI-последовательности (спиннер/перерисовка строк в CLI)."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\x1b":
            while i < len(text) and text[i] not in "mK":
                i += 1
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def run_cli(model: str, prompt: str) -> dict:
    """Запрос через CLI `ollama run` (CLI-путь из задания)."""
    if not shutil.which("ollama"):
        return {"ok": False, "text": "", "sec": 0.0, "error": "ollama не найден в PATH"}
    t0 = time.time()
    try:
        p = subprocess.run(["ollama", "run", model, prompt],
                           capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "text": "", "sec": time.time() - t0,
                "error": "таймаут 600 с"}
    sec = time.time() - t0
    text = strip_ansi(p.stdout).strip()
    if p.returncode != 0 and not text:
        return {"ok": False, "text": "", "sec": sec,
                "error": (p.stderr or "код возврата != 0").strip()[:300]}
    return {"ok": bool(text), "text": text, "sec": sec, "error": None}


def run_http(model: str, prompt: str, max_tokens: int) -> dict:
    """Запрос через HTTP API (OpenAI-совместимый /chat/completions)."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        OLLAMA_BASE_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ.get("OLLAMA_API_KEY", "ollama")})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            payload = json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
        return {"ok": False, "text": "", "sec": time.time() - t0,
                "error": str(e)[:300], "usage": {}, "finish": None}
    sec = time.time() - t0
    choice = (payload.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    return {"ok": bool(text), "text": text, "sec": sec, "error": None,
            "usage": payload.get("usage") or {},
            "finish": choice.get("finish_reason")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("DAY26_MODEL", "qwen3-coder:30b"))
    ap.add_argument("--http-only", action="store_true", help="пропустить CLI-проверку")
    args = ap.parse_args()

    print("=" * 78)
    print(f"День 26. Локальная LLM: модель {args.model}")
    print(f"HTTP API: {OLLAMA_BASE_URL}")
    print("=" * 78)

    failures = []
    for i, q in enumerate(QUERIES, 1):
        print(f"\n### Запрос {q['level']}: {q['name']}")
        print(f"Промпт: {q['prompt'][:100]}{'...' if len(q['prompt']) > 100 else ''}")

        if not args.http_only:
            cli = run_cli(args.model, q["prompt"])
            status = "OK" if cli["ok"] else "FAIL"
            print(f"\n  [CLI ] {status} ({cli['sec']:.1f} с)")
            if cli["ok"]:
                print("  " + "\n  ".join(cli["text"].splitlines()[:14]))
            else:
                print(f"  ошибка: {cli['error']}")
                failures.append(f"CLI {q['name']}: {cli['error']}")
            print()

        http = run_http(args.model, q["prompt"], q["max_tokens"])
        u = http.get("usage") or {}
        status = "OK" if http["ok"] else "FAIL"
        print(f"  [HTTP] {status} ({http['sec']:.1f} с, "
              f"prompt={u.get('prompt_tokens', '?')}, "
              f"completion={u.get('completion_tokens', '?')}, "
              f"finish={http.get('finish')})")
        if http["ok"]:
            print("  " + "\n  ".join(http["text"].splitlines()[:14]))
        else:
            print(f"  ошибка: {http['error']}")
            failures.append(f"HTTP {q['name']}: {http['error']}")

    print("\n" + "=" * 78)
    if failures:
        print(f"ИТОГ: ПРОВАЛ ({len(failures)} из {len(QUERIES)} запросов)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"ИТОГ: УСПЕХ — все {len(QUERIES)} запроса разной сложности "
          f"получили непустой ответ (CLI и HTTP API)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
