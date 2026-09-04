"""Day2: три тумблера контроля ответа LLM через CLI — вопрос про щи.

Без аргументов — интерактивная сессия (тумблеры меняются между вопросами):

    python day2.py   # интерактивная сессия: /format /limit /stop меняют тумблеры между вопросами

Одиночный запрос: флаги собираются в ОДИН запрос (включение — указать флаг,
выключение — не указывать):

    python day2.py --format                             # формат: 10 пунктов (json_schema) + ≤300 символов
    python day2.py --format --limit                     # + лимит: ответ из одной строки
    python day2.py --format --stop                      # + завершение по фразе «я не ем щи!»
    python day2.py --format --limit --stop --show-json  # всё вместе + JSON запроса/ответа
    python day2.py -q "Как приготовить борщ?" --format  # свой вопрос

Тумблеры — уровни контроля ответа через API:
  --format → response_format: {"type": "json_schema", ...} — сервер принудительно
             возвращает массив items[10]: 10 пунктов (реальный API-контроль);
             суммарно ≤300 символов — через промт;
  --limit  → ограничение длины через промт: ответ из одной строки + описательное
             поле max_length в response_format (type="text");
  --stop   → промпт «Заверши ответ фразой: я не ем щи!.» + stop в response_format
             и топ-левел параметр stop (фактически останавливает генерацию).

Подсветка вывода: запрос — зелёным, ответ — жёлтым, метаданные — синим (ANSI).

GPUSTACK_BASE_URL и GPUSTACK_API_KEY задаются в .env рядом со скриптом
(или env-переменные — они имеют приоритет).

Модель (qwen3.8-27b) — REASONING-модель: в ответе отдельное поле `reasoning`
(цепочка рассуждений) рядом с `content` (финальный ответ, может быть null).
stop/max_tokens действуют на ВСЮ генерацию (reasoning + content): маленький
бюджет может целиком уйти в рассуждения и оставить content=null
(finish_reason="length"), а модель цитирует промпт внутри reasoning, поэтому
маркер stop из промпта может сработать раньше.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL_ENV = "GPUSTACK_BASE_URL"  # endpoint kept out of source (public repo)
KEY_ENV = "GPUSTACK_API_KEY"
MODEL = "qwen3.8-27b"  # exact id confirmed on Day 1
TIMEOUT = 300  # uncontrolled generation can exceed 120s

QUESTION = "Расскажи, как приготовить щи."
FORMAT_RULE = "Ответ: 10 пунктов, суммарно не более 300 символов."
LIMIT_RULE = "Ответ должен состоять из одной строки."
STOP_PHRASE = "я не ем щи!"

# Структурированный вывод: сервер принудительно возвращает массив items[10]
# (10 пунктов). Проверено живым запросом: type принимает только
# text|json_object|json_schema; json_schema с items и minItems/maxItems=10 работает.
ANSWER_SCHEMA = {
    "name": "answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string"}, "minItems": 10, "maxItems": 10}},
        "required": ["items"],
        "additionalProperties": False,
    },
}

# Подсветка вывода: запрос зелёным, ответ жёлтым, метаданные синим (ANSI, stdlib only)
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
RESET = "\033[0m"


def paint(text, color):
    """Оборачивает текст в ANSI-цвет."""
    return f"{color}{text}{RESET}"


def enable_colors():
    """Включает VT-обработку ANSI-кодов на консоли Windows; при пайпе — тихий fallback."""
    if os.name != "nt":
        return
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def get_base_url():
    return os.environ[BASE_URL_ENV].strip().rstrip("/")


def load_dotenv():
    """Читает KEY=VALUE из .env рядом со скриптом. Переменные, уже заданные в shell, имеют приоритет."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_response_format(fmt: bool, limit_: bool, stop_: bool) -> dict:
    """--format → настоящий json_schema (сервер отдаёт {"answer": "..."});
    иначе type="text" + описательные поля (API принимает и молча игнорирует их)."""
    if fmt:
        return {"type": "json_schema", "json_schema": ANSWER_SCHEMA}
    rf = {"type": "text"}
    if limit_:
        rf["max_length"] = "одна строка"
    if stop_:
        rf["stop"] = [STOP_PHRASE]
    return rf


