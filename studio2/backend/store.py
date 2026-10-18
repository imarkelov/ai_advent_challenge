"""JSON-хранилище запусков: один файл на запуск."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

LOG_CAP = 500


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RunStore:
    def __init__(self, data_dir: str):
        self.runs_dir = Path(data_dir) / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def create_run(self, task_id: str, repo_path: str, branch: str) -> dict:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run = {
            "id": run_id,
            "task_id": task_id,
            "repo_path": repo_path,
            "branch": branch,
            "stage": "sdd",
            "status": "running",
            "round": 1,
            "created_at": _now(),
            "seq": self._next_seq(),
            "updated_at": _now(),
            "skeleton_approved": False,
            "execution_choice": None,
            "verdict": None,
            "log": [],
        }
        return self.save_run(run)

    def save_run(self, run: dict) -> dict:
        run["updated_at"] = _now()
        self._path(run["id"]).write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run

    def get_run(self, run_id: str) -> dict | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_run(self, run_id: str, **fields) -> dict:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        run.update(fields)
        return self.save_run(run)

    def list_runs(self) -> list[dict]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(
            runs,
            key=lambda run: (run.get("created_at", ""), run.get("seq", 0), run["id"]),
            reverse=True,
        )

    def append_log(self, run_id: str, role: str, message: str) -> dict:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        log = run.setdefault("log", [])
        log.append({"at": _now(), "role": role, "message": message})
        run["log"] = log[-LOG_CAP:]
        return self.save_run(run)
