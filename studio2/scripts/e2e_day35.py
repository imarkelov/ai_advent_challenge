"""E2E проверка studio2 на fake-LLM: пайплайн, гейты, инварианты, артефакты."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDIO2 = ROOT / "studio2"
sys.path.insert(0, str(STUDIO2 / "backend"))

from config import DEFAULT_CONFIG  # noqa: E402
from llm import LlmClient  # noqa: E402
from pipeline.lead import (CriticSameModel, InvalidTransition, PendingGate,  # noqa: E402
                           QaLead, STAGES)
from store import RunStore  # noqa: E402


class NullBus:
    def __init__(self):
        self.events: list[dict] = []

    def publish(self, run_id, event):
        self.events.append(event)

    def close(self, run_id):
        pass


PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="studio2-e2e-"))
    try:
        doc = tmp / "doc"
        shutil.copytree(STUDIO2 / "doc", doc, ignore=shutil.ignore_patterns("runs"))
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for role in cfg["roles"]:
            cfg["roles"][role]["provider"] = "fake"

        store = RunStore(str(tmp / "data"))
        bus = NullBus()
        lead = QaLead(str(doc), str(tmp / "data"), LlmClient(cfg), bus, store, config=cfg)
        run = store.create_run("TC-4217", str(STUDIO2), "develop")
        run_id = run["id"]

        check("этапы заданы в порядке", STAGES[0] == "sdd" and STAGES[-1] == "done")

        try:
            lead.start(run_id)
            check("старт останавливается на гейте", False)
        except PendingGate:
            check("старт останавливается на гейте", True)

        sdd_files = {p.name for p in (doc / "runs" / run_id / "sdd").glob("*.md")}
        check("SDD созданы на каждого субагента",
              {"agent_qa-lead.md", "agent_researcher.md", "agent_testwriter.md",
               "agent_reviewer.md"} <= sdd_files)
        check("каркас записан", (doc / "runs" / run_id / "skeleton.md").exists())
        check("SSE-события сгенерированы",
              any(e["type"] == "sdd_written" for e in bus.events))

        start_stage = store.get_run(run_id)["stage"]
        try:
            lead.validate_transition(start_stage, "sdd")
            check("переход назад запрещён", False)
        except InvalidTransition:
            check("переход назад запрещён", True)

        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["roles"]["reviewer"]["model"] = bad["roles"]["testwriter"]["model"]
        for role in bad["roles"]:
            bad["roles"][role]["provider"] = "fake"
        bad_lead = QaLead(str(doc), str(tmp / "data"), LlmClient(bad), NullBus(), store, config=bad)
        try:
            bad_lead.validate_critic()
            check("ревьюер не может быть моделью тестировщика", False)
        except CriticSameModel:
            check("ревьюер не может быть моделью тестировщика", True)

        lead.approve_skeleton(run_id)
        lead.choose_execution(run_id, "generated")
        nbus_events = list(bus.events)
        lead.resume(run_id)

        final = store.get_run(run_id)
        check("запуск завершён", final["status"] == "done")
        check("вердикт выставлен", final["verdict"] == "green")
        check("итоговый отчёт есть", (doc / "runs" / run_id / "test_report.md").exists())
        check("для generated ревьюер пропущен",
              not any(e["type"] == "agent_started" and e.get("role") == "reviewer"
                      for e in nbus_events))
        check("сгенерированный тест лежит в test_generated",
              any((doc / "runs" / run_id / "test_generated").glob("*.kt")))

        try:
            lead.validate_transition(final["stage"], "study")
            check("третий круг изучения запрещён", False)
        except Exception:  # noqa: BLE001
            check("третий круг изучения запрещён", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} PASS, {FAILED} FAIL")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