def ask(messages, stop=None, response_format=None, chat_template_kwargs=None, show_json: bool = False):
    """Send one chat request and return (content, usage, finish_reason, reasoning_preview, stop_reason).
    content may be None (reasoning model spent the whole budget on reasoning).
    With show_json=True, prints the request payload and the raw API response
    (debug mode; the API key lives only in the Authorization header and is never printed).
    Raises RuntimeError on any failure."""
    key = os.environ.get(KEY_ENV, "").strip()
    base = get_base_url()
    payload = {"model": MODEL, "messages": messages, "response_format": response_format}
    if stop is not None:
        payload["stop"] = stop
    if chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = chat_template_kwargs
    body = json.dumps(payload).encode("utf-8")
    if show_json:
        print(paint("--- JSON запрос ---", GREEN))
        print(paint(json.dumps(payload, ensure_ascii=False, indent=2), GREEN))
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
        if show_json:
            print(f"--- JSON ответ (ошибка HTTP {e.code}) ---")
            print(detail)
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        if show_json:
            print("--- JSON ответ: нет ответа (сеть) ---")
        raise RuntimeError(f"Network error calling {base}: {e.reason}") from e

    if show_json:
        print(paint("--- JSON ответ ---", YELLOW))
        try:
            print(paint(json.dumps(json.loads(raw), ensure_ascii=False, indent=2), YELLOW))
        except json.JSONDecodeError:
            print(paint(raw, YELLOW))

    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        content = choice["message"]["content"]  # may be None for a reasoning model - valid
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        raise RuntimeError(f"malformed response: {raw[:500]}") from None
    finish_reason = choice.get("finish_reason")
    stop_reason = choice.get("stop_reason")
    reasoning_preview = (choice["message"].get("reasoning") or "")[:120]
    return content, data.get("usage"), finish_reason, reasoning_preview, stop_reason


def show_result(content, usage, finish_reason, stop_reason, reasoning_preview, full_answer=False):
    """Print one ask() result: the full answer (CLI mode) or a 160-char preview."""
    if content is None:
        print(paint("Контент: null — бюджет генерации ушёл в фазу рассуждений (reasoning)", YELLOW))
        if reasoning_preview:
            print(paint(f"reasoning (фрагмент): {reasoning_preview}", YELLOW))
        print(paint(f"finish_reason: {finish_reason}", BLUE))
        if stop_reason:
            print(paint(f"stop_reason: {stop_reason}", BLUE))
        return
    print(paint(f"Длина: {len(content)} символов", BLUE))
    if usage:
        print(paint(f"Токенов: {usage.get('completion_tokens')}", BLUE))
    print(paint(f"finish_reason: {finish_reason}", BLUE))
    if stop_reason:
        print(paint(f"stop_reason: {stop_reason}", BLUE))
    if full_answer:
        print("Ответ:")
        print(paint(content, YELLOW))
    else:
        preview = content[:160].replace("\n", " ⏎ ")
        print(f"Превью: {preview}")


EPILOG = """Интерактивная сессия (без аргументов):
  python day2.py
  внутри сессии: /format /limit /stop — вкл/выкл тумблер (статус печатается
  автоматически после каждого переключения); /exit — выход;
  любые комбинации применяются ко всем последующим вопросам сессии

Тумблеры контроля ответа (включение — указать флаг, выключение — не указывать):
  --format   10 пунктов (json_schema items[10], реальный API-контроль) +
             суммарно ≤300 символов (промт)
  --limit    ограничение длины через промт: ответ из одной строки
  --stop     условие завершения: stop-последовательность «я не ем щи!»
  (плюс: -q/--question — свой вопрос; --show-json — вывод JSON запроса/ответа API)
  Подсветка вывода: запрос — зелёным, ответ — жёлтым, метаданные — синим (ANSI).

Каждый тумблер — уровень контроля ответа через API: описание уходит в поле
response_format тела запроса, фактическое завершение обеспечивает параметр stop.

Примеры:
  python day2.py
  python day2.py --format
  python day2.py --format --limit
  python day2.py --format --stop
  python day2.py --format --limit --stop --show-json
  python day2.py -q "Как приготовить борщ?" --format
"""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="day2.py",
        description="Day 2: три тумблера контроля ответа LLM — формат, лимит длины, условие завершения.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-q", "--question", default=QUESTION, metavar="TEXT",
                        help="вопрос к модели (по умолчанию — вопрос про щи)")
    parser.add_argument("--format", action="store_true",
                        help="10 пунктов (response_format: json_schema items[10], реальный API-контроль) + суммарно ≤300 символов (промт) (выключение — не указывать)")
    parser.add_argument("--limit", action="store_true",
                        help="ограничение длины через промт: ответ из одной строки (выключение — не указывать)")
    parser.add_argument("--stop", action="store_true",
                        help="условие завершения ответа: stop-последовательность «я не ем щи!» (выключение — не указывать)")
    parser.add_argument("--show-json", action="store_true",
                        help="вывести JSON запроса и ответа API (отладка)")
    return parser.parse_args(argv)


