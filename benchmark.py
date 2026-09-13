"""Бенчмарк токенов при сжатии контекста (day9, задача 11).

CLI прогоняет ОДИН скриптованный длинный диалог дважды: секция
A — «сжатие ВЫКЛ» (compression_enabled=False, история растёт без
сводки), секция B — «сжатие ВКЛ» (скользящее окно 6 + LLM-сводка
каждые 4 сообщения). Каждая секция создаёт СВОЙ SimpleAgent на
СВОЁМ tmp-файле диалогов (репозиторный dialogues.json не трогается)
и собирает usage (prompt/completion/total) по каждому ходу из
реального usage-поля API-ответа (agent.ask() -> result["usage"]).

После обеих секций — LLM-judge (по умолчанию ВКЛ, --no-judge
отключает): по одному запросу на транскрипт (та же модель,
temperature=0, thinking выключен, RU-промпт), ответ разбором в JSON
{"score": 1-10, "verdict": str, "reason": str}; отчёты печатаются и
пишутся в judge.json. Sбой judge — WARNING, прогон не ломается.

Итоги: таблица «turn | prompt | completion | total» по каждой
секции + строка ИТОГО; в реальном режиме — проверка экономии
(sum prompt B < sum prompt A, >= 10% → OK, иначе WARNING;
--strict → exit 1).

Режим --mock: urllib.request.urlopen подменяется ДО создания
агентов на заглушку OpenAI-совместимого ответа. Сеть не
используется: prompt_tokens считается локально существующей
count_tokens() по текстам сообщений исходящего запроса (растёт
от хода к ходу — история диалога в запросе удлиняется),
completion_tokens фиксированы (40). Judge-запрос заглушка узнаёт
по маркеру в system-сообщении и отвечает детерминированным
JSON-отчётом. --mock — жёсткий офлайн/CI-гейт: структурные
ассерты по каноническим значениям (prompt-серии A/B, рост A,
ограниченность B, транскрипты на месте).

Использование:
  python benchmark.py --mock --out .omo/evidence/bench
  python benchmark.py --turns 22 --reasoning off --out .omo/evidence/bench-real
  python benchmark.py --turns 22 --strict          # exit 1 при экономии < 10%
  python benchmark.py --no-judge                   # без LLM-judge

Аргументы:
  --turns N       сколько первых скриптованных ходов прогонять (по
                  умолчанию 22 — весь диалог)
  --model M       модель агента (по умолчанию qwen3.8-27b)
  --reasoning     on/off — рассуждения модели (по умолчанию off)
  --mock          офлайн-заглушка API (без сети) + структурные ассерты
  --out DIR       каталог для transcript_a.json / transcript_b.json /
                  judge.json (по умолчанию .omo/evidence/bench)
  --strict        реальный режим: exit 1, если экономия prompt B < A
                  отсутствует или < 10%
  --no-judge      не вызывать LLM-judge
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import urllib.request

from agent import (
    SimpleAgent,
    count_tokens,
    MODEL_KEY_ENV,
    TIMEOUT,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_WINDOW_SIZE,
    DEFAULT_SUMMARY_GAP,
    HISTORY_CAP,
)

# 22 скриптованных хода: один связный диалог «планирование миграции
# проекта». Каждый ход опирается на контекст предыдущих.
SCRIPTED_TURNS = [
    "Начнём планирование миграции проекта: монолит на Python 3.8 в legacy-репозитории, 400k строк. С чего начать аудит зависимостей?",
    "Аудит выявил 34 зависимости, из них 7 не поддерживают Python 3.12. Как приоритизировать замену этих семи, учитывая, что от них зависят модули биллинга и авторизации?",
    "Приоритеты приняты: начинаем с библиотеки авторизации. Составь план её замены на современный OAuth2-клиент с сохранением API-контрактов.",
    "По авторизации договорились на oauthlib. Следующая в списке — ORM: как мигрировать с SQLAlchemy 1.3 на 2.0 без остановки сервиса, если у нас 900 моделей?",
    "План по ORM понятен. Теперь база данных: реплика MySQL 5.7 надо перевести на PostgreSQL. Предложи стратегию переноса схемы и данных, помни, что биллинг ходит к ней напрямую.",
    "Схему БД переносим через двойную запись. Какую схему двойной записи выбрать для биллинга, чтобы не потерять транзакции во время миграции?",
    "С двойной записью разобрались. Следующий блок — CI/CD: пайплайн на Jenkins 2.180 устарел. Как переехать на GitHub Actions, сохранив ночные прогоны нагрузочных тестов?",
    "Про CI договорились. Теперь тесты: покрытие 41%. Какой минимальный набор интеграционных тестов нужен, чтобы безопасно включить двойную запись биллинга?",
    "Набор тестов зафиксировали. Следующий риск — фоновые джобы на Celery 4. Как мигрировать воркеры на Celery 5 и новые очереди без простоя расписаний?",
    "По Celery план есть. Вернёмся к базе: как валидировать, что данные в PostgreSQL совпадают с MySQL во время двойной записи? Нужен инструмент сверки.",
    "Инструмент сверки будет на nightly-джобе из Actions. Теперь релизы: сейчас деплой по FTP на 12 машин. Предложи схему канареечных релизов при переезде на Docker.",
    "Канареечная схема принята: 1 из 12 машин. Как настроить откат канарейки, если после деплоя растёт 5xx в авторизации — помнишь, мы её переносили на oauthlib?",
    "Откат через health-check и автоматический вывод машины из балансировщика — ок. Следующий вопрос: мониторинг. Какие метрики собирать, чтобы миграцию было видно на графиках?",
    "Метрики зафиксировали: latency, 5xx, лаг двойной записи, размер очередей Celery. Теперь команда: 6 разработчиков, у двоих нет опыта с Docker. Как построить обучение без остановки миграции?",
    "Обучение — парные ревью и пилотная машина. Теперь сроки: у нас 12 недель до конца поддержки Python 3.8. Составь поэтапный план по неделям с учётом всего, что обсудили: зависимости, БД, CI, релизы.",
    "План на 12 недель хороший, но бюджет: у нас только 2 подрядчика на 3 месяца. Какие этапы плана отдать подрядчикам, а какие тащить штатной команде?",
    "Подрядчиков отдаём на перенос данных и Docker, штат — на код. Теперь документация: какие артефакты плана миграции нужно зафиксировать, чтобы новый человек вошёл за неделю?",
    "Документацию утвердили: ADR, runbook отката, карта зависимостей. Вернёмся к рискам: топ-3 риска плана и по плану митигации для каждого?",
    "Риски: лаг сверки, дедлайн Python 3.8, потеря транзакций биллинга — митигации прописаны. Теперь пилот: какие модули вынести на канарейку первыми и как измерять успех пилота?",
    "Пилот начнём с авторизации и каталога, критерии успеха — 0 потерь запросов и латенси +5%. Остался последний блок: как откатить весь план миграции целиком, если на неделе 8 что-то пойдёт фатально не так?",
    "Точка невозврата — неделя 6 (до переключения продовых данных), после неё только форвард-фикс. Зафиксируем это в ADR-0001. Что должно быть в чек-листе финального переключения на PostgreSQL?",
    "Чек-лист готов: заморозка записи, финальная сверка, переключение DNS, окно мониторинга 48 часов. Подведи итог: перечисли все ключевые решения нашего плана миграции одним списком.",
]

# фиксированный mock-ответ
MOCK_REPLY = "Это mock-ответ бенчмарка: сжатие и judge отработаны на детерминированной заглушке."
MOCK_COMPLETION_TOKENS = 40

# маркер judge-запроса (mock-заглушка по нему отличает judge от ask/сводки)
JUDGE_MARKER = "JUDGE-ОЦЕНКА-ДИАЛОГА"

DOT_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def build_parser() -> argparse.ArgumentParser:
    """Аргументы CLI: --turns, --model, --reasoning, --mock, --out,
    --strict, --no-judge."""
    parser = argparse.ArgumentParser(
        description="Бенчмарк токенов: скриптованный диалог, секции A (сжатие ВЫКЛ) / B (сжатие ВКЛ), LLM-judge.",
    )
    parser.add_argument("--turns", type=int, default=22,
                        help="сколько первых скриптованных ходов прогонять (по умолчанию 22)")
    parser.add_argument("--model", default="qwen3.8-27b",
                        help="модель агента (по умолчанию qwen3.8-27b)")
    parser.add_argument("--reasoning", choices=("on", "off"), default="off",
                        help="рассуждения модели on/off (по умолчанию off)")
    parser.add_argument("--mock", action="store_true",
                        help="офлайн-режим: urlopen подменяется заглушкой, сеть не используется")
    parser.add_argument("--out", default=os.path.join(".omo", "evidence", "bench"),
                        help="каталог для transcript_a.json / transcript_b.json / judge.json (по умолчанию .omo/evidence/bench)")
    parser.add_argument("--strict", action="store_true",
                        help="реальный режим: exit 1, если экономия prompt (B < A) отсутствует или < 10%%")
    parser.add_argument("--no-judge", action="store_true",
                        help="не вызывать LLM-judge")
    return parser


def load_dotenv() -> None:
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


def make_mock_urlopen(model: str):
    """Заглушка urlopen: OpenAI-совместимый ответ без сети.

    ask/сводка: prompt_tokens считается локально count_tokens() по
    текстам сообщений исходящего запроса (req.data), completion_tokens
    фиксированы (MOCK_COMPLETION_TOKENS).
    Judge-запрос (маркер JUDGE_MARKER в содержимом) — детерминированный
    JSON-отчёт {"score": 7, "verdict", "reason"}.
    """

    def fake_urlopen(req, timeout=None):  # сигнатура как у urllib.request.urlopen
        payload = json.loads(req.data.decode("utf-8"))
        is_judge = any(
            JUDGE_MARKER in (m.get("content") or "") for m in payload.get("messages", [])
        )
        prompt_tokens = sum(
            count_tokens(m.get("content") or "", model)
            for m in payload.get("messages", [])
        )
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": MOCK_COMPLETION_TOKENS,
            "total_tokens": prompt_tokens + MOCK_COMPLETION_TOKENS,
        }
        if is_judge:
            content = json.dumps(
                {"score": 7, "verdict": "mock-оценка",
                 "reason": "детерминированный mock-ответ бенчмарка (сеть не используется)"},
                ensure_ascii=False,
            )
        else:
            content = MOCK_REPLY
        response = {
            "choices": [{
                "message": {"content": content, "reasoning": None},
                "finish_reason": "stop",
            }],
            "usage": usage,
        }
        raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
        return io.BytesIO(raw)  # у BytesIO есть read() и контекст-менеджер

    return fake_urlopen


def _fresh_tmp_path(prefix: str) -> str:
    """Путь к несуществующему tmp-файлу (agent создаст структуру сам)."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
    os.close(fd)
    os.remove(path)
    return path


