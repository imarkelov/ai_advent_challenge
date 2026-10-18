"""FastAPI-приложение studio2: конфиг, документы, RAG, задачи, MCP."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from config import load_config, mask_secret, save_config
from docstore import DocPathError, DocStore
from llm import LlmClient, LlmError
from mcp import McpError, McpRegistry, McpServerStore
from rag import RagIndex

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

    return app


app = create_app()
