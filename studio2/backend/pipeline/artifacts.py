"""Каталог запуска и писатели документов: SDD, каркас, отчёты."""
from __future__ import annotations

from pathlib import Path

from store import RUN_ID_RE


class RunDir:
    def __init__(self, doc_dir: str, run_id: str):
        if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
            raise ValueError("недопустимый идентификатор запуска")
        self.root = Path(doc_dir) / "runs" / run_id

    def ensure(self) -> None:
        for name in ("sdd", "logs", "test_generated"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def sdd(self) -> Path:
        return self.root / "sdd"

    @property
    def test_generated(self) -> Path:
        return self.root / "test_generated"

    @property
    def skeleton_path(self) -> Path:
        return self.root / "skeleton.md"

    @property
    def report_path(self) -> Path:
        return self.root / "test_report.md"

    @property
    def review_path(self) -> Path:
        return self.root / "review_report.md"

    def write(self, rel: str, content: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read(self, rel: str) -> str:
        path = self.root / rel
        return path.read_text(encoding="utf-8") if path.exists() else ""


def sdd_text(role: str, task_id: str, task_title: str, repo_path: str, branch: str,
             git_summary: str, goal: str, rag_inputs: str, artifacts: str,
             instructions: str, allowed_tools: str, max_rounds: int,
             write_scope: str, criteria: str) -> str:
    return f"""# SDD — {role}

## Контекст
- Задача: {task_id} — {task_title}
- Репозиторий / ветка: {repo_path} @ {branch}
- Изменения в ветке: {git_summary}

## Цель
{goal}

## Вход
- RAG: {rag_inputs}
- Артефакты предыдущих ролей: {artifacts}

## Инструкции
{instructions}

## Ограничения
- allowed_tools: {allowed_tools}
- max_rounds: {max_rounds}
- write_scope: {write_scope}

## Формат ответа (JSON)
{{"status": "done|need_question|fail", "artifacts": [], "summary": "", "question": null}}

## Критерии готовности
{criteria}
"""


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- —"


def skeleton_markdown(test_class: str, task_id: str, steps: list[str], tags: list[str],
                      inputs: list[str], boundaries: list[str],
                      edits: list[str]) -> str:
    numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(steps)) or "1. —"
    return (
        f"# Каркас — {test_class} ({task_id})\n\n"
        f"## Шаги (структура)\n{numbered}\n\n"
        f"## Используемые теги\n{_bullets(tags)}\n\n"
        f"## Входные данные\n{_bullets(inputs)}\n\n"
        f"## Границы (не проверяем)\n{_bullets(boundaries)}\n\n"
        f"## Ожидаемые правки человека\n{_bullets(edits)}\n"
    )


def review_markdown(run_id: str, rows: list[dict], verdict: str,
                    details: list[str] | None = None) -> str:
    body = "\n".join(
        f"| {row.get('test', '—')} | {row.get('status', '—')} | "
        f"{row.get('duration', '')} | {row.get('reason', '')} |"
        for row in rows
    ) or "| — | — | — | — |"
    extra = ""
    if details:
        extra = "\n## Проверки\n" + "\n".join(f"- {line}" for line in details) + "\n"
    return (
        f"# Отчёт ревью — {run_id}\n\n"
        "| Тест | Статус | Длительность | Причина |\n|---|---|---|---|\n"
        f"{body}\n{extra}\n**Вердикт:** {verdict}\n"
    )


def test_report_markdown(run_id: str, task_id: str, branch: str, stages: list[str],
                         verdict: str, artifacts: list[str], summary: str) -> str:
    return (
        f"# Итог запуска {run_id}\n\n"
        f"- ТК: {task_id}\n- Ветка: {branch}\n"
        f"- Стадии: {' → '.join(stages)}\n- Вердикт: {verdict}\n"
        f"- Артефакты: {', '.join(artifacts) or '—'}\n\n"
        f"## Сводка\n{summary}\n"
    )


test_report_markdown.__test__ = False
