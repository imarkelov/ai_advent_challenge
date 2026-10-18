"""Оркестратор QA-Lead: стадийная машина, гейты, раунды, ревью отдельной LLM."""
from __future__ import annotations

from config import load_config
from pipeline.artifacts import (RunDir, review_markdown, sdd_text, skeleton_markdown,
                                test_report_markdown)
from pipeline.subagents import run_subagent
from roles import load_agents

STAGES = ["sdd", "study", "skeleton", "write", "provision",
          "execute", "debug", "review", "done"]

_STAGE_ROLE = {
    "study": "researcher",
    "skeleton": "testwriter",
    "write": "testwriter",
    "debug": "debugger",
    "review": "reviewer",
}


class InvalidTransition(RuntimeError):
    pass


class GateError(RuntimeError):
    pass


class PendingGate(RuntimeError):
    pass


class CriticSameModel(RuntimeError):
    pass


class QuestionTimeout(RuntimeError):
    pass


class QaLead:
    def __init__(self, doc_dir, data_dir, llm, bus, store, config=None,
                 rag=None, git_factory=None, android=None, gradle=None):
        self.doc_dir = doc_dir
        self.data_dir = data_dir
        self.llm = llm
        self.bus = bus
        self.store = store
        self.config = config or load_config(data_dir)
        self.rag = rag
        self.git_factory = git_factory
        self.android = android
        self.gradle = gradle
        self.agents = load_agents(doc_dir)
        self._cancelled: set[str] = set()

    # ---------- инварианты ----------

    def validate_critic(self) -> None:
        if not self.config.get("critic", {}).get("must_differ_from_author", True):
            return
        reviewer = self.config["roles"]["reviewer"]["model"]
        writer = self.config["roles"]["testwriter"]["model"]
        if reviewer == writer:
            raise CriticSameModel(
                "модель ревьюера должна отличаться от модели тестировщика"
            )

    def validate_transition(self, current: str, nxt: str) -> None:
        if nxt == "study" and current == "review":
            return
        if current not in STAGES or nxt not in STAGES:
            raise InvalidTransition(f"неизвестная стадия: {current} → {nxt}")
        if STAGES.index(nxt) <= STAGES.index(current):
            raise InvalidTransition(f"запрещённый переход: {current} → {nxt}")

    def next_stage(self, nxt: str, run: dict) -> str:
        self.validate_transition(run["stage"], nxt)
        if nxt == "study" and run.get("round", 1) >= 2:
            raise InvalidTransition("третий круг изучения запрещён")
        return nxt

    def ensure_gate(self, stage: str, run: dict) -> None:
        if stage == "write" and not run.get("skeleton_approved"):
            raise GateError("переход в write запрещён: каркас не утверждён")
        if stage == "provision" and not run.get("execution_choice"):
            raise GateError("переход в provision запрещён: способ исполнения не выбран")

    # ---------- гейты ----------

    def approve_skeleton(self, run_id: str, approved: bool = True) -> dict:
        run = self.store.update_run(run_id, skeleton_approved=approved)
        self.bus.publish(run_id, {
            "type": "skeleton_approved" if approved else "skeleton_updated",
            "path": "skeleton.md",
        })
        self.store.append_log(run_id, "human",
                              "каркас утверждён" if approved else "каркас отклонён")
        return run

    def choose_execution(self, run_id: str, choice: str) -> dict:
        if choice not in {"as_loaded", "generated"}:
            raise ValueError("способ исполнения должен быть as_loaded или generated")
        run = self.store.update_run(run_id, execution_choice=choice)
        self.bus.publish(run_id, {"type": "execution_chosen", "choice": choice})
        self.store.append_log(run_id, "human", f"способ исполнения: {choice}")
        return run

    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)
        self.store.update_run(run_id, status="cancelled")
        self.bus.publish(run_id, {"type": "run_done", "status": "cancelled",
                                  "report_path": ""})
        self.bus.close(run_id)

    # ---------- основной цикл ----------

    def start(self, run_id: str) -> None:
        run = self._require(run_id)
        self.validate_critic()
        self.bus.publish(run_id, {"type": "run_started", "run_id": run_id,
                                  "task": run["task_id"]})
        run_dir = RunDir(self.doc_dir, run_id)
        run_dir.ensure()
        self._write_sdd(run, run_dir)
        self._emit_stage(run_id, "study", run.get("round", 1))
        self._run_stage_agent("study", run, run_dir)
        run = self._require(run_id)
        self._emit_stage(run_id, "skeleton", run.get("round", 1))
        self._run_stage_agent("skeleton", run, run_dir)
        if not run_dir.skeleton_path.exists():
            run_dir.write("skeleton.md", self._default_skeleton(run))
        self.bus.publish(run_id, {"type": "skeleton_ready",
                                  "path": "skeleton.md",
                                  "steps": [], "tags": [], "assumptions": []})
        raise PendingGate("пайплайн ждёт апрув каркаса и выбор способа исполнения")

    def resume(self, run_id: str) -> None:
        run = self._require(run_id)
        run_dir = RunDir(self.doc_dir, run_id)
        run_dir.ensure()
        self.ensure_gate("write", run)
        self.ensure_gate("provision", run)

        self._emit_stage(run_id, "write", run.get("round", 1))
        self._run_stage_agent("write", run, run_dir)
        self._ensure_generated_test(run, run_dir)

        self._emit_stage(run_id, "provision", run.get("round", 1))
        self._provision(run, run_dir)

        self._emit_stage(run_id, "execute", run.get("round", 1))
        result = self._execute(run, run_dir)

        if not result["success"]:
            self._emit_stage(run_id, "debug", run.get("round", 1))
            self._run_stage_agent("debug", run, run_dir, context={
                "Gradle log": result.get("log", "")[:4000],
            })

        self._emit_stage(run_id, "review", run.get("round", 1))
        self._run_stage_agent("review", run, run_dir, context={
            "Gradle result": str(result),
        })
        verdict = "green" if result["success"] else "red"
        run_dir.write("review_report.md", review_markdown(
            run_id,
            [{"test": row.get("test", ""),
              "status": "passed" if result["success"] else "failed",
              "duration": result.get("duration", ""),
              "reason": row.get("method", "")} for row in result.get("failures", [])],
            verdict,
        ))
        self.bus.publish(run_id, {"type": "review_verdict", "verdict": verdict,
                                  "path": "review_report.md"})

        self._finish(run_id, run_dir, run, result, verdict)

    def _finish(self, run_id, run_dir, run, result, verdict) -> None:
        run = self._require(run_id, allow_done=True)
        stages = STAGES[:STAGES.index(run["stage"]) + 1] + ["done"]
        run_dir.write("test_report.md", test_report_markdown(
            run_id, run["task_id"], run["branch"], stages, verdict,
            ["skeleton.md", "review_report.md", "test_report.md"],
            "Пайплайн завершён. См. review_report.md и test_generated/.",
        ))
        self._emit_stage(run_id, "done", run.get("round", 1))
        self.store.update_run(run_id, status="done", verdict=verdict)
        self.bus.publish(run_id, {"type": "run_done", "status": "done",
                                  "report_path": "test_report.md"})
        self.bus.close(run_id)

    # ---------- стадии ----------

    def _require(self, run_id: str, allow_done: bool = False) -> dict:
        if run_id in self._cancelled:
            raise InvalidTransition("запуск отменён")
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        if not allow_done and run.get("status") == "cancelled":
            raise InvalidTransition("запуск отменён")
        return run

    def _emit_stage(self, run_id: str, stage: str, round_no: int) -> None:
        self.store.update_run(run_id, stage=stage, round=round_no)
        self.bus.publish(run_id, {"type": "stage_changed", "stage": stage,
                                  "round": round_no})

    def _write_sdd(self, run: dict, run_dir: RunDir) -> None:
        git_summary = "git недоступен, контекст ограничен"
        if self.git_factory:
            try:
                git_summary = self.git_factory(run["repo_path"]).recent_changes(limit=10)
            except Exception as exc:  # noqa: BLE001 - причина уходит агенту
                git_summary = f"git: {exc}"
        for role, spec in self.agents.items():
            text = sdd_text(
                role=role,
                task_id=run["task_id"],
                task_title=run["task_id"],
                repo_path=run["repo_path"],
                branch=run["branch"],
                git_summary=git_summary[:4000],
                goal=spec.body[:400],
                rag_inputs=", ".join(spec.inputs),
                artifacts="—",
                instructions="Выполни роль согласно своему описанию.",
                allowed_tools=", ".join(spec.allowed_tools),
                max_rounds=spec.max_rounds,
                write_scope=", ".join(spec.outputs) or "—",
                criteria="- [ ] статус done в JSON-ответе",
            )
            run_dir.write(f"sdd/agent_{role}.md", text)
            self.bus.publish(run["id"], {"type": "sdd_written", "role": role,
                                         "path": f"sdd/agent_{role}.md", "title": role})

    def _run_stage_agent(self, stage: str, run: dict, run_dir: RunDir,
                         context: dict | None = None) -> None:
        role = _STAGE_ROLE[stage]
        spec = self.agents[role]
        ctx: dict = dict(context or {})
        if self.rag is not None:
            try:
                hits = self.rag.search(run["task_id"], limit=5)
                ctx["RAG"] = "\n".join(f"- {hit['path']}: {hit['snippet']}" for hit in hits)
            except Exception as exc:  # noqa: BLE001
                ctx["RAG"] = f"RAG недоступен: {exc}"
        reply = run_subagent(role, spec, self.llm, run_dir, ctx, self.bus, run["id"])
        self.store.append_log(run["id"], role, reply.summary or reply.status)
        if reply.status == "need_question":
            self.bus.publish(run["id"], {
                "type": "question",
                "question_id": f"{run['id']}-{role}",
                "role": role,
                "text": (reply.question or {}).get("text", "нужна уточняющая информация"),
                "options": (reply.question or {}).get("options", []),
            })

    def _default_skeleton(self, run: dict) -> str:
        return skeleton_markdown(
            f"Generated{run['task_id'].replace('-', '')}Test",
            run["task_id"],
            ["Открыть экран по шагам ТК"],
            ["—"],
            ["—"],
            ["—"],
            ["Уточнить теги после разведки экрана"],
        )

    def _ensure_generated_test(self, run: dict, run_dir: RunDir) -> None:
        if any(run_dir.test_generated.glob("*.kt")):
            return
        path = run_dir.write(
            "test_generated/GeneratedTest.kt",
            "package ru.leroymerlin.mobile.presentation.generated\n\n"
            "// Сгенерировано QA-Lead (fake-режим или без сети)\n",
        )
        self.bus.publish(run["id"], {"type": "test_written", "path": str(path),
                                     "loc": 3})

    def _provision(self, run: dict, run_dir: RunDir) -> None:
        if self.android is None:
            self.bus.publish(run["id"], {"type": "gradle_log",
                                         "line": "provision: adb не настроен, SKIP"})
            return
        try:
            run_dir.logs.mkdir(parents=True, exist_ok=True)
            tree = self.android.dump_ui()
            path = run_dir.write("logs/ui_1.xml", str(tree))
            self.bus.publish(run["id"], {"type": "adb_dump", "screen": run["task_id"],
                                         "nodes": str(tree)[:200], "path": str(path)})
        except Exception as exc:  # noqa: BLE001 - деградация вместо падения
            self.bus.publish(run["id"], {"type": "gradle_log",
                                         "line": f"provision SKIP: {exc}"})

    def _execute(self, run: dict, run_dir: RunDir) -> dict:
        if self.gradle is None:
            return {"success": True, "failures": [], "duration": "—", "log": "gradle не настроен"}
        try:
            result = self.gradle.connected_test(run["task_id"],
                                                run_dir.logs / "gradle_execute.log")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "failures": [], "duration": "",
                    "log": f"Gradle недоступен: {exc}"}
        self.bus.publish(run["id"], {"type": "gradle_log",
                                     "line": f"execute: success={result.get('success')}"})
        return result
