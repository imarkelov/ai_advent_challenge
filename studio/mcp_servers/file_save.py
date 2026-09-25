"""MCP-сервер file_save (день 20) — stdio, один тул: saveToFile.

Port из pipeline_tools.py дня 19, алгоритм без изменений: атомарная
запись (tmp + os.replace) в каталог вывода data/pipeline; только
basename (traversal-safe, «../» не проходит за каталог вывода),
санитизация имени (символы вне [\\w.\\- ] -> _), pdf -> +.pdf
(PDF — stdlib-райтер pdf_writer, кириллица).

Только stdlib. Env читаются в момент вызова:
    PIPELINE_OUT_DIR    — каталог вывода (дефолт <repo>/data/pipeline)
    PIPELINE_FONT_PATH  — .ttf для PDF (дефолт — системный, см. pdf_writer)

Запуск: python studio/mcp_servers/file_save.py
"""
import json
import os
import re
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_DIR))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import pdf_writer  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "saveToFile",
    "description": ("Сохранить content атомарно в каталоге вывода "
                    "(data/pipeline): md|txt|json|pdf. PDF — stdlib-райтер "
                    "pdf_writer (A4, кириллица, шрифт вшит). "
                    "Возвращает path, size, format."),
    "input_schema": {"type": "object", "properties": {
        "filename": {"type": "string",
                     "description": "Имя файла (только basename)"},
        "content": {"type": "string",
                    "description": "Содержимое файла"},
        "format": {"type": "string",
                   "enum": ["md", "txt", "json", "pdf"],
                   "description": "Формат (дефолт md; pdf -> +.pdf)"}},
        "required": ["filename", "content"]},
}


def call(args: dict) -> tuple:
    a = args or {}
    filename = a.get("filename")
    content = a.get("content")
    fmt = a.get("format") or "md"
    if not isinstance(filename, str) or not filename.strip():
        return {"error": "saveToFile: 'filename' — непустая строка"}, True
    if not isinstance(content, str):
        return {"error": "saveToFile: 'content' — строка"}, True
    if fmt not in ("md", "txt", "json", "pdf"):
        return {"error": "saveToFile: 'format' — md|txt|json|pdf"}, True
    name = os.path.basename(filename.strip())
    if not name or set(name) <= {"."}:
        return {"error": "saveToFile: недопустимое имя файла: "
                         + repr(filename)}, True
    name = re.sub(r"[^\w.\- ]", "_", name)
    if fmt == "pdf" and not name.lower().endswith(".pdf"):
        name += ".pdf"
    out_dir = (os.environ.get("PIPELINE_OUT_DIR")
               or os.path.join(REPO_ROOT, "data", "pipeline"))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, name)
    try:
        if fmt == "json":
            blob = json.dumps(content, ensure_ascii=False,
                              indent=2).encode("utf-8")
        elif fmt == "pdf":
            blob = pdf_writer.text_to_pdf(
                content, font_path=os.environ.get("PIPELINE_FONT_PATH")
                or None)
        else:
            blob = content.encode("utf-8")
    except pdf_writer.PdfError as e:
        return {"error": "saveToFile: PDF: " + str(e)}, True
    tmp = os.path.join(out_dir, "." + name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, target)
    return {"path": os.path.abspath(target), "size": len(blob),
            "format": fmt}, False


if __name__ == "__main__":
    run_server("file_save", TOOL, call)