def run_section(label: str, agent: SimpleAgent, turns_count: int):
    """Прогнать первые turns_count скриптованных ходов; вернуть (records, usages).

    Печатает заголовок секции, таблицу
      turn | prompt | completion | total
    и строку ИТОГО. usage берётся из реального usage-поля ответа
    (result["usage"]).
    """
    print(f"=== Секция {label} ===")
    print(f"{'turn':>4} | {'prompt':>7} | {'completion':>10} | {'total':>8}")
    records = []
    usages = []
    for i, msg in enumerate(SCRIPTED_TURNS[:turns_count]):
        result = agent.ask(msg)
        usage = result.get("usage") or {}
        usages.append(usage)
        print(f"{i + 1:>4} | {usage.get('prompt_tokens'):>7} | "
              f"{usage.get('completion_tokens'):>10} | {usage.get('total_tokens'):>8}")
        records.append({"role": "user", "content": msg, "usage": usage})
        records.append({"role": "assistant", "content": result.get("reply"), "usage": usage})
    sp = sum(u.get("prompt_tokens") or 0 for u in usages)
    sc = sum(u.get("completion_tokens") or 0 for u in usages)
    st = sum(u.get("total_tokens") or 0 for u in usages)
    print(f"{'ИТОГО':>4} | {sp:>7} | {sc:>10} | {st:>8}")
    return records, usages


