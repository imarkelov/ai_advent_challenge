import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import config as cfgmod
from config import load_config
from fastapi.testclient import TestClient
from main import create_app
from pipeline.lead import PendingGate

data_dir = ROOT / "data"
cfg = load_config(str(data_dir))
for role in cfg["roles"]:
    cfg["roles"][role]["provider"] = "fake"
cfgmod.save_config(str(data_dir), cfg)

app = create_app(doc_dir=str(ROOT / "doc"), data_dir=str(data_dir))
client = TestClient(app)

response = client.post("/api/runs", json={
    "task_id": "TC-4217",
    "repo_path": str(ROOT.parent),
    "branch": "develop",
})
run = response.json()["run"]
rid = run["id"]
print("run", rid)

try:
    client.app.state.lead_factory().start(rid)
except PendingGate:
    print("gate reached")

client.post(f"/api/runs/{rid}/approve-skeleton")
client.post(f"/api/runs/{rid}/execution-choice", json={"choice": "generated"})
client.post(f"/api/runs/{rid}/resume")

final = client.get(f"/api/runs/{rid}").json()["run"]
print("stage", final["stage"], "status", final["status"], "verdict", final["verdict"])
(ROOT.parent / ".superpowers/sdd/day35-qa-lead-orchestrator/demo_fake_run_id.txt").write_text(rid, encoding="utf-8")

for path in sorted((ROOT / "doc" / "runs" / rid).rglob("*")):
    if path.is_file():
        print(path.relative_to(ROOT), path.stat().st_size)
