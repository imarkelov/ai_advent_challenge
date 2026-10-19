"""FastAPI-приложение studio2: конфиг, документы, RAG, задачи, MCP."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import load_config, mask_secret, save_config
from docstore import DocPathError, DocStore
from events import EventBus
from llm import LlmClient, LlmError
from mcp import McpError, McpRegistry, McpServerStore
from pipeline.lead import GateError, PendingGate, QaLead
from rag import RagIndex
from store import RunStore

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DOC = BACKEND_DIR.parent / "doc"
DEFAULT_DATA = BACKEND_DIR.parent / "data"
SETTINGS_FIELDS = ("providers", "roles", "critic", "android")


def _public_config(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    for provider in out["providers"].values():
        raw = provider.pop("api_key", None)
        provider["api_key_masked"] = mask_secret(raw)
    return out


def _merge_config(current: dict, updates: dict) -> dict:
    for key, value in updates.items():
        if key in SETTINGS_FIELDS and isinstance(value, dict):
            for name, fields in value.items():
                if isinstance(fields, dict):
                    current[key].setdefault(name, {}).update(fields)
                else:
                    current[key][name] = fields
        else:
            current[key] = value
    return current


def _parse_task(text: str, path: Path) -> dict:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        head, _, _body = rest.partition("\n---")
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            if key.strip():
                meta[key.strip()] = value.strip()
    meta.setdefault("id", path.stem)
    meta.setdefault("title", meta["id"])
    meta.setdefault("status", "ready")
    return meta


def create_app(doc_dir: str | None = None, data_dir: str | None = None,
               llm: LlmClient | None = None) -> FastAPI:
    doc_root = str(doc_dir or DEFAULT_DOC)
    data_root = str(data_dir or DEFAULT_DATA)
    docs = DocStore(doc_root)
    rag = RagIndex(doc_root)
    rag.reindex()
    mcp_store = McpServerStore(data_root)
    mcp_registry = McpRegistry(mcp_store)
    app = FastAPI(title="QA-Lead Studio")
    bus = EventBus()
    runs = RunStore(data_root)
    app.state.bus = bus
    app.state.runs = runs

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def client() -> LlmClient:
        nonlocal llm
        if llm is None:
            llm = LlmClient(load_config(data_root))
        return llm

    app.state.lead_factory = lambda: QaLead(
        doc_root, data_root, client(), bus, runs,
        config=load_config(data_root),
        rag=rag,
    )

    @app.on_event("shutdown")
    def _shutdown() -> None:
        mcp_registry.close_all()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": "day35"}

    @app.get("/api/config")
    def get_config() -> dict:
        return _public_config(load_config(data_root))

    @app.post("/api/config")
    def post_config(body: dict) -> dict:
        cfg = load_config(data_root)
        merged = _merge_config(cfg, body)
        if merged.get("critic", {}).get("must_differ_from_author", True):
            if merged["roles"]["reviewer"]["model"] == merged["roles"]["testwriter"]["model"]:
                raise HTTPException(
                    400, "модель ревьюера должна отличаться от модели тестировщика"
                )
        for provider in merged["providers"].values():
            provider.pop("api_key_masked", None)
        save_config(data_root, merged)
        nonlocal llm
        llm = None
        return _public_config(merged)

    @app.get("/api/models")
    def models(provider: str = "ollama") -> dict:
        try:
            return {"provider": provider, "models": client().list_models(provider)}
        except LlmError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/models/test")
    def test_model(body: dict) -> dict:
        role = body.get("role")
        if role not in load_config(data_root)["roles"]:
            raise HTTPException(400, "неизвестная роль")
        return client().test_model(role)

    @app.get("/api/doc/tree")
    def doc_tree() -> dict:
        return {"tree": docs.tree()}

    @app.get("/api/doc/file")
    def doc_read(path: str) -> dict:
        try:
            return {"path": path, "content": docs.read(path)}
        except DocPathError as exc:
            code = 404 if "не найден" in str(exc) else 400
            raise HTTPException(code, str(exc)) from exc

    @app.post("/api/doc/file")
    def doc_write(body: dict) -> dict:
        try:
            docs.write(body.get("path", ""), body.get("content", ""))
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": body.get("path"), "saved": True}

    @app.post("/api/doc/upload")
    async def doc_upload(dir: str = Form("kb"), file: UploadFile = File(...)) -> dict:
        data = await file.read()
        try:
            rel = docs.upload(dir, file.filename or "upload.md", data)
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": rel, "bytes": len(data)}

    @app.delete("/api/doc/file")
    def doc_delete(path: str) -> dict:
        try:
            docs.delete(path)
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": path}

    @app.post("/api/rag/reindex")
    def rag_reindex() -> dict:
        return {"documents": rag.reindex(), "stats": rag.stats()}

    @app.get("/api/rag/search")
    def rag_search(q: str, limit: int = 5) -> dict:
        return {"query": q, "hits": rag.search(q, limit=max(1, min(limit, 20)))}

    @app.get("/api/tasks")
    def tasks() -> dict:
        items = [
            _parse_task(path.read_text(encoding="utf-8"), path)
            for path in sorted((Path(doc_root) / "tasks").glob("*.md"))
        ]
        return {"tasks": items}

    @app.get("/api/mcp/servers")
    def mcp_servers() -> dict:
        servers = []
        for server in mcp_store.list():
            servers.append({**server, "status": mcp_registry.status(server["id"])})
        return {"servers": servers}

    @app.post("/api/mcp/servers", status_code=201)
    def mcp_add(body: dict) -> dict:
        try:
            server = mcp_store.add(body)
        except McpError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"server": {**server, "status": mcp_registry.status(server["id"])}}

    @app.delete("/api/mcp/servers/{sid}")
    def mcp_delete(sid: str) -> dict:
        try:
            mcp_registry.disconnect(sid)
            mcp_store.delete(sid)
        except McpError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": sid}

    @app.post("/api/mcp/servers/{sid}/connect")
    def mcp_connect(sid: str) -> dict:
        try:
            result = mcp_registry.connect(sid)
        except McpError as exc:
            message = str(exc)
            raise HTTPException(404 if "не найден" in message else 400, message) from exc
        return {"tools": result["tools"], "status": mcp_registry.status(sid)}

    @app.post("/api/mcp/servers/{sid}/disconnect")
    def mcp_disconnect(sid: str) -> dict:
        try:
            mcp_store.get(sid)
        except McpError as exc:
            raise HTTPException(404, str(exc)) from exc
        mcp_registry.disconnect(sid)
        return {"status": mcp_registry.status(sid)}

    @app.get("/api/mcp/tools")
    def mcp_tools() -> dict:
        return {"tools": mcp_registry.tools()}

    def _lead() -> QaLead:
        return app.state.lead_factory()

    def _require_run(run_id: str) -> dict:
        run = runs.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"запуск {run_id} не найден")
        return run

    def _task_exists(task_id: str) -> bool:
        return (Path(doc_root) / "tasks" / f"{task_id}.md").exists()

    @app.post("/api/runs", status_code=201)
    def create_run(body: dict) -> dict:
        task_id = str(body.get("task_id") or "")
        repo_path = str(body.get("repo_path") or "")
        branch = str(body.get("branch") or "")
        if not _task_exists(task_id):
            raise HTTPException(400, f"тест-кейс {task_id or '(пусто)'} не найден в doc/tasks")
        if not repo_path or not branch:
            raise HTTPException(400, "нужны repo_path и branch")
        if not (Path(repo_path) / ".git").exists():
            raise HTTPException(400, f"путь не является git-репозиторием: {repo_path}")
        run = runs.create_run(task_id, repo_path, branch)
        return {"run": run}

    @app.get("/api/runs")
    def list_runs() -> dict:
        return {"runs": runs.list_runs()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return {"run": _require_run(run_id)}

    @app.get("/api/runs/{run_id}/stream")
    def stream(run_id: str) -> StreamingResponse:
        _require_run(run_id)

        async def generator():
            async for event in bus.subscribe(run_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/start")
    async def start_run(run_id: str) -> dict:
        _require_run(run_id)
        try:
            await asyncio.to_thread(_lead().start, run_id)
        except PendingGate:
            pass
        except Exception as exc:  # noqa: BLE001
            bus.publish(run_id, {"type": "error", "message": str(exc)})
            bus.close(run_id)
            raise HTTPException(400, str(exc)) from exc
        return {"run": runs.get_run(run_id)}

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict:
        run = _require_run(run_id)
        try:
            _lead().ensure_gate("write", run)
            _lead().ensure_gate("provision", run)
        except GateError as exc:
            raise HTTPException(400, str(exc)) from exc
        await asyncio.to_thread(_lead().resume, run_id)
        return {"run": runs.get_run(run_id)}

    @app.get("/api/runs/{run_id}/skeleton")
    def get_skeleton(run_id: str) -> dict:
        run_dir = Path(doc_root) / "runs" / run_id
        path = run_dir / "skeleton.md"
        if not path.exists():
            raise HTTPException(404, "каркас ещё не сформирован")
        return {"path": "skeleton.md", "content": path.read_text(encoding="utf-8")}

    @app.put("/api/runs/{run_id}/skeleton")
    def put_skeleton(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(400, "каркас не может быть пустым")
        run_dir = Path(doc_root) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "skeleton.md").write_text(content, encoding="utf-8")
        bus.publish(run_id, {"type": "skeleton_updated", "path": "skeleton.md",
                             "by": "human"})
        return {"path": "skeleton.md", "saved": True}

    @app.post("/api/runs/{run_id}/approve-skeleton")
    def approve_skeleton(run_id: str) -> dict:
        _require_run(run_id)
        return {"run": _lead().approve_skeleton(run_id, True)}

    @app.post("/api/runs/{run_id}/execution-choice")
    def execution_choice(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        try:
            run = _lead().choose_execution(run_id, body.get("choice", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"run": run}

    @app.post("/api/runs/{run_id}/answer")
    def answer(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        question_id = body.get("question_id")
        if not bus.answer(question_id, str(body.get("text") or "")):
            raise HTTPException(404, "вопрос не найден или уже отвечен")
        bus.publish(run_id, {"type": "question_answered", "question_id": question_id})
        return {"answered": question_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict:
        _require_run(run_id)
        _lead().cancel(run_id)
        return {"run": runs.get_run(run_id)}

    return app


app = create_app()