def _mock_expected_prompts(turns: int, model: str, compression: bool) -> list:
    """Канонические mock-значения prompt_tokens по ходам (детерминировано).

    Зеркало логики: mock-заглушка считает prompt как сумму
    count_tokens() по contents всех сообщений исходящего запроса
    (system + история + вопрос); agent в режиме сжатия держит окно
    DEFAULT_WINDOW_SIZE + сводку (в mock сводка = MOCK_REPLY), в
    несжатом — историю без ограничений и без сводки.
    """
    ct = lambda t: count_tokens(t, model)
    history = []
    summary = ""
    prompts = []
    for u in SCRIPTED_TURNS[:turns]:
        if compression:
            k = len(history) - DEFAULT_WINDOW_SIZE
            while k >= DEFAULT_SUMMARY_GAP:
                summary = MOCK_REPLY  # заглушка отвечает MOCK_REPLY на запрос сводки
                del history[:k]
                k = len(history) - DEFAULT_WINDOW_SIZE
        sysc = DEFAULT_SYSTEM_PROMPT
        if compression and summary:
            sysc += "\n\nРезюме диалога: " + summary
        prompts.append(ct(sysc) + sum(ct(c) for c in history) + ct(u))
        history.append(u)
        history.append(MOCK_REPLY)
        if compression and len(history) > HISTORY_CAP:
            del history[:len(history) - HISTORY_CAP]
    return prompts


