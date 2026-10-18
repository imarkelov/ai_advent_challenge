"""Файловое хранилище doc/: дерево, чтение, запись, загрузка, удаление."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

WRITABLE = {"kb", "tasks", "agents", "templates"}
DELETABLE = {"kb", "tasks"}


class DocPathError(ValueError):
    pass


class DocStore:
    def __init__(self, doc_dir: str):
        self.root = Path(doc_dir).resolve()

    def _resolve(self, rel: str) -> Path:
        rel = rel.replace("\\", "/").strip("/")
        if not rel:
            raise DocPathError("пустой путь")
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise DocPathError(f"недопустимый путь: {rel}")
        full = (self.root / rel).resolve()
        if full != self.root and self.root not in full.parents:
            raise DocPathError(f"путь вне doc/: {rel}")
        return full

    def _top(self, full: Path) -> str:
        return PurePosixPath(full.relative_to(self.root).as_posix()).parts[0]

    def tree(self) -> list[dict]:
        nodes = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir():
                files = sorted(p.relative_to(self.root).as_posix()
                               for p in child.rglob("*") if p.is_file())
                nodes.append({"name": child.name, "type": "dir", "children": files})
            else:
                nodes.append({"name": child.name, "type": "file", "children": []})
        return nodes

    def read(self, rel: str) -> str:
        full = self._resolve(rel)
        if not full.is_file():
            raise DocPathError(f"файл не найден: {rel}")
        return full.read_text(encoding="utf-8")

    def write(self, rel: str, content: str) -> str:
        full = self._resolve(rel)
        top = self._top(full)
        if top not in WRITABLE:
            raise DocPathError(f"запись в {top}/ запрещена")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        return rel

    def upload(self, rel_dir: str, filename: str, data: bytes) -> str:
        name = PurePosixPath((filename or "").replace("\\", "/")).name
        if not name:
            raise DocPathError("имя файла не задано")
        rel = f"{(rel_dir.strip('/') or 'kb')}/{name}"
        full = self._resolve(rel)
        top = self._top(full)
        if top not in WRITABLE:
            raise DocPathError(f"загрузка в {top}/ запрещена")
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)
        return rel

    def delete(self, rel: str) -> None:
        full = self._resolve(rel)
        top = self._top(full)
        if top not in DELETABLE:
            raise DocPathError(f"удаление из {top}/ запрещено")
        if full.is_file():
            full.unlink()
