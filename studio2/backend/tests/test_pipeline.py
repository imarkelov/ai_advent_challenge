import base64
import json
import shutil
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG
from events import EventBus
from llm import LlmClient
from pipeline.artifacts import RunDir
from pipeline.lead import (ALLOWED_TRANSITIONS, CriticSameModel, GateError,
                           InvalidTransition, PendingGate, QaLead, STAGES)
from store import RunStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"
_TC = base64.b64decode("VEMtNDIxNw==").decode()


@pytest.fixture()
def env(tmp_path):
    doc = tmp_path / "doc"
    shutil.copytree(REPO_DOC, doc)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    store = RunStore(str(tmp_path / "data"))
    lead = QaLead(str(doc), str(tmp_path / "data"), LlmClient(cfg), EventBus(),
                  store, config=cfg)
    run = store.create_run(_TC, "C:/repo", "develop")
    return {"lead": lead, "store": store, "doc": doc, "run": run, "cfg": cfg}


def test_stage_order_is_fixed():
    assert STAGES == ["sdd", "study", "skeleton", "write", "provision",
                      "execute", "debug", "review", "done"]


def test_forward_transition_allowed(env):
    env["lead"].validate_transition("sdd", "study")


def test_backward_transition_rejected(env):
    with pytest.raises(InvalidTransition):
        env["lead"].validate_transition("execute", "study")


def test_forward_jump_rejected(env):
    with pytest.raises(InvalidTransition):
        env["lead"].validate_transition("sdd", "review")
    with pytest.raises(InvalidTransition):
        env["lead"].validate_transition("sdd", "done")


def test_round2_allowed_from_review(env):
    env["lead"].validate_transition("review", "study")


def test_third_round_rejected(env):
    run = dict(env["run"], stage="review", round=2)
    with pytest.raises(InvalidTransition):
        env["lead"].next_stage("study", run)


def test_write_requires_skeleton_approval(env):
    with pytest.raises(GateError):
        env["lead"].ensure_gate("write", env["run"])


def test_provision_requires_execution_choice(env):
    run = dict(env["run"], skeleton_approved=True, stage="write")
    with pytest.raises(GateError):
        env["lead"].ensure_gate("provision", run)


def test_gates_pass_when_satisfied(env):
    run = dict(env["run"], skeleton_approved=True, execution_choice="generated")
    env["lead"].ensure_gate("write", run)
    env["lead"].ensure_gate("provision", run)