def run_mock_asserts(turns: int, model: str, usages_a: list, usages_b: list, out_dir: str) -> None:
    """Жёсткий офлайн/CI-гейт: структурные ассерты на детерминированном mock.

    Любое нарушение — AssertionError (main перехватит и вернёт exit 1).
    """
    pa = [u["prompt_tokens"] for u in usages_a]
    pb = [u["prompt_tokens"] for u in usages_b]
    assert pa == _mock_expected_prompts(turns, model, False), \
        f"A: prompt-серия {pa} != каноника {_mock_expected_prompts(turns, model, False)}"
    assert pb == _mock_expected_prompts(turns, model, True), \
        f"B: prompt-серия {pb} != каноника {_mock_expected_prompts(turns, model, True)}"
    assert all(x < y for x, y in zip(pa, pa[1:])), "A: prompt должен расти с каждым ходом"
    assert max(pb) < max(pa), \
        f"B: max prompt {max(pb)} должен быть < max A {max(pa)} (окно+сводка ограничивает запрос)"
    assert all(u["completion_tokens"] == MOCK_COMPLETION_TOKENS for u in usages_a + usages_b), \
        "mock: completion_tokens должен быть фиксирован"
    for name in ("transcript_a.json", "transcript_b.json"):
        with open(os.path.join(out_dir, name), encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["messages"]) == 2 * turns, f"{name}: записей {len(data['messages'])} != {2 * turns}"
        assert all(m["usage"].get("prompt_tokens") is not None for m in data["messages"]), \
            f"{name}: usage отсутствует в записях транскрипта"
    print(f"Mock-проверки: OK (A sum_prompt={sum(pa)}, B sum_prompt={sum(pb)}, "
          f"экономия={100.0 * (sum(pa) - sum(pb)) / sum(pa):.1f}%)")


