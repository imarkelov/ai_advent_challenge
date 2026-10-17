#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Упаковка экспорта RAG в Markdown-файлы для ручной загрузки.

Берёт `rag_export/` (см. `scripts/export_rag_project.py`) и режет корпус
на файлы ≤ `--max-bytes`, НЕ разрезая документ посередине: документ
целиком переезжает в следующий файл, если не влезает в текущий. Так
части пачке загружаются по одной и читаются как обычные документы.

Каждая часть — валидный Markdown: шапка с содержимым (документ/чанки),
заголовки `##` на документ и `###` на его секции — так внешний индекс
даже при своей нарезке сохранит смысл.

Запуск (из корня репозитория):

    .venv/bin/python scripts/export_rag_project.py --out rag_export
    .venv/bin/python scripts/package_rag_upload.py --src rag_export
"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(REPO_ROOT, "rag_export")
DEFAULT_OUT = os.path.join(REPO_ROOT, "rag_upload")
MAX_BYTES = 200_000


def _load_chunks(path: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            out.setdefault(c["file"], []).append(c)
    return out


def _trim(text: str, limit: int = 6000) -> str:
    """Чанк длиннее лимита обрезается по границе абзаца."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n\n", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + " …"


def _split_long_block(rel: str, d: dict, chunks: list[dict],
                      max_bytes: int) -> list[str]:
    """Документ не влезает в файл целиком — режем по его чанкам:
    каждый кусок — отдельный Markdown с тем же `## путь`."""
    pieces, cur = [], []
    size = 0
    for c in chunks:
        block = f"### {rel} · {c['section']}\n\n{_trim(c['text'])}\n"
        if size + len(block) > max_bytes * 0.9 and cur:
            pieces.append(cur)
            cur, size = [], 0
        cur.append(block)
        size += len(block)
    if cur:
        pieces.append(cur)
    return [f"## {rel} (продолжение)\n\n" + "\n".join(p) + "\n---\n\n"
            for p in pieces]


def build_parts(docs: list[dict], chunks: dict[str, list[dict]],
                max_bytes: int = MAX_BYTES) -> list[tuple[str, str]]:
    """[(имя файла, текст)] — документы-чанки в порядке manifest."""
    header = (
        "# RAG-корпус проекта «Студия» — часть {n}\n\n"
        "Документы репозитория Студии (вопросы по проекту) в виде\n"
        "фрагментов, которые видит поиск панели «Ассистент»:\n"
        "каждый фрагмент — `### путь/к/файлу · секция` и текст.\n\n"
        "---\n\n"
    )
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    size = 0
    n = 1

    def flush():
        nonlocal buf, size, n
        if not buf:
            return
        name = f"rag_part_{n:02d}.md"
        parts.append((name, header.format(n=n) + "\n".join(buf)))
        n += 1
        buf, size = [], 0

    for d in docs:
        rel = d["file"]
        blocks = [f"## {rel}\n"]
        for c in chunks.get(rel, []):
            body = _trim(c["text"])
            # секция-обёртка без содержания («## 6. Как проверить
            # руками») — не дублируем пустой заголовок
            if body.strip() in ("", f"## {c['section']}".strip(),
                                f"# {c['section']}".strip()):
                continue
            blocks.append(f"### {rel} · {c['section']}\n\n{body}\n")
        if len(blocks) == 1:
            blocks.append("_(документ без текста или из одних заголовков)_\n")
        block = "\n".join(blocks) + "\n---\n\n"
        if size + len(block) > max_bytes and buf:
            flush()
        if len(block) > max_bytes:
            # огромный файл — режем по его же чанкам, а не по символам
            for piece in _split_long_block(rel, d, chunks.get(rel, []),
                                           max_bytes):
                if buf:
                    flush()
                buf, size = [piece], len(piece)
                flush()
            continue
        buf.append(block)
        size += len(block)
    flush()
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = ap.parse_args()

    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    manifest = json.load(open(os.path.join(src, "documents.json"),
                              encoding="utf-8"))
    chunks = _load_chunks(os.path.join(src, "chunks.jsonl"))
    os.makedirs(out, exist_ok=True)
    for name in os.listdir(out):            # только свои части
        if name.startswith("rag_part_") and name.endswith(".md"):
            os.remove(os.path.join(out, name))

    parts = build_parts(manifest["documents"], chunks, args.max_bytes)
    index = []
    for name, text in parts:
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(text)
        index.append({"file": name, "bytes": len(text.encode("utf-8"))})

    with open(os.path.join(out, "MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"documents": len(manifest["documents"]),
                   "parts": index,
                   "total_bytes": sum(p["bytes"] for p in index)},
                  f, ensure_ascii=False, indent=2)

    print(f"Готово: {out}")
    print(f"  документов: {len(manifest['documents'])}, "
          f"частей: {len(parts)}, суммарно "
          f"{sum(p['bytes'] for p in index) / 1024:.0f} КБ")
    for p in index:
        print(f"  {p['file']:20} {p['bytes'] / 1024:7.0f} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
