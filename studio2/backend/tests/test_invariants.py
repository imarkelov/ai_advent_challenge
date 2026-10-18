import json
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG
from events import EventBus
from pipeline.lead import CriticSameModel, QaLead
from store import RunStore


def test_reviewer_same_model_is_rejected(tmp_path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["roles"]["reviewer"]["model"] = cfg["roles"]["testwriter"]["model"]
    lead = QaLead(str(tmp_path / "doc"), str(tmp_path / "data"), None,
                  EventBus(), RunStore(str(tmp_path / "data")), config=cfg)
    with pytest.raises(CriticSameModel):
        lead.validate_critic()


def test_default_models_are_different(tmp_path):
    lead = QaLead(str(tmp_path / "doc"), str(tmp_path / "data"), None,
                  EventBus(), RunStore(str(tmp_path / "data")),
                  config=json.loads(json.dumps(DEFAULT_CONFIG)))
    lead.validate_critic()


def test_run_json_has_no_api_key(tmp_path):
    store = RunStore(str(tmp_path / "data"))
    run = store.create_run("TC-1", "C:/repo", "main")
    raw = (tmp_path / "data" / "runs" / f"{run['id']}.json").read_text(encoding="utf-8")
    assert "api_key" not in raw


def test_stage_list_has_no_extra_entries():
    from pipeline.lead import STAGES
    assert len(STAGES) == 9
    assert STAGES[0] == "sdd"
    assert STAGES[-1] == "done"