def test_approve_skeleton_updates_run(env):
    env["lead"].approve_skeleton(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["skeleton_approved"] is True


def test_choose_execution_rejects_unknown_choice(env):
    with pytest.raises(ValueError):
        env["lead"].choose_execution(env["run"]["id"], "magic")


def test_start_writes_sdd_and_stops_at_gate(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    sdd_dir = env["doc"] / "runs" / env["run"]["id"] / "sdd"
    files = {p.name for p in sdd_dir.glob("*.md")}
    assert {"agent_qa-lead.md", "agent_researcher.md",
            "agent_testwriter.md", "agent_reviewer.md"} <= files
    assert (env["doc"] / "runs" / env["run"]["id"] / "skeleton.md").exists()
    run = env["store"].get_run(env["run"]["id"])
    assert run["stage"] == "skeleton"
    assert run["skeleton_approved"] is False


def test_resume_requires_gates(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    with pytest.raises(InvalidTransition):
        env["lead"].resume(env["run"]["id"])


def test_gate_order_write_then_provision(env):
    current = env["run"]["id"]
    with pytest.raises(PendingGate):
        env["lead"].start(current)
    env["lead"].approve_skeleton(current)
    stages = []
    original = env["lead"]._emit_stage

    def spy(run_id, stage, round_no):
        stages.append(stage)
        original(run_id, stage, round_no)

    env["lead"]._emit_stage = spy
    with pytest.raises(GateError):
        env["lead"].resume(current)
    assert stages == ["write"]
    run = env["store"].get_run(env["run"]["id"])
    assert run["stage"] == "write"
    assert run["status"] == "running"


def test_start_twice_is_rejected(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    with pytest.raises(InvalidTransition):
        env["lead"].start(env["run"]["id"])


def test_full_run_reaches_done_with_fake_llm(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    env["lead"].approve_skeleton(env["run"]["id"])
    env["lead"].choose_execution(env["run"]["id"], "generated")
    env["lead"].resume(env["run"]["id"])
    run = env["store"].get_run(env["run"]["id"])
    assert run["status"] == "done"
    assert run["verdict"] == "green"
    assert STAGES.index(run["stage"]) == STAGES.index("done")


def test_full_run_writes_reports(env):
    lead = _scripted_env(env, {})
    lead.gradle = type("G", (), {"connected_test": staticmethod(
        lambda c, l: {"success": True, "failures": [], "duration": "1s", "log": ""})})()
    with pytest.raises(PendingGate):
        lead.start(env["run"]["id"])
    lead.approve_skeleton(env["run"]["id"])
    lead.choose_execution(env["run"]["id"], "as_loaded")
    lead.resume(env["run"]["id"])
    assert "reviewer" in lead.llm.calls
    run_root = env["doc"] / "runs" / env["run"]["id"]
    assert (run_root / "review_report.md").exists()
    assert (run_root / "test_report.md").exists()
    assert (run_root / "test_generated").is_dir()


class ScriptedLlm:
    def __init__(self, replies: dict[str, list[str]]):
        self.replies = replies
        self.calls: list[str] = []

    def stream(self, role, messages, system=None):
        self.calls.append(role)
        queue = self.replies.get(role) or ['{"status": "done", "artifacts": [], '
                                           '"summary": "ok", "question": null}']
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        for i in range(0, len(text), 24):
            yield text[i:i + 24]

    def chat(self, role, messages, system=None):
        return "".join(self.stream(role, messages, system))


def _scripted_env(env, replies):
    lead = QaLead(str(env["doc"]), str(env["doc"] / ".." / "data"), ScriptedLlm(replies),
                  EventBus(), env["store"], config=env["cfg"])
    return lead


def _start_and_gate(lead, run_id):
    with pytest.raises(PendingGate):
        lead.start(run_id)
    lead.approve_skeleton(run_id)
    lead.choose_execution(run_id, "generated")


def test_generated_choice_skips_reviewer(env):
    lead = _scripted_env(env, {})
    _start_and_gate(lead, env["run"]["id"])
    lead.resume(env["run"]["id"])
    run = env["store"].get_run(env["run"]["id"])
    assert run["status"] == "done"
    assert run["verdict"] == "green"
    assert "reviewer" not in lead.llm.calls
    assert not (env["doc"] / "runs" / env["run"]["id"] / "review_report.md").exists()


def test_red_then_round2_then_fail(env):
    lead = _scripted_env(env, {"reviewer": ['{"status": "done", "artifacts": '
                                            '["review_report.md"], "summary": "red"}\n']})

    def failing(class_name, log_path):
        return {"success": False, "failures": [{"test": class_name, "method": "run"}],
                "duration": "1s", "log": "FAILED"}

    lead.gradle = type("G", (), {"connected_test": staticmethod(failing)})()
    run_id = env["run"]["id"]
    with pytest.raises(PendingGate):
        lead.start(run_id)
    lead.approve_skeleton(run_id)
    lead.choose_execution(run_id, "as_loaded")
    lead.resume(run_id)
    run = env["store"].get_run(run_id)
    assert run["round"] == 2
    assert run["status"] == "failed"
    assert run["verdict"] == "red"
    assert lead.llm.calls.count("researcher") >= 2
    assert run["stage"] == "done"


def test_generated_failure_stops_after_two_attempts(env):
    lead = _scripted_env(env, {
        "debugger": ['{"status": "done", "artifacts": [], "summary": "исправлено"}\n'],
    })
    attempts = {"n": 0}

    def failing(class_name, log_path):
        attempts["n"] += 1
        return {"success": False, "failures": [], "duration": "1s", "log": "FAILED"}

    lead.gradle = type("G", (), {"connected_test": staticmethod(failing)})()
    _start_and_gate(lead, env["run"]["id"])
    lead.resume(env["run"]["id"])
    assert attempts["n"] == 4
    assert lead.llm.calls.count("debugger") == 4
    assert "reviewer" not in lead.llm.calls
    run = env["store"].get_run(env["run"]["id"])
    assert run["round"] == 2
    assert run["status"] == "failed"
    assert run["verdict"] == "red"


def test_debugger_product_bug_fails_without_rerun(env):
    lead = _scripted_env(env, {
        "debugger": ['{"status": "fail", "artifacts": [], "summary": "баг продукта"}\n'],
    })
    attempts = {"n": 0}

    def failing(class_name, log_path):
        attempts["n"] += 1
        return {"success": False, "failures": [], "duration": "1s", "log": "FAILED"}

    lead.gradle = type("G", (), {"connected_test": staticmethod(failing)})()
    _start_and_gate(lead, env["run"]["id"])
    lead.resume(env["run"]["id"])
    assert attempts["n"] == 1
    assert env["store"].get_run(env["run"]["id"])["status"] == "failed"


def test_artifacts_outside_write_scope_are_dropped(env):
    lead = _scripted_env(env, {
        "testwriter": ['{"status": "done", "artifacts": '
                       '["kb/architecture.md", "test_generated/GeneratedX.kt"], '
                       '"summary": "ok"}\n'],
    })
    _start_and_gate(lead, env["run"]["id"])
    lead.resume(env["run"]["id"])
    log = env["store"].get_run(env["run"]["id"])["log"]
    messages = [row["message"] for row in log if row["role"] == "testwriter"]
    assert any("write_scope" in message and "kb/architecture.md" in message
               for message in messages)
    assert any("test_generated/GeneratedX.kt" in message for message in messages)


def test_guard_tools_rejects_unknown_tool(env):
    from pipeline.subagents import ToolNotAllowed, guard_tools
    spec = env["lead"].agents["testwriter"]
    guard_tools(spec, [{"name": "doc.read"}])
    with pytest.raises(ToolNotAllowed):
        guard_tools(spec, [{"name": "gradle.run"}])


def test_need_question_waits_and_reruns(env):
    lead = _scripted_env(env, {
        "researcher": [
            '{"status": "need_question", "artifacts": [], "summary": "", '
            '"question": {"text": "Какой экран?"}}\n',
            '{"status": "done", "artifacts": ["kb/architecture.md"], "summary": "ok"}\n',
        ],
    })
    lead.bus.answer = lambda qid, text: True
    lead.bus.wait_for_answer_sync = lambda qid, timeout=1800: "AddNewAddress"
    _start_and_gate(lead, env["run"]["id"])
    lead.resume(env["run"]["id"])
    assert lead.llm.calls.count("researcher") >= 2
    assert lead.facts[env["run"]["id"]] == ["Какой экран? → AddNewAddress"]


def test_need_question_without_answer_fails_stage(env):
    lead = _scripted_env(env, {
        "researcher": ['{"status": "need_question", "artifacts": [], "summary": "", '
                       '"question": {"text": "Какой экран?"}}\n'],
    })
    lead.bus.wait_for_answer_sync = lambda qid, timeout=1800: None
    run = env["run"]
    reply = lead._run_stage_agent("study", run, RunDir(str(env["doc"]), run["id"]))
    assert reply.status == "fail"
    log = [row["message"] for row in env["store"].get_run(run["id"])["log"]]
    assert any("не получен" in message for message in log)


def test_critic_must_differ(env):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    same = cfg["roles"]["testwriter"]["model"]
    cfg["roles"]["reviewer"]["model"] = same
    lead = QaLead(str(env["doc"]), str(env["doc"]), None, None,
                  env["store"], config=cfg)
    with pytest.raises(CriticSameModel):
        lead.validate_critic()


def test_questions_in_one_run_get_distinct_ids(env):
    lead = _scripted_env(env, {
        "researcher": [
            '{"status": "need_question", "artifacts": [], "summary": "", '
            '"question": {"text": "Какой экран?"}}\n',
            '{"status": "done", "artifacts": [], "summary": "ok"}\n',
        ],
        "testwriter": [
            '{"status": "need_question", "artifacts": [], "summary": "", '
            '"question": {"text": "Какой тег?"}}\n',
            '{"status": "done", "artifacts": [], "summary": "ok"}\n',
        ],
    })
    ids: list[str] = []
    original_register = lead.bus.register_question

    def register(qid):
        ids.append(qid)
        original_register(qid)

    lead.bus.register_question = register
    lead.bus.wait_for_answer_sync = lambda qid, timeout=1800: "ответ"
    run = env["run"]
    run_dir = RunDir(str(env["doc"]), run["id"])
    lead._run_stage_agent("study", run, run_dir)
    lead._run_stage_agent("skeleton", run, run_dir)
    assert len(ids) == 2
    assert len(set(ids)) == 2
    assert ids[0].startswith(f"{run['id']}-researcher-")
    assert ids[1].startswith(f"{run['id']}-testwriter-")


def test_cancel_marks_run_cancelled(env):
    env["lead"].cancel(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["status"] == "cancelled"


def test_cancelled_run_blocks_start_for_new_lead_instance(env):
    run_id = env["run"]["id"]
    env["store"].update_run(run_id, status="cancelled")
    fresh = _scripted_env(env, {})
    with pytest.raises(InvalidTransition, match="отменён"):
        fresh.start(run_id)


def test_cancel_stops_in_flight_run(env):
    lead = _scripted_env(env, {
        "researcher": [
            '{"status": "need_question", "artifacts": [], "summary": "", '
            '"question": {"text": "Какой экран?"}}\n',
        ],
    })

    def wait(qid, timeout=1800):
        env["store"].update_run(env["run"]["id"], status="cancelled")
        return None

    lead.bus.wait_for_answer_sync = wait
    with pytest.raises(InvalidTransition, match="отменён"):
        lead.start(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["status"] == "cancelled"


def _stage_writes(store):
    """Смены stage в обход `_emit_stage` (round меняется в `_next_round` намеренно)."""
    writes: list[tuple] = []
    state = {"emit": 0}
    original = store.save_run
    current = store.get_run

    def save_run(run):
        before = current(run["id"]) or {}
        if state["emit"]:
            state["emit"] = 0
        elif before.get("stage") != run.get("stage"):
            writes.append((before.get("stage"), before.get("round"),
                           run.get("stage"), run.get("round")))
        return original(run)

    store.save_run = save_run
    return writes, state


def test_review_round2_routes_stage_through_emit(env):
    lead = _scripted_env(env, {
        "reviewer": ['{"status": "done", "artifacts": ["review_report.md"], '
                     '"summary": "red"}\n'],
    })

    def failing(class_name, log_path):
        return {"success": False, "failures": [], "duration": "1s", "log": "FAILED"}

    lead.gradle = type("G", (), {"connected_test": staticmethod(failing)})()
    stages: list[tuple[str, int]] = []
    events: list[dict] = []
    validated: list[tuple[str, str]] = []
    state = {"emit": 0}
    original_validate = lead.validate_transition
    original_publish = lead.bus.publish
    original_update = lead.store.update_run

    def update_run(run_id, **fields):
        if "stage" in fields:
            state["emit"] += 1
        return original_update(run_id, **fields)

    def validate(current, nxt):
        validated.append((current, nxt))
        original_validate(current, nxt)

    def publish(run_id, event):
        events.append(event)
        original_publish(run_id, event)

    def emit(run_id, stage, round_no):
        current = lead.store.get_run(run_id).get("stage", "")
        original_validate(current, stage)
        stages.append((stage, round_no))
        lead.store.update_run(run_id, stage=stage, round=round_no)
        lead.bus.publish(run_id, {"type": "stage_changed", "stage": stage,
                                  "round": round_no})

    run_id = env["run"]["id"]
    _start_and_gate(lead, run_id)
    lead.store.update_run = update_run
    lead._emit_stage = emit
    lead.validate_transition = validate
    lead.bus.publish = publish
    writes, state = _stage_writes(env["store"])
    lead.resume(run_id)

    assert writes == []
    assert ("execute", "review") in ALLOWED_TRANSITIONS
    assert ("debug", "review") in ALLOWED_TRANSITIONS
    assert ("review", "study") in ALLOWED_TRANSITIONS
    assert ("study", "skeleton") in ALLOWED_TRANSITIONS
    assert ("review", 1) in stages and ("review", 2) in stages
    assert ("study", 2) in stages and ("execute", 2) in stages
    assert ("review", "study") in validated
    published = [(event["stage"], event["round"]) for event in events
                 if event.get("type") == "stage_changed"]
    assert published == stages
    run = env["store"].get_run(run_id)
    assert run["round"] == 2
    assert run["stage"] == "done"
    assert run["status"] == "failed"
    assert run["verdict"] == "red"


def test_review_cannot_greenlight_failed_gradle(env):
    lead = _scripted_env(env, {
        "reviewer": ['{"status": "done", "artifacts": ["review_report.md"], '
                     '"summary": "green"}\n'],
    })

    def failing(class_name, log_path):
        return {"success": False, "failures": [], "duration": "1s", "log": "FAILED"}

    lead.gradle = type("G", (), {"connected_test": staticmethod(failing)})()
    run_id = env["run"]["id"]
    with pytest.raises(PendingGate):
        lead.start(run_id)
    lead.approve_skeleton(run_id)
    lead.choose_execution(run_id, "as_loaded")
    lead.resume(run_id)
    assert "reviewer" in lead.llm.calls
    run = env["store"].get_run(run_id)
    assert run["verdict"] == "red"
    assert run["status"] == "failed"
    report = (env["doc"] / "runs" / run_id / "review_report.md").read_text(encoding="utf-8")
    assert "**Вердикт:** red" in report


def test_reviewer_rejection_is_red(env):
    lead = _scripted_env(env, {
        "reviewer": ['{"status": "done", "artifacts": ["review_report.md"], '
                     '"summary": "тест не соответствует ТК, red"}\n'],
    })

    def passing(class_name, log_path):
        return {"success": True, "failures": [], "duration": "1s", "log": ""}

    lead.gradle = type("G", (), {"connected_test": staticmethod(passing)})()
    run_id = env["run"]["id"]
    with pytest.raises(PendingGate):
        lead.start(run_id)
    lead.approve_skeleton(run_id)
    lead.choose_execution(run_id, "as_loaded")
    lead.resume(run_id)
    run = env["store"].get_run(run_id)
    assert run["verdict"] == "red"
    assert run["status"] == "failed"