def _parse_judge_report(text: str) -> dict:
    """Разобрать ответ judge в {"score": int 1-10, "verdict": str, "reason": str}.

    Допускает JSON внутри ```-блоков и пояснений вокруг. Невалидно — ValueError.
    """
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"JSON не найден в ответе judge: {text[:200]}")
    data = json.loads(m.group(0))
    score, verdict, reason = data["score"], data["verdict"], data["reason"]
    if isinstance(score, float) and score.is_integer():
        score = int(score)
    if not isinstance(score, int) or not (1 <= score <= 10):
        raise ValueError(f"score вне диапазона 1-10: {score!r}")
    if not isinstance(verdict, str) or not isinstance(reason, str):
        raise ValueError("verdict/reason должны быть строками")
    return {"score": score, "verdict": verdict, "reason": reason}


def judge_transcript(model: str, mode_desc: str, records: list):
    """ОДИН judge-вызов на транскрипт (та же модель, T=0, thinking выключен).

    Возвращает {"score", "verdict", "reason"} или None: пустой транскрипт
    → skip + заметка; любой сбой (сеть/API/разбор) → WARNING, без падения.
    """
    if not records:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {mode_desc} — транскрипт пуст, judge пропущен")
        return None
    lines = [
        "Ты — независимый LLM-судья бенчмарка управления контекстом.",
        f"Ниже — диалог пользователя с ассистентом, проведённый в режиме: {mode_desc}.",
        "Оцени СВОЙСТВО ДИАЛОГА: связность и качество памяти — насколько ассистент на поздних ходах "
        "помнит и соблюдает решения, договорённости и факты, принятые на ранних ходах.",
        "Оцени по целочисленной шкале от 1 до 10 (10 — память и связность идеальны).",
        'Ответ — СТРОГО один JSON-объект без пояснений: {"score": int 1-10, "verdict": str, "reason": str}',
        "Все поля заполняй на русском языке.",
        "Диалог:",
    ]
    lines += [f"{m['role']}: {m['content']}" for m in records]
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты — независимый судья качества диалога. " + JUDGE_MARKER},
            {"role": "user", "content": "\n\n".join(lines)},
        ],
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get(MODEL_KEY_ENV.get(model) or "", "").strip()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        content = json.loads(raw)["choices"][0]["message"].get("content") or ""
        return _parse_judge_report(content)
    except Exception as e:
        print(f"ПРЕДУПРЕЖДЕНИЕ: LLM-judge не удался ({mode_desc}): {e}")
        return None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.turns < 1 or args.turns > len(SCRIPTED_TURNS):
        print(f"--turns должен быть от 1 до {len(SCRIPTED_TURNS)}", file=sys.stderr)
        return 2

    if args.mock:
        # заглушка ставится ДО создания агентов; сеть в --mock не используется.
        # ask() читает env до urlopen — ставим фиктивные значения.
        os.environ.setdefault("GPUSTACK_BASE_URL", "http://mock.invalid/v1")
        key_env_by_model = {
            "qwen3.8-27b": "GPUSTACK_API_KEY",
            "deepseek-v4-flash": "GPUSTACK_KEY_DEEPSEEK",
            "glm-5.3-flash": "GPUSTACK_KEY_GLM",
        }
        os.environ.setdefault(key_env_by_model.get(args.model, "GPUSTACK_API_KEY"), "mock-key")
        urllib.request.urlopen = make_mock_urlopen(args.model)
    else:
        # реальный режим: ключи и базовый URL — из .env (паттерн main.py)
        load_dotenv()

    reasoning = args.reasoning == "on"
    os.makedirs(args.out, exist_ok=True)

    # секция A: сжатие ВЫКЛ
    dlg_a = _fresh_tmp_path("bench-a-dlg-")
    hist_a = _fresh_tmp_path("bench-a-hist-")
    agent_a = SimpleAgent(model=args.model, reasoning=reasoning,
                          dialogues_file=dlg_a, history_file=hist_a)
    agent_a.configure(compression_enabled=False)
    print(f"\n[Секция A] сжатие ВЫКЛ | модель {args.model} | ходов {args.turns}")
    records_a, usages_a = run_section("A: без сжатия", agent_a, args.turns)
    with open(os.path.join(args.out, "transcript_a.json"), "w", encoding="utf-8") as f:
        json.dump({"section": "A", "model": args.model, "turns": args.turns,
                   "messages": records_a}, f, ensure_ascii=False, indent=2)
    for p in (dlg_a, hist_a):
        if os.path.exists(p):
            os.remove(p)

    # секция B: сжатие ВКЛ (окно/шаг — дефолты 6/4)
    dlg_b = _fresh_tmp_path("bench-b-dlg-")
    hist_b = _fresh_tmp_path("bench-b-hist-")
    agent_b = SimpleAgent(model=args.model, reasoning=reasoning,
                          dialogues_file=dlg_b, history_file=hist_b)
    agent_b.configure(compression_enabled=True)
    print(f"\n[Секция B] сжатие ВКЛ | модель {args.model} | ходов {args.turns}")
    records_b, usages_b = run_section("B: со сжатием", agent_b, args.turns)
    with open(os.path.join(args.out, "transcript_b.json"), "w", encoding="utf-8") as f:
        json.dump({"section": "B", "model": args.model, "turns": args.turns,
                   "messages": records_b}, f, ensure_ascii=False, indent=2)
    for p in (dlg_b, hist_b):
        if os.path.exists(p):
            os.remove(p)

    # LLM-judge: по одному вызову на транскрипт (после обоих прогонов)
    if args.no_judge:
        print("\nLLM-judge отключён (--no-judge)")
    else:
        print("\n=== LLM-judge ===")
        report_a = judge_transcript(args.model, "БЕЗ сжатия (полная история, без сводки)", records_a)
        report_b = judge_transcript(args.model, "СО сжатием (скользящее окно + LLM-сводка)", records_b)
        for label, rep in (("A: без сжатия", report_a), ("B: со сжатием", report_b)):
            if rep:
                print(f"{label}: score={rep['score']} | verdict: {rep['verdict']} | reason: {rep['reason']}")
            else:
                print(f"{label}: отчёт не получен (см. предупреждения выше)")
        with open(os.path.join(args.out, "judge.json"), "w", encoding="utf-8") as f:
            json.dump({"a": report_a, "b": report_b}, f, ensure_ascii=False, indent=2)
        print(f"Отчёт judge: {args.out}/judge.json")

    # итоги
    sum_a = sum(u.get("prompt_tokens") or 0 for u in usages_a)
    sum_b = sum(u.get("prompt_tokens") or 0 for u in usages_b)
    if sum_a:
        saving = 100.0 * (sum_a - sum_b) / sum_a
        print(f"\nИтог: prompt A={sum_a} | prompt B={sum_b} | экономия={saving:.1f}%")
    else:
        print(f"\nИтог: prompt A={sum_a} | prompt B={sum_b}")

    if args.mock:
        run_mock_asserts(args.turns, args.model, usages_a, usages_b, args.out)
    else:
        if any(u.get("prompt_tokens") is None for u in usages_a + usages_b):
            print("ОШИБКА: usage.prompt_tokens отсутствует в ответе API", file=sys.stderr)
            return 1
        if sum_b < sum_a and (sum_a - sum_b) / sum_a >= 0.10:
            print(f"OK: сжатие экономит prompt-токены ({100.0 * (sum_a - sum_b) / sum_a:.1f}% >= 10%)")
        else:
            print("ПРЕДУПРЕЖДЕНИЕ: LLM variance / insufficient savings (экономия отсутствует или < 10%)")
            if args.strict:
                return 1

    print(f"\nГотово: транскрипты в {args.out}/transcript_a.json и {args.out}/transcript_b.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"MOCK-ASSERT FAIL: {e}", file=sys.stderr)
        sys.exit(1)
