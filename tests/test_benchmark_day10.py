"""Бенчмарк стратегий day10 (задача 12) — TDD, офлайн.

Покрытие:
- S1 (sliding_window): 12 ходов сценария, max prompt равен канонике
  (окно 4 → запрос ограничен, роста нет), извлекательных вызовов нет;
- S2 (sticky_facts): ровно 12 извлекательных вызовов (по одному на
  user-ход), финальный system-промт содержит блок «Актуальные факты:»,
  факты == канонический mock-ответ (canned);
- S3 (branching): чекпоинт на len(history)=12 (после 6 ходов), ветка A —
  ходы 7-12 (user + ответы), ветка B — ровно один ask, B-payload не
  содержит A-текст хода 12, переключение на B случилось после хода 7;
- CLI-гейт: `python benchmark_day10.py --mock` (subprocess) → exit 0;
- --out FILE.json: файл пишется, валидный JSON, ровно 3 стратегии.

Офлайн: mock-urlopen бенчмарка (make_mock_urlopen_day10) подменяет
urllib.request.urlopen; реальный API в тестах НЕ вызывается.
"""
import json
import os
import subprocess
import sys

import pytest

from agent import DEFAULT_SYSTEM_PROMPT, count_tokens
from benchmark_day10 import (
    BENCHMARK_STRATEGIES,
    MOCK_REPLY,
    CANNED_FACTS,
    CallRecorder,
    StrategyBenchmarkRunner,
    make_mock_urlopen_day10,
    main,
)
from scenario_day10 import SCENARIO_TURNS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "qwen3.8-27b"

# обязательные метрики в JSON-отчёте по каждой стратегии
METRIC_KEYS = (
    "stability", "recall",
    "prompt_tokens", "completion_tokens", "total_tokens",
    "llm_calls", "extraction_calls", "max_prompt_tokens",
)


def make_runner(monkeypatch):
    """Runner на mock-urlopen (детерминированная заглушка, без сети)."""
    monkeypatch.setenv("GPUSTACK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("GPUSTACK_API_KEY", "test-key")
    recorder = CallRecorder(make_mock_urlopen_day10(MODEL))
    monkeypatch.setattr("urllib.request.urlopen", recorder)
    return StrategyBenchmarkRunner(model=MODEL, recorder=recorder)


# ---------------------------------------------------------------------------
# 1. S1: 12 ходов, max prompt ограничен окном, структура метрик
# ---------------------------------------------------------------------------

def test_s1_mock_bounded_payload_and_output_structure(monkeypatch):
    """Скользящее окно N=4: max prompt за прогон = каноника
    (system + последние 4 сообщения + ход 12), без извлечений."""
    runner = make_runner(monkeypatch)
    m = runner.run_strategy("sliding_window")
    # порог, выведенный из окна 4: payload = system + <=4 сообщений истории
    # + 1 user-вопрос; каждое сообщение <= самого длинного сценарного хода
    # (или canned-ответа). Рост от хода к ходу невозможен.
    ct = lambda t: count_tokens(t, MODEL)
    turn_max = max(ct(t) for t in SCENARIO_TURNS)
    msg_max = max(turn_max, ct(MOCK_REPLY))
    threshold = ct(DEFAULT_SYSTEM_PROMPT) + 4 * msg_max + turn_max
    assert m["max_prompt_tokens"] <= threshold, \
        f"S1: max prompt {m['max_prompt_tokens']} > порога окна 4 {threshold} (рост запроса)"
    # и нет неограниченного роста: max prompt <= порога даже на финальном ходе
    assert m["max_prompt_tokens"] >= max(ct(t) for t in SCENARIO_TURNS), \
        "S1: max prompt аномально мал"
    assert m["llm_calls"] == 12
    assert m["extraction_calls"] == 0
    assert m["checkpoint"] is None
    assert m["b_asks"] == 0
    for key in METRIC_KEYS:
        assert key in m, f"метрика {key} отсутствует в отчёте S1"
    assert 0.0 <= m["stability"] <= 1.0
    assert 0.0 <= m["recall"] <= 1.0


# ---------------------------------------------------------------------------
# 2. S2: 12 извлекательных вызовов, facts-блок, canned-факты
# ---------------------------------------------------------------------------

def test_s2_extraction_calls_facts_block_and_canned_facts(monkeypatch):
    """Sticky-факты: извлечение на каждый user-ход (12), в финальном
    system-промте блок «Актуальные факты:», факты == canned."""
    runner = make_runner(monkeypatch)
    m = runner.run_strategy("sticky_facts")
    assert m["extraction_calls"] == 12
    assert m["llm_calls"] == 24  # 12 ask + 12 извлечений
    assert m["facts"] == CANNED_FACTS
    assert m["facts_block_in_system"] is True
    # canned-значения покрывают все KEY_DETAILS → устойчивость полная
    assert m["stability"] == pytest.approx(1.0)
    assert m["recall"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 3. S3: протокол чекпоинт / ветка A / ветка B
# ---------------------------------------------------------------------------

def test_s3_checkpoint_branch_a_and_b_isolation(monkeypatch):
    """S3-протокол: чекпоинт после 6 ходов (len=12), A — ходы 7-12
    (6 user + 6 ответов), B — ровно 1 ask с изоляцией от A-хода 12,
    переключение на B — после хода 7."""
    runner = make_runner(monkeypatch)
    m = runner.run_strategy("branching")
    assert m["checkpoint"] == 12
    assert m["branch_a_messages"] == 12  # user 7..12 + ответы ассистента
    assert m["b_asks"] == 1
    assert m["b_payload_excludes_turn12"] is True
    assert m["switch_after_turn7"] is True
    assert m["main_calls"] == 13  # 6 + A(7..12) + B(1)
    assert m["extraction_calls"] == 0
    # преамбула (ходы 1-6) держит все ключевые детали
    assert m["stability"] == pytest.approx(1.0)
    assert m["recall"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. CLI-гейт: python benchmark_day10.py --mock → exit 0
# ---------------------------------------------------------------------------

def test_mock_cli_exit_zero():
    """Subprocess-прогон mock-режима: гейт проходит, exit 0."""
    proc = subprocess.run(
        [sys.executable, "benchmark_day10.py", "--mock"],
        cwd=REPO_ROOT, capture_output=True, timeout=180,
    )
    assert proc.returncode == 0, \
        f"--mock должен завершиться с exit 0: stderr={proc.stderr.decode('utf-8', 'replace')[:500]}"


# ---------------------------------------------------------------------------
# 5. --out FILE.json: валидный JSON, ровно 3 стратегии, метрики на месте
# ---------------------------------------------------------------------------

def test_out_json_written_with_three_strategies(tmp_path):
    """main() с --out: файл создан, JSON валиден, ключи = 3 стратегии."""
    out = tmp_path / "bench-day10.json"
    rc = main(["--mock", "--out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == set(BENCHMARK_STRATEGIES) == {
        "sliding_window", "sticky_facts", "branching"
    }
    for name, m in data.items():
        for key in METRIC_KEYS:
            assert key in m, f"{name}: метрика {key} отсутствует в --out JSON"
