"""Обёртка над git: ветки, журнал, diff, blame — изучение изменений проекта."""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: str):
        self.path = Path(path)

    def _run(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(self.path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitError(f"git недоступен: {exc}") from exc
        if result.returncode != 0:
            raise GitError(result.stderr.strip() or f"git {args[0]} завершился с ошибкой")
        return result.stdout

    def validate(self) -> None:
        if not (self.path / ".git").exists():
            raise GitError(f"не git-репозиторий: {self.path}")
        self.current_branch()

    def current_branch(self) -> str:
        return self._run("rev-parse", "--abbrev-ref", "HEAD").strip()

    def branches(self) -> list[str]:
        out = self._run("branch", "--format=%(refname:short)")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def log(self, limit: int = 20, branch: str | None = None) -> list[dict]:
        target = branch or "HEAD"
        out = self._run("log", f"-{limit}", "--format=%h|%s|%an|%ad", "--date=short", target)
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"hash": parts[0], "message": parts[1],
                                "author": parts[2], "date": parts[3]})
        return commits

    def diff(self, branch: str | None = None, base: str = "develop") -> str:
        target = branch or "HEAD"
        try:
            return self._run("diff", f"{base}...{target}")
        except GitError:
            try:
                return self._run("diff", "HEAD~1", target)
            except GitError:
                empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
                return self._run("diff", empty_tree, target)

    def blame(self, rel_path: str, lines: int = 20) -> str:
        out = self._run("blame", "--line-porcelain", "-L", f"1,{lines}", rel_path)
        return "".join(
            line + "\n" for line in out.splitlines() if line.startswith(("author ", "summary "))
        )

    def recent_changes(self, limit: int = 20) -> str:
        commits = self.log(limit=limit)
        head = "\n".join(
            f"- {c['hash']} {c['message']} ({c['author']}, {c['date']})" for c in commits
        )
        return f"## Коммиты\n{head}\n\n## Diff\n{self.diff()[:6000]}"
