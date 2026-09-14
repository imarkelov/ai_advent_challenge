"""Бенчмарк стратегий контекста (day10, задача 12).

CLI прогоняет фиксированный 12-ходовой сценарий сбора ТЗ
(scenario_day10.SCENARIO_TURNS) на трёх стратегиях дня 10:
sliding_window / sticky_facts / branching. Каждая стратегия — СВОЙ
свежий SimpleAgent на СВОИХ tmp-файлах диалогов (dialogues.json
репозитория не трогается); стратегия задаётся switch_strategy().

Сценарий S3 (branching) идёт по протоколу ветвления:
  ходы 1-6  — основная история;
  после хода 6 — make_checkpoint() (чекпоинт на len(history)=12);
  ходы 7-12 — ветка A (активная);
  после хода 7 (ответ ветки A на ход 7 получен) — switch_branch("B"),
  один альтернативный вопрос (фиксированная RU-строка) на ветке B,
  ответ B записывается;
  switch_branch("A") — продолжение ветки A ходами 8-12.
  Всего основных ask: 6 + 6 (A) + 1 (B) = 13.

Метрики — ДЕТЕРМИНИРОВАННЫЕ, без LLM-judge и субъективных оценок:
  stability — доля KEY_DETAILS (scenario_day10), встречающихся
    (substring, без учёта регистра) в сообщениях последним payload
    стратегии (финальный ход прогона);
  recall    — средняя доля KEY_DETAILS в payload ходами 7-12
    (сценарий «references-back»: на поздних ходах ключевые факты
    должны быть видны модели);
  prompt/completion/total tokens — сумма usage по ВСЕМ LLM-вызовам
    прогона (основные ask + служебные извлечения S2), usage читается
    из ответа (в mock — детерминированный расчёт count_tokens);
  llm_calls / extraction_calls — число вызовов;
  max_prompt_tokens — максимальный prompt среди основных ask
    (для S1 — проверка ограниченности окном).

Режим --mock (по умолчанию и при явном флаге; --real — реальный API
через .env): urllib.request.urlopen подменяется заглушкой
OpenAI-совместимого ответа. Основной ask -> MOCK_REPLY (prompt_tokens
считается локально count_tokens() по сообщениям запроса — детерм.),
извлекательный вызов (форма temp 0 / max_tokens 300 / thinking off +
FACTS_MARKER) -> canned-факты JSON (CANNED_FACTS_JSON). Служебные
вызовы видны только CallRecorder (в /agent/last-request не попадают).

Mock-гейт (как run_mock_asserts в benchmark.py):
  S1: prompt-серия == канонике окна 4 (максимум ограничен, роста нет);
  S2: ровно 12 извлекательных вызовов (по одному на user-ход),
      в финальном system-промте блок «Актуальные факты:», факты == canned;
  S3: чекпоинт на 12, ветка B получила ровно 1 ask, B-payload не
      содержит содержимое сообщений ветки A, переключение на B —
      после хода 7;
  извлечения не происходят в S1/S3; usage — целые, total = prompt +
  completion. Сбой гейта -> exit 1.

Использование:
  python benchmark_day10.py --mock                 # офлайн-гейт, exit 0
  python benchmark_day10.py --mock --out FILE.json # + JSON-отчёт
  python benchmark_day10.py                        # тоже mock (с заметкой)
  python benchmark_day10.py --real                 # реальный API (.env)

Аргументы:
  --mock          офлайн-заглушка API + mock-гейт (exit 1 при сбое)
  --real          реальный API (env GPUSTACK_BASE_URL / GPUSTACK_API_KEY)
  --out FILE.json файл для JSON-отчёта {strategy: metrics}
  --model M       модель агента (по умолчанию qwen3.8-27b)
"""
import argparse
import io
import json
import os
import sys
import tempfile
import urllib.request

from agent import SimpleAgent, count_tokens, DEFAULT_SYSTEM_PROMPT, MODEL_KEY_ENV
from facts import FACTS_MARKER
from scenario_day10 import SCENARIO_TURNS, KEY_DETAILS

# стратегии дня 10, участвующие в бенчмарке (legacy — не прогоняется)
BENCHMARK_STRATEGIES = ["sliding_window", "sticky_facts", "branching"]

