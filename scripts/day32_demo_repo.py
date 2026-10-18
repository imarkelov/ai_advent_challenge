# -*- coding: utf-8 -*-
"""Демо-PR дня 32: собирает git-репозиторий с реальным pull request.

Вызывается из `scripts/day32_pr_review.sh demo`, но годится и сам по себе:

    .venv/bin/python scripts/day32_demo_repo.py /tmp/day32-demo

Что получается: копия корпуса проекта (документация + код, чтобы RAG было
по чему искать), ветка `main` с «базой» и ветка `feature/users-api` с
небольшим, но не идеальным PR:

* новый `studio/backend/users_api.py` — секрет прямо в коде, голый
  `except:`, удаление без проверки прав, нет тестов;
* правка `docs/USERS.md` — удаление описано как «через сервис», а код
  идёт мимо сервиса (RAG подскажет модели это противоречие).

Скрипт печатает путь репозитория и `git diff --stat main...HEAD`.
"""
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CORPUS = ["README.md", "docs", "agents", "openspec", "data/crm",
          "studio/backend", "studio/frontend/src", "scripts"]

USERS_API = '''"""Пользователи: REST-слой."""
from fastapi import FastAPI

app = FastAPI()

API_KEY = "sk-live-1234567890abcdefghijkl"


@app.get("/users/{user_id}")
def get_user(user_id: str):
    try:
        return {"id": user_id}
    except:
        return None


@app.delete("/users/{user_id}")
def delete_user(user_id: str):
    return {"deleted": user_id}
'''

USERS_DOC_BASE = ("# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST.\n"
                  "Удаление доступно только администратору через сервис "
                  "`users_service`.\n")
USERS_DOC_PR = ("# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST.\n"
                "Удаление — `DELETE /users/{id}` в том же модуле, "
                "права администратора проверяет сам слой API.\n")


def git(root: str, *args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                       text=True)
    if p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def build(root: str) -> None:
    for rel in CORPUS:
        src, dst = os.path.join(REPO, rel), os.path.join(root, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "node_modules", "dist"))
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst) or root, exist_ok=True)
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(root, "data", "kb"), exist_ok=True)

    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "dev@example.com")
    git(root, "config", "user.name", "Dev")
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write(USERS_DOC_BASE)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "база: документация пользователей")

    git(root, "checkout", "-qb", "feature/users-api")
    with open(os.path.join(root, "studio", "backend", "users_api.py"), "w",
              encoding="utf-8") as f:
        f.write(USERS_API)
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write(USERS_DOC_PR)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "feat(users): REST-слой пользователей")


def main() -> int:
    if len(sys.argv) != 2:
        print("Использование: day32_demo_repo.py КАТАЛОГ", file=sys.stderr)
        return 2
    root = os.path.abspath(sys.argv[1])
    os.makedirs(root, exist_ok=True)
    build(root)
    print(f"Репозиторий: {root}")
    print(git(root, "log", "--oneline", "--graph", "--all").rstrip())
    print(git(root, "diff", "--stat", "main...HEAD").rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
