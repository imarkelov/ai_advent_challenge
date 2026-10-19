"""Оркестратор QA-Lead: стадийная машина, гейты, раунды, ревью отдельной LLM."""
from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from config import load_config
from pipeline.artifacts import (RunDir, review_markdown, sdd_text, skeleton_markdown,
                                test_report_markdown)
from pipeline.subagents import AgentReply, guard_tools, run_subagent
from roles import load_agents

STAGES = ["sdd", "study", "skeleton", "write", "provision",
          "execute", "debug", "review", "done"]

MAX_ROUNDS = 2
MAX_EXECUTE_ATTEMPTS = 2
QUESTION_TIMEOUT = 1800

# Разрешены только соседние переходы вперёд (+ возврат review → study для круга 2).
ALLOWED_TRANSITIONS = {
    ("sdd", "study"),
    ("study", "skeleton"),
    ("skeleton", "write"),
    ("write", "provision"),
    ("provision", "execute"),
    ("execute", "debug"),
    ("execute", "review"),
    ("execute", "execute"),
    ("execute", "done"),
    ("debug", "execute"),
    ("debug", "review"),
    ("review", "study"),
    ("review", "done"),
}

TEST_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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
        self.facts: dict[str, list[str]] = {}

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
        if current not in STAGES or nxt not in STAGES:
            raise InvalidTransition(f"неизвестная стадия: {current} → {nxt}")
        if (current, nxt) not in ALLOWED_TRANSITIONS:
            raise InvalidTransition(f"запрещённый переход: {current} → {nxt}")

    def next_stage(self, nxt: str, run: dict) -> str:
        self.validate_transition(run["stage"], nxt)
        if nxt == "study" and run.get("round", 1) >= MAX_ROUNDS:
            raise InvalidTransition("третий круг изучения запрещён")
        return nxt

    def ensure_gate(self, stage: str, run: dict) -> None:
        if stage == "write" and not run.get("skeleton_approved"):
            raise GateError("переход в write запрещён: каркас не утверждён")
        if stage == "provision" and not run.get("execution_choice"):
            raise GateError("переход в provision запрещён: способ исполнения не выбран")

    def generated_test_class(self, run: dict) -> str:
        name = f"Generated{str(run['task_id']).replace('-', '')}Test"
        if not TEST_CLASS_RE.match(name):
            raise ValueError(f"недопустимое имя тестового класса: {name}")
        return name

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
        self.store.update_run(run_id, status="cancelled")
        self.bus.publish(run_id, {"type": "run_done", "status": "cancelled",
                                  "report_path": ""})
        self.bus.close(run_id)

    # ---------- основной цикл ----------

    def start(self, run_id: str) -> None:
        run = self._require(run_id)
        if run.get("status") != "running" or run.get("stage") != "sdd":
            raise InvalidTransition(
                f"запуск уже стартовал: стадия {run.get('stage')}, статус {run.get('status')}"
            )
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
        if not run.get("skeleton_approved") or run.get("stage") not in ("skeleton", "write"):
            raise InvalidTransition(
                "возобновление возможно только после утверждения каркаса"
            )
        run_dir = RunDir(self.doc_dir, run_id)
        run_dir.ensure()
        self._tail(run_id, run_dir)

    def _tail(self, run_id: str, run_dir: RunDir) -> None:
        """write → provision → execute → debug → review для текущего круга."""
        run = self._require(run_id)
        round_no = run.get("round", 1)

        self.ensure_gate("write", run)
        self._emit_stage(run_id, "write", round_no)
        self._run_stage_agent("write", run, run_dir)
        self._ensure_generated_test(run, run_dir)

        run = self._require(run_id)
        self.ensure_gate("provision", run)
        self._emit_stage(run_id, "provision", round_no)
        self._provision(run, run_dir)

        result = self._execute_loop(run_id, run_dir)
        fail_verdict_published = False
        from_loaded = self.gradle is not None and run.get("execution_choice") == "as_loaded"
        if result["success"] and not from_loaded:
            verdict = "green"
        elif from_loaded:
            verdict = self._review(run_id, run_dir, result)
            fail_verdict_published = True
        else:
            verdict = "red"

        run = self._require(run_id, allow_done=True)
        if verdict == "green":
            self._finish(run_id, run_dir, "green")
            return
        if run.get("stage") != "review":
            self._emit_stage(run_id, "review", round_no)
        if not fail_verdict_published and not result.get("product_bug"):
            self.bus.publish(run_id, {
                "type": "review_verdict",
                "verdict": "red",
                "path": "review_report.md",
            })
        if run.get("round", 1) < MAX_ROUNDS and not result.get("product_bug"):
            self._next_round(run_id, run_dir, result)
            return
        self._finish(run_id, run_dir, "red")

    def _next_round(self, run_id: str, run_dir: RunDir, result: dict) -> None:
        run = self._require(run_id)
        run.update(round=run.get("round", 1) + 1)
        self.store.save_run(run)
        self.store.append_log(run_id, "qa-lead",
                              f"вердикт red — круг {run['round']}: повторное изучение")
        self.bus.publish(run_id, {"type": "review_verdict", "verdict": "red",
                                  "path": "review_report.md"})
        self.validate_transition(run.get("stage"), "study")
        self._emit_stage(run_id, "study", run["round"])
        self._run_stage_agent("study", run, run_dir, context={
            "Предыдущий прогон": str(result)[:2000],
            "Новый запрос": "Круг 2: найди недостающие факты для падающего теста",
        })
        # study → skeleton уже пройден: утверждение каркаса действует, идём в write.
        self.store.update_run(run_id, stage="skeleton")
        self._tail(run_id, run_dir)

    def _finish(self, run_id: str, run_dir: RunDir, verdict: str) -> None:
        run = self._require(run_id, allow_done=True)
        stages = STAGES[:STAGES.index(run["stage"]) + 1] + ["done"]
        status = "done" if verdict == "green" else "failed"
        run_dir.write("test_report.md", test_report_markdown(
            run_id, run["task_id"], run["branch"], stages, verdict,
            ["skeleton.md", "review_report.md", "test_report.md"],
            "Пайплайн завершён. См. review_report.md и test_generated/.",
        ))
        self._emit_stage(run_id, "done", run.get("round", 1))
        self.store.update_run(run_id, status=status, verdict=verdict)
        self.bus.publish(run_id, {"type": "run_done", "status": status,
                                  "report_path": "test_report.md"})
        self.bus.close(run_id)

    # ---------- стадии ----------

    def _require(self, run_id: str, allow_done: bool = False) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        if run.get("status") == "cancelled":
            raise InvalidTransition("запуск отменён")
        if not allow_done and run.get("status") != "running":
            raise InvalidTransition(f"запуск не выполняется: статус {run.get('status')}")
        return run

    def _emit_stage(self, run_id: str, stage: str, round_no: int) -> None:
        run = self.store.get_run(run_id)
        if run is not None:
            self.validate_transition(run.get("stage", ""), stage)
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
                goal=spec.body[:1500],
                rag_inputs=", ".join(spec.inputs),
                artifacts="—",
                instructions=(
                    f"Роль: {spec.display_name or role}. "
                    "Следуй телу декларации агента и верни ответ строго по формату ниже."
                ),
                allowed_tools=", ".join(spec.allowed_tools),
                max_rounds=spec.max_rounds,
                write_scope=", ".join(spec.outputs) or "—",
                criteria="- [ ] статус done в JSON-ответе",
            )
            run_dir.write(f"sdd/agent_{role}.md", text)
            self.bus.publish(run["id"], {"type": "sdd_written", "role": role,
                                         "path": f"sdd/agent_{role}.md", "title": role})

    def _run_stage_agent(self, stage: str, run: dict, run_dir: RunDir,
                         context: dict | None = None) -> AgentReply:
        role = _STAGE_ROLE[stage]
        spec = self.agents[role]
        ctx: dict = dict(context or {})
        ctx.update({f"Уточнение {i + 1}": fact
                    for i, fact in enumerate(self.facts.get(run["id"], []))})
        if self.rag is not None:
            try:
                hits = self.rag.search(run["task_id"], limit=5)
                ctx["RAG"] = "\n".join(f"- {hit['path']}: {hit['snippet']}" for hit in hits)
            except Exception as exc:  # noqa: BLE001
                ctx["RAG"] = f"RAG недоступен: {exc}"
        guard_tools(spec, ["doc.read", "rag.search"])
        reply = run_subagent(role, spec, self.llm, run_dir, ctx, self.bus, run["id"])
        reply = self._resolve_question(run, role, spec, run_dir, ctx, reply)
        valid, dropped = self._filter_artifacts(spec, reply.artifacts)
        reply.artifacts = valid
        for path in dropped:
            self.store.append_log(run["id"], role,
                                  f"артефакт вне write_scope отклонён: {path}")
        self.store.append_log(run["id"], role, reply.summary or reply.status)
        for path in valid:
            self.store.append_log(run["id"], role, f"артефакт: {path}")
        return reply

    def _filter_artifacts(self, spec, artifacts: list[str]) -> tuple[list[str], list[str]]:
        valid: list[str] = []
        dropped: list[str] = []
        for path in artifacts:
            clean = str(path).replace("\\", "/").strip("/")
            if any(clean == prefix or clean.startswith(prefix.rstrip("/") + "/")
                   for prefix in spec.outputs):
                valid.append(clean)
            else:
                dropped.append(str(path))
        return valid, dropped

    def _resolve_question(self, run: dict, role: str, spec, run_dir: RunDir,
                          ctx: dict, reply: AgentReply) -> AgentReply:
        if reply.status != "need_question":
            return reply
        qid = f"{run['id']}-{role}-{uuid4().hex[:6]}"
        question = reply.question or {}
        text = question.get("text", "нужна уточняющая информация")
        try:
            self.bus.register_question(qid)
        except AttributeError:
            return reply
        self.bus.publish(run["id"], {
            "type": "question",
            "question_id": qid,
            "role": role,
            "text": text,
            "options": question.get("options", []),
        })
        answer = self._wait_answer(qid)
        if answer is None:
            self.store.append_log(run["id"], role, "ответ на вопрос не получен")
            reply.status = "fail"
            reply.summary = reply.summary or "ответ на вопрос не получен"
            return reply
        self.store.append_log(run["id"], role, f"ответ человека: {answer}")
        self.facts.setdefault(run["id"], []).append(f"{text} → {answer}")
        new_ctx = dict(ctx)
        new_ctx["Ответ человека"] = answer
        guarded = run_subagent(role, spec, self.llm, run_dir, new_ctx,
                               self.bus, run["id"])
        return guarded

    def _wait_answer(self, qid: str) -> str | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            future = asyncio.run_coroutine_threadsafe(
                self.bus.wait_for_answer(qid, timeout=QUESTION_TIMEOUT), loop)
            return future.result()
        return self.bus.wait_for_answer_sync(qid, timeout=QUESTION_TIMEOUT)

    def _default_skeleton(self, run: dict) -> str:
        return skeleton_markdown(
            self.generated_test_class(run),
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
            f"test_generated/{self.generated_test_class(run)}.kt",
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

    def _execute_loop(self, run_id: str, run_dir: RunDir) -> dict:
        round_no = self.store.get_run(run_id).get("round", 1)
        result: dict = {}
        for attempt in range(1, MAX_EXECUTE_ATTEMPTS + 1):
            run = self._require(run_id)
            self._emit_stage(run_id, "execute", round_no)
            result = self._execute(run, run_dir)
            if result["success"]:
                return result
            self._emit_stage(run_id, "debug", round_no)
            run = self._require(run_id)
            if self.gradle is None:
                return result
            reply = self._run_stage_agent("debug", run, run_dir, context={
                "Gradle log": result.get("log", "")[:4000],
                "Попытка": str(attempt),
            })
            if reply.status == "fail":
                self.store.append_log(run_id, "debugger",
                                      f"реальный баг продукта: {reply.summary}")
                return dict(result, product_bug=True)
            if attempt == MAX_EXECUTE_ATTEMPTS:
                break
        return result

    def _execute(self, run: dict, run_dir: RunDir) -> dict:
        if self.gradle is None:
            return {"success": True, "failures": [], "duration": "—", "log": "gradle не настроен"}
        try:
            class_name = self.generated_test_class(run)
            if not TEST_CLASS_RE.match(class_name):
                raise ValueError(f"недопустимое имя тестового класса: {class_name}")
            run_dir.logs.mkdir(parents=True, exist_ok=True)
            result = self.gradle.connected_test(class_name,
                                                run_dir.logs / "gradle_execute.log")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "failures": [], "duration": "",
                    "log": f"Gradle недоступен: {exc}"}
        self.bus.publish(run["id"], {"type": "gradle_log",
                                     "line": f"execute: success={result.get('success')}"})
        return result

    def _review(self, run_id: str, run_dir: RunDir, result: dict) -> str:
        run = self._require(run_id)
        round_no = run.get("round", 1)
        self._emit_stage(run_id, "review", round_no)
        reply = self._run_stage_agent("review", run, run_dir, context={
            "Gradle result": str(result),
        })
        # Жёсткое правило: непрошедший Gradle — всегда red, вердикт ревьюера не
        # может «позеленить» упавший прогон. Зелёный — только Gradle passed
        # и ревьюер не отклонил.
        verdict = "red" if not result.get("success") else self._verdict_from_reply(reply)
        self.store.append_log(run_id, "reviewer", f"вердикт: {verdict}")
        self.bus.publish(run_id, {"type": "review_verdict", "verdict": verdict,
                                  "path": "review_report.md"})
        self._write_review(run_id, run_dir, result, verdict)
        return verdict

    def _write_review(self, run_id: str, run_dir: RunDir, result: dict,
                      verdict: str) -> None:
        details = [
            f"1. gradle: {'passed' if result.get('success') else 'failed'}",
            f"2. стабильность: {'stable' if result.get('success') else 'flaky'}",
            "3. соответствие ТК: matches",
            "4. ссылка на ТК: valid",
            f"вердикт: {verdict}",
        ]
        rows = [{"test": row.get("test", ""),
                 "status": "passed" if result.get("success") else "failed",
                 "duration": result.get("duration", ""),
                 "reason": row.get("method", "")} for row in result.get("failures", [])]
        if not rows:
            rows = [{"test": "загруженные тесты", "status": "passed",
                     "duration": result.get("duration", ""), "reason": ""}]
        run_dir.write("review_report.md", review_markdown(run_id, rows, verdict, details))

    @staticmethod
    def _verdict_from_reply(reply: AgentReply) -> str:
        text = (reply.summary or "").lower()
        for token in ("green", "red"):
            if token in text:
                return token
        if reply.status == "fail":
            return "red"
        return "green"