# фиксированный mock-ответ на основные ask
MOCK_REPLY = ("Это mock-ответ бенчмарка day10: стратегии отработаны "
              "на детерминированной заглушке.")
MOCK_COMPLETION_TOKENS = 40

# единственный вопрос ветки B (S3): фиксированная RU-строка
B_ALTERNATIVE_QUESTION = "Предложите альтернативный стек: Django + SQLite — что изменится?"

# canned-факты (mock-ответ на извлечение). Значения фиксированы и
# покрывают ВСЕ KEY_DETAILS: устойчивость S2 не зависит от «модели».
CANNED_FACTS = {
    "цель": "корпоративный портал для логистической компании",
    "стек": "FastAPI + PostgreSQL + React",
    "ограничения": "внутренняя сеть, двухфакторная аутентификация",
    "дедлайн": "15 ноября",
    "решения": "роли: сотрудник и роль администратора",
}
CANNED_FACTS_JSON = json.dumps(CANNED_FACTS, ensure_ascii=False)

DOT_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def build_parser() -> argparse.ArgumentParser:
    """Аргументы CLI: --mock, --real, --out FILE.json, --model."""
    parser = argparse.ArgumentParser(
        description="Бенчмарк стратегий контекста day10: 12-ходовой сценарий "
                    "сбора ТЗ на sliding_window / sticky_facts / branching.",
    )
    parser.add_argument("--mock", action="store_true",
                        help="офлайн-режим: urlopen подменяется заглушкой, сеть не используется")
    parser.add_argument("--real", action="store_true",
                        help="реальный API: ключи и базовый URL из .env")
    parser.add_argument("--out", default=None, metavar="FILE.json",
                        help="файл для JSON-отчёта {strategy: metrics}")
    parser.add_argument("--model", default="qwen3.8-27b",
                        help="модель агента (по умолчанию qwen3.8-27b)")
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


def _is_service_shape(payload: dict) -> bool:
    """Форма служебного (текстового) запроса: temp 0, max_tokens 300,
    thinking выключен — та же форма, что у day9-сводки и извлечения."""
    return (
        payload.get("temperature") == 0
        and payload.get("max_tokens") == 300
        and payload.get("chat_template_kwargs") == {"enable_thinking": False}
    )


def classify_call(payload: dict) -> str:
    """Вид LLM-вызова: "extraction" / "summary" / "main".

    Извлечение — служебная форма + FACTS_MARKER в промпте; сводка —
    служебная форма + «Сожми» (у трёх стратегий дня 10 не возникает);
    остальное — основной ask.
    """
    contents = " ".join(m.get("content") or "" for m in payload.get("messages", []))
    if _is_service_shape(payload):
        if FACTS_MARKER in contents:
            return "extraction"
        if "Сожми" in contents:
            return "summary"
    return "main"


def make_mock_urlopen_day10(model: str):
    """Заглушка urlopen: OpenAI-совместимый ответ без сети (паттерн
    benchmark.py make_mock_urlopen).

    Основной ask: content = MOCK_REPLY. Извлекательный вызов
    (FACTS_MARKER): content = CANNED_FACTS_JSON. prompt_tokens — сумма
    count_tokens() по сообщениям исходящего запроса (детерминировано),
    completion_tokens фиксированы (MOCK_COMPLETION_TOKENS).
    """

    def fake_urlopen(req, timeout=None):  # сигнатура как у urllib.request.urlopen
        payload = json.loads(req.data.decode("utf-8"))
        kind = classify_call(payload)
        prompt_tokens = sum(
            count_tokens(m.get("content") or "", model)
            for m in payload.get("messages", [])
        )
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": MOCK_COMPLETION_TOKENS,
            "total_tokens": prompt_tokens + MOCK_COMPLETION_TOKENS,
        }
        content = CANNED_FACTS_JSON if kind == "extraction" else MOCK_REPLY
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


