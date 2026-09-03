"""Day2: the SAME question sent to the LLM at 4 progressive control levels.

Level 0: raw question            - no control
Level 1: + explicit format rule  - prompt engineering only
Level 2: + length limit          - max_tokens API parameter
Level 3: + stop sequence         - stop API parameter
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL_ENV = "GPUSTACK_BASE_URL"  # endpoint kept out of source (public repo)
KEY_ENV = "GPUSTACK_API_KEY"
MODEL = "qwen3.8-27b"  # exact id confirmed on Day 1
TIMEOUT = 120

QUESTION = "Расскажи подробно об истории вычислительной техники: от абака до нейросетей."
FORMAT_RULE = "Ответь строго в формате: Ответ: <твой ответ в одну строку>."


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def get_base_url():
    return os.environ[BASE_URL_ENV].strip().rstrip("/")


def ask(messages, max_tokens=None, stop=None):
    """Send one chat request and return (content, usage). Raises RuntimeError on any failure."""
    key = os.environ.get(KEY_ENV, "").strip()
    base = get_base_url()
    payload = {"model": MODEL, "messages": messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if stop is not None:
        payload["stop"] = stop
    body = json.dumps(payload).encode("utf-8")
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
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"malformed response: {raw[:500]}") from None
    if not content:
        raise RuntimeError(f"malformed response: {raw[:500]}")
    return content, data.get("usage")


LEVELS = [
    {
        "name": "no control",
        "messages": lambda: [{"role": "user", "content": QUESTION}],
        "params": {},
    },
    {
        "name": "+ explicit format",
        "messages": lambda: [{"role": "user", "content": QUESTION + "\n" + FORMAT_RULE}],
        "params": {},
    },
    {
        "name": "+ format + length limit",
        "messages": lambda: [{"role": "user", "content": QUESTION + "\n" + FORMAT_RULE + "\nДлина ответа: не более 30 слов."}],
        "params": {"max_tokens": 40},
    },
    {
        "name": "+ format + stop sequence",
        "messages": lambda: [{"role": "user", "content": QUESTION + "\n" + FORMAT_RULE + "\nЗаверши ответ маркером [STOP]."}],
        "params": {"stop": ["[STOP]"]},
    },
]


def run_demo():
    print("Day 2 — уровни контроля ответа LLM")
    print()
    for i, level in enumerate(LEVELS, 1):
        print(f"=== {i}. {level['name']} ===")
        try:
            content, usage = ask(level["messages"](), **level["params"])
            print(f"Длина: {len(content)} символов")
            if usage:
                print(f"Токенов: {usage.get('completion_tokens')}")
            preview = content[:160].replace("\n", " ⏎ ")
            print(f"Превью: {preview}")
        except Exception as e:
            print(f"Ошибка: {e}")
        if i < len(LEVELS):
            time.sleep(1)
        print()
    print("Готово.")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not os.environ.get(BASE_URL_ENV, "").strip():
        fail(f"{BASE_URL_ENV} is not set. Export it first, e.g. $env:{BASE_URL_ENV}='https://your-host/v1' (PowerShell)")
    if not os.environ.get(KEY_ENV, "").strip():
        fail(f"{KEY_ENV} is not set. Export it first, e.g. $env:{KEY_ENV}='...' (PowerShell)")
    run_demo()


if __name__ == "__main__":
    main()