def send_question(question, fmt, limit_, stop_, show_json):
    """Собирает и отправляет один запрос с текущими тумблерами; печатает результат."""
    user_msg = question
    controls = []
    if fmt:
        user_msg += "\n" + FORMAT_RULE
        controls.append("format")
    if limit_:
        user_msg += "\n" + LIMIT_RULE
        controls.append("limit")
    if stop_:
        user_msg += f"\nОтвет должен обязательно заканчиваться фразой: {STOP_PHRASE}"
        controls.append("stop")

    rf = build_response_format(fmt, limit_, stop_)

    print(paint("Запрос: " + user_msg, GREEN))
    print(paint("Контроль: " + (", ".join(controls) if controls else "нет (без ограничений)"), BLUE))
    print(paint("response_format: " + json.dumps(rf, ensure_ascii=False), BLUE))
    try:
        # только топ-левел stop работает: stop_sequence/stop_sequences сервер молча игнорирует (проверено live)
        stop = [STOP_PHRASE] if stop_ else None
        # reasoning-модель цитирует промпт в рассуждениях → stop-маркер срабатывает в
        # reasoning-фазе → пустой контент; отключаем thinking — live-проверено (t31b)
        ctk = {"enable_thinking": False} if stop_ else None
        start = time.perf_counter()
        content, usage, finish_reason, reasoning_preview, stop_reason = ask(
            [{"role": "user", "content": user_msg}], stop=stop, response_format=rf,
            chat_template_kwargs=ctk, show_json=show_json,
        )
        dt = time.perf_counter() - start
        print(paint(f"Время генерации: {dt:.2f} с", BLUE))
        show_result(content, usage, finish_reason, stop_reason, reasoning_preview, full_answer=True)
    except Exception as e:
        print(f"Ошибка: {e}")


def run_single(args):
    send_question(args.question, args.format, args.limit, args.stop, args.show_json)


def interactive_status(state):
    def mark(key):
        return "[x]" if state[key] else "[ ]"
    return (f"Тумблеры: {mark('format')} format  {mark('limit')} limit  "
            f"{mark('stop')} stop")


def run_interactive():
    state = {"format": False, "limit": False, "stop": False}
    print("Day 2 — интерактивная сессия: тумблеры меняются в рамках сессии")
    print("Команды: /format /limit /stop — вкл/выкл тумблер; /exit — выход")
    print(interactive_status(state))
    while True:
        try:
            line = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            cmd = line[1:].strip().lower()
            if cmd in ("format", "limit", "stop"):
                state[cmd] = not state[cmd]
                print(interactive_status(state))
            elif cmd in ("exit", "quit", "q"):
                break
            else:
                print(f"Неизвестная команда: /{cmd} (доступно: /format /limit /stop /exit)")
        else:
            send_question(line, state["format"], state["limit"], state["stop"], False)
    print("Сессия завершена.")


def main():
    enable_colors()
    args = parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    if not os.environ.get(BASE_URL_ENV, "").strip():
        fail(f"{BASE_URL_ENV} is not set. Set it in env or in the .env file next to the script.")
    if not os.environ.get(KEY_ENV, "").strip():
        fail(f"{KEY_ENV} is not set. Set it in env or in the .env file next to the script.")
    if len(sys.argv) == 1:
        run_interactive()
    else:
        run_single(args)


if __name__ == "__main__":
    main()