class CallRecorder:
    """Реестр ВСЕХ LLM-вызовов прогона (основные + служебные).

    Оборачивает реализацию urlopen (mock или реальный): классифицирует
    каждый вызов, читает usage из тела ответа, возвращает прокси ответа.
    Запись: {"kind": "main"|"extraction"|"summary", "prompt_tokens",
    "completion_tokens", "total_tokens", "payload"}. Служебные вызовы
    агент в self._last_request НЕ пишет, поэтому реестр нужен отдельно.
    """

    def __init__(self, impl):
        self.impl = impl
        self.calls = []

    def __call__(self, req, timeout=None):  # сигнатура как у urllib.request.urlopen
        payload = json.loads(req.data.decode("utf-8"))
        kind = classify_call(payload)
        resp = self.impl(req, timeout=timeout)
        return _ReadingProxy(resp, self, kind, payload)

    def record(self, kind: str, payload: dict, usage: dict) -> None:
        self.calls.append({
            "kind": kind,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "payload": payload,
        })


def _fresh_tmp_path(prefix: str) -> str:
    """Путь к несуществующему tmp-файлу (agent создаст структуру сам)."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
    os.close(fd)
    os.remove(path)
    return path


def _detail_hits(messages: list) -> int:
    """Сколько KEY_DETAILS встречается (substring, без учёта регистра)
    в содержимом сообщений."""
    joined = " ".join((m.get("content") or "") for m in messages).lower()
    return sum(1 for d in KEY_DETAILS if d.lower() in joined)


def _turns_7_12_prompts(model: str) -> list:
    """Канонические mock-prompt по ходам S1 (окно 4): system + последние
    4 сообщения истории + текущий вопрос (mirror SlidingWindowStrategy)."""
    ct = lambda t: count_tokens(t, model)
    sysc = DEFAULT_SYSTEM_PROMPT
    history = []
    prompts = []
    for u in SCENARIO_TURNS:
        prompts.append(ct(sysc) + sum(ct(c) for c in history[-4:]) + ct(u))
        history.append(u)
        history.append(MOCK_REPLY)
    return prompts


def _main_payloads(calls: list) -> list:
    """Основной ask-вызовы прогона (payload) в порядке выполнения."""
    return [c["payload"] for c in calls if c["kind"] == "main"]


class StrategyBenchmarkRunner:
    """Прогон сценария day10 по одной или всем стратегиям.

    Публичный API:
      run_strategy(name) -> dict — полный отчёт прогона стратегии:
        {"strategy", "metrics", "calls" (сегмент реестра),
         "turn_payloads" {label: messages}, "checkpoint" (S3),
         "branches" (S3, копия), "facts" (S2, копия), "b_payload"}
      run_all() -> {name: report} — по всем BENCHMARK_STRATEGIES.
    """

    def __init__(self, model: str = "qwen3.8-27b", recorder: "CallRecorder | None" = None):
        self.model = model
        # recorder оборачивает уже установленную реализацию urlopen
        # (в main() — CallRecorder/mock или реальный urlopen)
        self.recorder = recorder if recorder is not None else CallRecorder(urllib.request.urlopen)

    # ------------------------------------------------------------------
    def _agent_for(self, name: str) -> SimpleAgent:
        """Свежий SimpleAgent на tmp-файлах со стратегией на активном диалоге."""
        dlg = _fresh_tmp_path(f"bench10-{name}-dlg-")
        hist = _fresh_tmp_path(f"bench10-{name}-hist-")
        agent = SimpleAgent(model=self.model, dialogues_file=dlg, history_file=hist)
        agent.switch_strategy(name)
        return agent

    @staticmethod
    def _cleanup_agent(agent: SimpleAgent) -> None:
        for p in (agent.dialogues_file, agent.history_file):
            if os.path.exists(p):
                os.remove(p)

    # ------------------------------------------------------------------
    def run_strategy(self, name: str) -> dict:
        """Прогон одной стратегии; вернуть полный отчёт (см. класс)."""
        if name not in BENCHMARK_STRATEGIES:
            raise ValueError(f"стратегия {name!r} не входит в бенчмарк: {BENCHMARK_STRATEGIES}")
        agent = self._agent_for(name)
        start = len(self.recorder.calls)
        try:
            turn_payloads = {}   # label -> messages основного ask
            checkpoint = None
            b_payload = None

            def ask_and_capture(label: str, text: str) -> None:
                agent.ask(text)
                turn_payloads[label] = agent.get_last_request()["messages"]

            if name == "branching":
                # ходы 1-6 — до чекпоинта (полная история, веток нет)
                for i in range(6):
                    ask_and_capture(str(i + 1), SCENARIO_TURNS[i])
                checkpoint = agent.make_checkpoint().get("checkpoint")
                # ветка A: ход 7
                ask_and_capture("A7", SCENARIO_TURNS[6])
                # ветка B: ровно один альтернативный вопрос
                agent.switch_branch("B")
                ask_and_capture("B", B_ALTERNATIVE_QUESTION)
                b_payload = turn_payloads["B"]
                # обратно в A: ходы 8-12
                agent.switch_branch("A")
                for i in range(7, 12):
                    ask_and_capture(f"A{i + 1}", SCENARIO_TURNS[i])
                branch_labels = [f"A{i}" for i in range(7, 13)]
            else:
                for i, turn in enumerate(SCENARIO_TURNS, start=1):
                    ask_and_capture(str(i), turn)
                branch_labels = [str(i) for i in range(7, 13)]

            calls = self.recorder.calls[start:]
            info = agent.get_strategy_info()
            branches = (info["strategy_state"].get("branches")
                        if name == "branching" else None)
            report = self._metrics(name, calls, turn_payloads, branch_labels, {
                "checkpoint": checkpoint,
                "branches": branches,
                "b_payload": b_payload,
                "facts": dict(info.get("facts") or {}),
            })
            report.update({
                "strategy": name,
                "calls": calls,
                "turn_payloads": turn_payloads,
                "branches": branches,
                "b_payload": b_payload,
            })
            return report
        finally:
            self._cleanup_agent(agent)

    def run_all(self) -> dict:
        """Прогон всех BENCHMARK_STRATEGIES: {name: report}."""
        return {name: self.run_strategy(name) for name in BENCHMARK_STRATEGIES}

    # ------------------------------------------------------------------
    @staticmethod
    def _metrics(name: str, calls: list, turn_payloads: dict,
                 branch_labels: list, ctx: dict) -> dict:
        """Детерминированные метрики прогона (без LLM-judge), плоский dict.

        stability — доля KEY_DETAILS в payload финального хода;
        recall    — средняя доля KEY_DETAILS в payload ходах 7-12;
        токены    — сумма usage по ВСЕМ вызовам (main + extraction);
        max_prompt_tokens — максимум prompt среди основных ask.
        Вспомогательные (S2/S3): facts, facts_block_in_system,
        checkpoint, branch_a_messages, b_asks, b_payload_excludes_turn12,
        switch_after_turn7.
        """
        mains = [c for c in calls if c["kind"] == "main"]
        extractions = [c for c in calls if c["kind"] == "extraction"]
        # ходы 7-12: по 6 user-вопросов у всех стратегий (у S3 — ветка A)
        late = [turn_payloads[label] for label in branch_labels]
        recall = sum(_detail_hits(p) for p in late) / (len(late) * len(KEY_DETAILS))
        stability = _detail_hits(turn_payloads[list(turn_payloads)[-1]]) / len(KEY_DETAILS)
        b_asks = 0
        switch_after_turn7 = None
        b_payload_excludes_turn12 = None
        if name == "branching":
            main_last_texts = [(c["payload"]["messages"][-1].get("content") or "")
                               for c in mains]
            idx_a7 = main_last_texts.index(SCENARIO_TURNS[6])
            b_idx = [i for i, t in enumerate(main_last_texts)
                     if t == B_ALTERNATIVE_QUESTION]
            b_asks = len(b_idx)
            switch_after_turn7 = (b_asks == 1 and b_idx[0] > idx_a7)
            b_payload = ctx.get("b_payload")
            if b_payload is not None:
                b_text = " ".join(m.get("content") or "" for m in b_payload)
                b_payload_excludes_turn12 = SCENARIO_TURNS[11] not in b_text
        branches = ctx.get("branches") or {}
        return {
            "stability": round(stability, 4),
            "recall": round(recall, 4),
            "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
            "completion_tokens": sum(c["completion_tokens"] or 0 for c in calls),
            "total_tokens": sum(c["total_tokens"] or 0 for c in calls),
            "llm_calls": len(calls),
            "main_calls": len(mains),
            "extraction_calls": len(extractions),
            "max_prompt_tokens": max((c["prompt_tokens"] or 0) for c in mains),
            # вспомогательные для гейта/тестов (детерминированные)
            "facts": dict(ctx.get("facts") or {}),
            "facts_block_in_system": "Актуальные факты:" in
                                     (turn_payloads[list(turn_payloads)[-1]][0].get("content") or ""),
            "checkpoint": ctx.get("checkpoint"),
            "branch_a_messages": len(branches.get("A") or []),
            "b_asks": b_asks,
            "b_payload_excludes_turn12": b_payload_excludes_turn12,
            "switch_after_turn7": switch_after_turn7,
        }


# ----------------------------------------------------------------------
# Mock-гейт (жёсткий офлайн/CI): сбой — AssertionError (main → exit 1)
# ----------------------------------------------------------------------

def run_mock_gate(reports: dict, model: str) -> None:
    """Структурные ассерты на детерминированном mock (см. docstring)."""
    s1 = reports["sliding_window"]
    s2 = reports["sticky_facts"]
    s3 = reports["branching"]

    # S1: prompt-серия основных ask == канонике окна 4 (запрос ограничен)
    expected = _turns_7_12_prompts(model)  # все 12 ходов, каноника серии
    actual = [c["prompt_tokens"] for c in s1["calls"] if c["kind"] == "main"]
    assert actual == expected, f"S1: prompt-серия {actual} != каноника {expected}"
    assert s1["extraction_calls"] == 0, "S1: извлекательные вызовы не ожидаются"

    # S2: ровно 12 извлечений (по одному на user-ход), facts-блок, canned
    assert s2["extraction_calls"] == 12, \
        f"S2: извлечений {s2['extraction_calls']} != 12"
    assert s2["facts_block_in_system"] is True, \
        "S2: в финальном system-промте должен быть блок «Актуальные факты:»"
    assert s2["facts"] == CANNED_FACTS, f"S2: факты {s2['facts']} != canned {CANNED_FACTS}"

    # S3: чекпоинт на 12, B — ровно 1 ask, изоляция B от A, порядок
    assert s3["checkpoint"] == 12, f"S3: чекпоинт {s3['checkpoint']} != 12"
    assert s3["extraction_calls"] == 0, "S3: извлекательные вызовы не ожидаются"
    assert s3["b_asks"] == 1, f"S3: B-ask {s3['b_asks']} != 1"
    assert s3["b_payload_excludes_turn12"] is True, \
        "S3: B-payload не должен содержать текст A-хода 12"
    assert s3["switch_after_turn7"] is True, "S3: переключение на B — после хода 7"
    b_messages = s3["branches"]["B"]
    assert len(b_messages) == 2 and b_messages[0]["content"] == B_ALTERNATIVE_QUESTION, \
        f"S3: ветка B должна содержать ровно 1 ask: {b_messages}"
    # сравнение по user-ходам ветки A (canned-ответ ассистента есть
    # и в преамбуле — его сравнивать некорректно)
    a_user_contents = [m["content"] for m in s3["branches"]["A"]
                       if m["role"] == "user"]
    b_text = " ".join(m.get("content") or "" for m in s3["b_payload"])
    assert not any(c in b_text for c in a_user_contents), \
        "S3: B-payload не должен содержать user-ходы ветки A"
    # переключение на B — после хода 7: A7 стоит в ветке A ДО B-ask
    a_user_texts = [m["content"] for m in s3["branches"]["A"] if m["role"] == "user"]
    assert a_user_texts[0] == SCENARIO_TURNS[6], \
        "S3: первый user-ход ветки A должен быть ходом 7"
    assert SCENARIO_TURNS[6] not in b_text, "S3: A-ход 7 не должен попасть в B-payload"

    # все: usage — целые, total = prompt + completion (finite)
    for name, rep in reports.items():
        for c in rep["calls"]:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                assert isinstance(c[key], int) and c[key] >= 0, \
                    f"{name}: usage.{key} = {c[key]!r} (ожидается int >= 0)"
            assert c["total_tokens"] == c["prompt_tokens"] + c["completion_tokens"], \
                f"{name}: total != prompt + completion"
    print("Mock-гейт: OK (S1 max prompt="
          f"{s1['max_prompt_tokens']}, S2 extraction="
          f"{s2['extraction_calls']}, S3 B-asks={s3['b_asks']})")


# ----------------------------------------------------------------------
# Консольный отчёт
# ----------------------------------------------------------------------

def print_table(reports: dict) -> None:
    """Выровненная таблица: 3 стратегии × метрики + строка ИТОГО."""
    header = (f"{'стратегия':<15} | {'stability':>9} | {'recall':>6} | "
              f"{'prompt':>7} | {'completion':>10} | {'total':>8} | {'calls':>5}")
    print("\n" + header)
    print("-" * len(header))
    sp = sc = st = scalls = 0
    for name in BENCHMARK_STRATEGIES:
        m = reports[name]
        print(f"{name:<15} | {m['stability']:>9.4f} | {m['recall']:>6.4f} | "
              f"{m['prompt_tokens']:>7} | {m['completion_tokens']:>10} | "
              f"{m['total_tokens']:>8} | {m['llm_calls']:>5}")
        sp += m["prompt_tokens"]
        sc += m["completion_tokens"]
        st += m["total_tokens"]
        scalls += m["llm_calls"]
    print(f"{'ИТОГО':<15} | {'':>9} | {'':>6} | {sp:>7} | {sc:>10} | {st:>8} | {scalls:>5}")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.mock and args.real:
        print("--mock и --real несовместимы", file=sys.stderr)
        return 2
    mock = not args.real  # без флагов — тоже mock (с заметкой)
    if mock and not args.mock:
        print("Замечание: флаги не заданы — mock-режим по умолчанию "
              "(реальный API: --real).")

    if mock:
        # заглушка ставится ДО создания агентов; сеть в --mock не используется.
        # ask() читает env до urlopen — ставим фиктивные значения.
        os.environ.setdefault("GPUSTACK_BASE_URL", "http://mock.invalid/v1")
        os.environ.setdefault(MODEL_KEY_ENV.get(args.model, "GPUSTACK_API_KEY"), "mock-key")
        # реестр оборачивает mock-заглушку (usage — из её ответа)
        recorder = CallRecorder(make_mock_urlopen_day10(args.model))
        urllib.request.urlopen = recorder
    else:
        # реальный режим: ключи и базовый URL — из .env (паттерн main.py)
        load_dotenv()
        # реальный urlopen тоже обёртываем реестром (usage из ответов)
        recorder = CallRecorder(urllib.request.urlopen)
        urllib.request.urlopen = recorder

    runner = StrategyBenchmarkRunner(model=args.model, recorder=recorder)
    reports = runner.run_all()
    print(f"Бенчмарк стратегий day10 | модель {args.model} | "
          f"режим {'mock' if mock else 'real'} | ходов 12")
    print_table(reports)

    if args.out:
        # метрики по стратегиям (тяжёлые служебные поля не выгружаются)
        heavy = ("calls", "turn_payloads", "branches", "b_payload")
        payload = {name: {k: v for k, v in rep.items() if k not in heavy}
                   for name, rep in reports.items()}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"JSON-отчёт: {args.out}")

    if mock:
        run_mock_gate(reports, args.model)
    else:
        for name, rep in reports.items():
            if any(c["prompt_tokens"] is None for c in rep["calls"]):
                print(f"ОШИБКА: {name} — usage.prompt_tokens отсутствует в ответе API",
                      file=sys.stderr)
                return 1
    return 0


class _ReadingProxy:
    """Прокси ответа: перехватывает read(), чтобы зафиксировать usage."""

    def __init__(self, real, recorder: CallRecorder, kind: str, payload: dict):
        self._real = real
        self._recorder = recorder
        self._kind = kind
        self._payload = payload

    def read(self, *a, **kw):
        data = self._real.read(*a, **kw)
        try:
            usage = json.loads(data.decode("utf-8", errors="replace")).get("usage") or {}
            self._recorder.record(self._kind, self._payload, usage)
        except (ValueError, AttributeError):
            self._recorder.record(self._kind, self._payload, {})
        return data

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, item):
        return getattr(self._real, item)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        sys.exit(1)
    except AssertionError as e:
        print(f"MOCK-GATE FAIL: {e}", file=sys.stderr)
        sys.exit(1)
