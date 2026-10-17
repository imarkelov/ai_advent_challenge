#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Экспорт RAG-корпуса проекта «Студия» для внешней проверки.

Собирает из репозитория ровно тот корпус, который индексирует веб-панель
(`POST /api/assistant/index` → `data/kb/project/index.db`), и кладёт его
двумя способами:

* `documents/` — исходные файлы, сохранённые структуру каталогов
  (с ними проверяющий видит первоисточник);
* `chunks.jsonl`, `documents.json` — те же документы, порезанные
  чанкером индекса (`kb.StructuredChunker`) — как их видит поиск;
* `manifest.json` — что загружено, контрольные суммы, разбивка по
  источникам и совпадение с живым SQLite-индексом.

Офлайн и детерминированно: без Ollama и сети, только stdlib.

Запуск (из корня репозитория):

    .venv/bin/python scripts/export_rag_project.py
    .venv/bin/python scripts/export_rag_project.py --out rag_export
    .venv/bin/python scripts/export_rag_project.py --check-db   # сверка с индексом
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "studio", "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from assistant import ProjectCorpus  # noqa: E402
import kb as kb_mod  # noqa: E402

DEFAULT_OUT = os.path.join(REPO_ROOT, "rag_export")
PROJECT_DB = os.path.join("data", "kb", "project", "index.db")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_dir(path: str) -> None:
    """Удалить прошлый экспорт, но не трогать посторонние файлы."""
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def _db_stats(repo_root: str) -> dict | None:
    """Сводка живого индекса проекта (если он построен)."""
    db = os.path.join(repo_root, PROJECT_DB)
    if not os.path.isfile(db):
        return None
    con = sqlite3.connect(db)
    try:
        files = con.execute("select count(distinct file) from chunks").fetchone()[0]
        chunks = con.execute("select count(*) from chunks").fetchone()[0]
        return {"db": PROJECT_DB.replace(os.sep, "/"),
                "files": files, "chunks": chunks}
    finally:
        con.close()


def _db_files(repo_root: str) -> set:
    db = os.path.join(repo_root, PROJECT_DB)
    if not os.path.isfile(db):
        return set()
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("select distinct file from chunks")}
    finally:
        con.close()


def export(repo_root: str, out_dir: str, check_db: bool = False) -> dict:
    corpus = ProjectCorpus(repo_root)
    docs = corpus.documents()
    if not docs:
        raise SystemExit("Корпус пуст — нечего экспортировать")

    _clean_dir(out_dir)
    docs_dir = os.path.join(out_dir, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    chunker = kb_mod.StructuredChunker()
    manifest_docs = []
    chunks_jsonl = os.path.join(out_dir, "chunks.jsonl")
    by_source: dict[str, int] = {}
    with open(chunks_jsonl, "w", encoding="utf-8") as jl:
        for doc in docs:
            rel = doc.path.replace(os.sep, "/")
            raw = doc.text.encode("utf-8")
            dest = os.path.join(docs_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(raw)

            chunks = chunker.chunk(doc)
            for i, ch in enumerate(chunks):
                jl.write(json.dumps({
                    "chunk_id": f"{rel}#{i:04d}",
                    "file": rel,
                    "source": doc.source,
                    "section": ch.section,
                    "chars": len(ch.text),
                    "text": ch.text,
                }, ensure_ascii=False) + "\n")
            by_source[doc.source] = by_source.get(doc.source, 0) + 1
            manifest_docs.append({
                "file": rel,
                "source": doc.source,
                "bytes": len(raw),
                "chars": len(doc.text),
                "sha256": _sha256(raw),
                "chunks": len(chunks),
            })

    documents_json = os.path.join(out_dir, "documents.json")
    with open(documents_json, "w", encoding="utf-8") as f:
        json.dump({"repo_root": os.path.basename(repo_root),
                   "generated_at": datetime.now(timezone.utc).isoformat(
                       timespec="seconds"),
                   "documents": manifest_docs},
                  f, ensure_ascii=False, indent=2)

    db = _db_stats(repo_root)
    manifest = {
        "project": os.path.basename(os.path.abspath(repo_root)),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len(manifest_docs),
        "chunks": sum(d["chunks"] for d in manifest_docs),
        "by_source": dict(sorted(by_source.items())),
        "chunker": {"strategy": "structural",
                    "max_section": kb_mod.DEFAULT_MAX_SECTION,
                    "sub_chunk_chars": kb_mod.DEFAULT_CHUNK_CHARS,
                    "sub_chunk_overlap": kb_mod.DEFAULT_OVERLAP},
        "formats": {
            "documents/": "исходные файлы корпуса (источники)",
            "documents.json": "список документов + sha256",
            "chunks.jsonl": "чанки в том виде, в каком их видит поиск",
        },
        "files_sha256": {
            "documents.json": _sha256(open(documents_json, "rb").read()),
            "chunks.jsonl": _sha256(open(chunks_jsonl, "rb").read()),
        },
        "index": db,
    }
    if check_db:
        have = _db_files(repo_root)
        manifest["index_check"] = {
            "index_present": db is not None,
            "corpus_files": len(manifest_docs),
            "missing_in_index": sorted(set(manifest_docs and
                                                  [d["file"] for d in manifest_docs])
                                         - have),
            "extra_in_index": sorted(have - {d["file"] for d in manifest_docs}),
        }

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=REPO_ROOT)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--check-db", action="store_true",
                    help="сверить корпус с живым index.db (файлы без индекса)")
    args = ap.parse_args()

    manifest = export(os.path.abspath(args.repo), os.path.abspath(args.out),
                      check_db=args.check_db)
    print(f"Экспорт RAG: {args.out}")
    print(f"  документов: {manifest['files']}   чанков: {manifest['chunks']}")
    print("  по источникам: " + ", ".join(
        f"{k} {v}" for k, v in manifest["by_source"].items()))
    if manifest.get("index") is None:
        print("  индекс проекта не построен (data/kb/project) — "
              "сверки нет; экспорт сделан из файлов репозитория")
    elif manifest.get("index_check"):
        chk = manifest["index_check"]
        print(f"  индекс: {chk['corpus_files']} файлов корпуса, "
              f"нет в индексе: {len(chk['missing_in_index'])}, "
              f"лишних: {len(chk['extra_in_index'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
