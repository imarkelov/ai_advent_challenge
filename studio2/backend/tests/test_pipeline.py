import base64
import json
import shutil
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG
from events import EventBus
from llm import LlmClient
from pipeline.lead import (CriticSameModel, GateError, InvalidTransition, PendingGate,
                           QaLead, STAGES)
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
    with pytest.raises(GateError):
        env["lead"].resume(env["run"]["id"])


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
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    env["lead"].approve_skeleton(env["run"]["id"])
    env["lead"].choose_execution(env["run"]["id"], "generated")
    env["lead"].resume(env["run"]["id"])
    run_root = env["doc"] / "runs" / env["run"]["id"]
    assert (run_root / "review_report.md").exists()
    assert (run_root / "test_report.md").exists()
    assert (run_root / "test_generated").is_dir()


def test_critic_must_differ(env):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    same = cfg["roles"]["testwriter"]["model"]
    cfg["roles"]["reviewer"]["model"] = same
    lead = QaLead(str(env["doc"]), str(env["doc"]), None, None,
                  env["store"], config=cfg)
    with pytest.raises(CriticSameModel):
        lead.validate_critic()


def test_cancel_marks_run_cancelled(env):
    env["lead"].cancel(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["status"] == "cancelled"
