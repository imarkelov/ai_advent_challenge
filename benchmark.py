"""Бенчмарк токенов при сжатии контекста (day9, скелет).

CLI прогоняет ОДИН скриптованный длинный диалог дважды: секция
A — «сжатие ВЫКЛ», секция B — «сжатие ВКЛ». Каждая секция создаёт
СВОЙ SimpleAgent на СВОЕМ tmp-файле диалогов (репозиторный
dialogues.json не трогается) и собирает usage по каждому ходу.

Скелет: переключение сжатия подключается в задаче 11 (после того,
как ядро сжатия ляжет в agent.py). Пока обе секции гоняют агента
с дефолтной конфигурацией; точка подключения помечена
TODO(task-11).

Режим --mock: urllib.request.urlopen подменяется ДО создания
агентов на заглушку OpenAI-совместимого ответа. Сеть не
используется: prompt_tokens считается локально существующей
count_tokens() по текстам сообщений исходящего запроса (растёт
от хода к ходу — история диалога в запросе удлиняется),
completion_tokens фиксированы (40).

Использование:
  python benchmark.py --mock --turns 5 --out .omo/evidence/bench-skel
  python benchmark.py --turns 22 --model glm-5.3-flash --reasoning on

Аргументы:
  --turns N       сколько первых скриптованных ходов прогонять (по
                  умолчанию 22 — весь диалог)
  --model M       модель агента (по умолчанию qwen3.8-27b)
  --reasoning     on/off — рассуждения модели (по умолчанию off)
  --mock          офлайн-заглушка API (без сети)
  --out DIR       каталог для transcript_a.json / transcript_b.json
                  (по умолчанию .omo/evidence/bench)
"""
import argparse
import io
import json
import os
import sys
import tempfile
import urllib.request

from agent import SimpleAgent, count_tokens

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
MOCK_REPLY = "Это mock-ответ бенчмарка (скелет task-5): сжатие подключается в задаче 11."
MOCK_COMPLETION_TOKENS = 40


def build_parser() -> argparse.ArgumentParser:
    """Аргументы CLI: --turns, --model, --reasoning, --mock, --out."""
    parser = argparse.ArgumentParser(
        description="Бенчмарк токенов: скриптованный диалог, секции A (сжатие ВЫКЛ) / B (сжатие ВКЛ). Скелет — task 5.",
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
                        help="каталог для transcript_a.json / transcript_b.json (по умолчанию .omo/evidence/bench)")
    return parser


def make_mock_urlopen(model: str):
    """Заглушка urlopen: OpenAI-совместимый ответ без сети.

    prompt_tokens считается локально count_tokens() по текстам
    сообщений исходящего запроса (req.data), completion_tokens
    фиксированы (MOCK_COMPLETION_TOKENS).
    """

    def fake_urlopen(req, timeout=None):  # сигнатура как у urllib.request.urlopen
        payload = json.loads(req.data.decode("utf-8"))
        prompt_tokens = sum(
            count_tokens(m.get("content") or "", model)
            for m in payload.get("messages", [])
        )
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": MOCK_COMPLETION_TOKENS,
            "total_tokens": prompt_tokens + MOCK_COMPLETION_TOKENS,
        }
        response = {
            "choices": [{
                "message": {"content": MOCK_REPLY, "reasoning": None},
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


def run_section(label: str, agent: SimpleAgent, turns_count: int) -> list:
    """Прогнать первые turns_count скриптованных ходов; вернуть записи хода.

    Печатает заголовок секции и построчно:
      turn N | prompt P | completion C | total T
    """
    print(f"=== Секция {label} ===")
    records = []
    for i, msg in enumerate(SCRIPTED_TURNS[:turns_count]):
        result = agent.ask(msg)
        usage = result.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        total = usage.get("total_tokens")
        print(f"turn {i + 1} | prompt {prompt} | completion {completion} | total {total}")
        records.append({
            "role": "user", "content": msg, "usage": usage,
        })
        records.append({
            "role": "assistant", "content": result.get("reply"), "usage": usage,
        })
    return records


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

    reasoning = args.reasoning == "on"
    os.makedirs(args.out, exist_ok=True)

    # секция A: сжатие ВЫКЛ
    dlg_a = _fresh_tmp_path("bench-a-dlg-")
    hist_a = _fresh_tmp_path("bench-a-hist-")
    agent_a = SimpleAgent(model=args.model, reasoning=reasoning,
                          dialogues_file=dlg_a, history_file=hist_a)
    print(f"\n[Секция A] сжатие ВЫКЛ | модель {args.model} | ходов {args.turns}")
    records_a = run_section("A: сжатие ВЫКЛ", agent_a, args.turns)
    with open(os.path.join(args.out, "transcript_a.json"), "w", encoding="utf-8") as f:
        json.dump({"section": "A", "model": args.model, "turns": args.turns,
                   "messages": records_a}, f, ensure_ascii=False, indent=2)
    for p in (dlg_a, hist_a):
        if os.path.exists(p):
            os.remove(p)

    # секция B: сжатие ВКЛ
    dlg_b = _fresh_tmp_path("bench-b-dlg-")
    hist_b = _fresh_tmp_path("bench-b-hist-")
    # TODO(task-11): включить сжатие через agent.configure(compression_enabled=...)
    agent_b = SimpleAgent(model=args.model, reasoning=reasoning,
                          dialogues_file=dlg_b, history_file=hist_b)
    print(f"\n[Секция B] сжатие ВКЛ | модель {args.model} | ходов {args.turns}")
    records_b = run_section("B: сжатие ВКЛ", agent_b, args.turns)
    with open(os.path.join(args.out, "transcript_b.json"), "w", encoding="utf-8") as f:
        json.dump({"section": "B", "model": args.model, "turns": args.turns,
                   "messages": records_b}, f, ensure_ascii=False, indent=2)
    for p in (dlg_b, hist_b):
        if os.path.exists(p):
            os.remove(p)

    print(f"\nГотово: транскрипты в {args.out}/transcript_a.json и transcript_b.json")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
