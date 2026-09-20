"""FastAPI-приложение «Студия» (день 11).

Все бизнес-логика — в StudioAgent/MemoryStore; роуты здесь только
валидация (400/404 с RU detail) и формат ответа.
"""
import json
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

import httpx

try:  # пакетный режим: uvicorn studio.backend.main:app из корня репозитория
    from .agent import CONTEXT_LIMITS, DEFAULT_CONTEXT_LIMIT, MEMORY_RULE, StudioAgent
    from .memory import PROFILE_ACTIONS, MemoryStore
except ImportError:  # dev-режим: uvicorn main:app из studio/backend
    from agent import CONTEXT_LIMITS, DEFAULT_CONTEXT_LIMIT, MEMORY_RULE, StudioAgent
    from memory import PROFILE_ACTIONS, MemoryStore, PROFILE_ACTIONS

# Секреты/настройки — из .env в корне репозитория.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Данные лежат в studio/data (создаётся при первой записи, в git не коммитится).
DATA_DIR = str(Path(__file__).resolve().parents[1] / "data")


def create_app(agent: StudioAgent | None = None) -> FastAPI:
    """Создаёт FastAPI-приложение. agent inject-ится для тестов;
    по умолчанию — StudioAgent(DATA_DIR) с настройками из окружения."""
    agent = agent or StudioAgent(DATA_DIR)
    app = FastAPI(title="Студия")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- чат (SSE) ----------

    @app.post("/api/chat")
    def chat(body: dict):
        """Стриминг ответа LLM по SSE: data: {событие}\n\n."""
        dialogue_id = body.get("dialogue_id")
        message = body.get("message")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(400, "Сообщение не может быть пустым")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")

        def gen():
            for event in agent.ask_stream(dialogue_id, message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ---------- профиль пользователя (день 12) ----------

    @app.post("/api/profile")
    def profile_set(body: dict):
        """Сохранить 4 поля профиля диалога (день 12).
        Body: {dialogue_id, name, role, tone, taboos} — все обязательны."""
        keys = ("dialogue_id", "name", "role", "tone", "taboos")
        for k in keys:
            if k not in body:
                raise HTTPException(400, f"Не указано поле «{k}»")
        for k in keys[1:]:
            if not isinstance(body[k], str):
                raise HTTPException(400, "Поля профиля должны быть строками")
        try:
            p = agent.store.profile_set(body["dialogue_id"], body["name"],
                                        body["role"], body["tone"],
                                        body["taboos"])
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"profile": p}

    @app.post("/api/profile/action")
    def profile_action(body: dict):
        """Действие с профилем: interview / decline / reset (день 12).
        Body: {dialogue_id, action}."""
        if "dialogue_id" not in body or "action" not in body:
            raise HTTPException(400, "Не указаны dialogue_id или action")
        if body["action"] not in PROFILE_ACTIONS:
            raise HTTPException(400, f"Неизвестное действие: {body['action']}")
        try:
            p = agent.store.profile_action(body["dialogue_id"], body["action"])
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"profile": p}

    # ---------- задача: FSM + stage-агенты (день 13) ----------

    @app.post("/api/task/start")
    def task_start(body: dict):
        """Создать задачу: {dialogue_id, description}. 400 — пустое описание
        или уже есть незавершённая задача; 404 — диалог. User-сообщение-
        запрос сохраняется с маркером task_id (якорь карточки процесса).
        При завершённой задаче (done/failed) — новая задача."""
        dialogue_id = body.get("dialogue_id")
        description = body.get("description")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if not isinstance(description, str) or not description.strip():
            raise HTTPException(400, "Описание задачи не может быть пустым")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        try:
            t = agent.store.task_new(dialogue_id, description.strip())
            agent.store.append_message(dialogue_id, "user",
                                       description.strip(), task_id=t["task_id"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task": t}

    @app.post("/api/task/run")
    def task_run(body: dict):
        """SSE-пайплайн задачи: события stage/stage_done/task_paused/
        task_done/error."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        t = agent.store.task_get(dialogue_id)
        if not t["active"]:
            raise HTTPException(400, "Задача не активна")
        if t["stage"] == "done":
            raise HTTPException(400, "Задача завершена")

        def gen():
            for event in agent.task_run(dialogue_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/api/task/pause")
    def task_pause(body: dict):
        """Поставить паузу (вступает на границе стадии)."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        try:
            t = agent.store.task_pause(dialogue_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task": t}

    @app.post("/api/task/resume")
    def task_resume(body: dict):
        """Снять паузу (и ошибку); пайплайн запускается через /api/task/run."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        try:
            t = agent.store.task_resume(dialogue_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task": t}

    @app.post("/api/task/instruction")
    def task_instruction(body: dict):
        """Инструкция пользователя — только на паузе."""
        dialogue_id = body.get("dialogue_id")
        text = body.get("text")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(400, "Инструкция не может быть пустой")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        try:
            t = agent.store.task_set_instruction(dialogue_id, text.strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task": t}

    @app.post("/api/task/reset")
    def task_reset(body: dict):
        """Сбросить состояние задачи (готовность к новой задаче)."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        t = agent.store.task_reset(dialogue_id)
        return {"task": t}

    @app.get("/api/task")
    def task_get(dialogue_id: str):
        """Состояние задачи диалога (нет задачи — active=false)."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        return {"task": agent.store.task_get(dialogue_id)}

    # ---------- конфиг ----------

    @app.get("/api/config")
    def config_get():
        """Актуальный конфиг LLM."""
        return agent.get_config()

    @app.post("/api/config")
    def config_post(body: dict):
        """Частичное обновление конфига; 200 — актуальный конфиг."""
        try:
            return agent.set_config(body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---------- модели ----------

    @app.get("/api/models")
    def models():
        """Доступные модели API (зонд) с лимитами; self-heal недоступной модели
        из конфига; 502 если API недоступно."""
        try:
            agent.ensure_model_available()
            return {"models": agent.list_models()}
        except Exception:
            raise HTTPException(502, "Не удалось получить список моделей с API")

    # ---------- диалоги ----------

    @app.get("/api/dialogues")
    def dialogues_list():
        """Список диалогов и id активного."""
        return {"active_id": agent.store.active_id(),
                "dialogues": agent.store.list_dialogues()}

    @app.post("/api/dialogues", status_code=201)
    def dialogues_new():
        """Создать диалог (становится активным)."""
        d = agent.store.new_dialogue()
        return {"dialogue": d, "active_id": agent.store.active_id()}

    @app.get("/api/dialogues/{dialogue_id}")
    def dialogues_get(dialogue_id: str):
        """Диалог с сообщениями."""
        d = agent.store.get_dialogue(dialogue_id)
        if d is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        return {"dialogue": d}

    @app.delete("/api/dialogues/{dialogue_id}")
    def dialogues_delete(dialogue_id: str):
        """Удалить диалог и его рабочую память."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        agent.store.delete_dialogue(dialogue_id)
        return {"ok": True}

    @app.post("/api/dialogues/{dialogue_id}/rename")
    def dialogues_rename(dialogue_id: str, body: dict):
        """Переименовать диалог (body: {title})."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(400, "Заголовок не может быть пустым")
        agent.store.rename_dialogue(dialogue_id, title)
        d = agent.store.get_dialogue(dialogue_id)
        return {"dialogue": {"id": d["id"], "title": d["title"],
                             "created": d["created"],
                             "message_count": len(d["messages"])}}

    @app.post("/api/dialogues/{dialogue_id}/activate")
    def dialogues_activate(dialogue_id: str):
        """Сделать диалог активным."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        agent.store.activate(dialogue_id)
        return {"active_id": agent.store.active_id()}

    # ---------- память: ST ----------

    @app.post("/api/memory/st/clear")
    def memory_st_clear():
        """Очистить сообщения активного диалога."""
        active = agent.store.active_id()
        if active is None:
            raise HTTPException(400, "Нет активного диалога")
        agent.store.clear_st(active)
        return {"ok": True}

    @app.get("/api/memory")
    def memory_get():
        """Статистика слоёв памяти (по активному диалогу) + active_id + toggles."""
        stats = agent.store.layer_stats()
        stats["active_id"] = agent.store.active_id()
        stats["toggles"] = agent.store.get_toggles()
        return stats

    @app.get("/api/memory/toggles")
    def memory_toggles_get():
        """Тумблеры слоёв памяти: {st, wm, lt: bool}."""
        return {"toggles": agent.store.get_toggles()}

    @app.post("/api/memory/toggles")
    def memory_toggles_set(body: dict):
        """Включить/отключить слой памяти {layer: st|wm|lt, enabled: bool}."""
        layer = body.get("layer")
        enabled = body.get("enabled")
        if not isinstance(layer, str) or layer not in MemoryStore.TOGGLE_LAYERS:
            raise HTTPException(400, "layer должен быть одним из: st, wm, lt")
        if not isinstance(enabled, bool):
            raise HTTPException(400, "enabled должен быть bool (true/false)")
        return {"toggles": agent.store.set_toggle(layer, enabled)}

    # ---------- память: WM ----------

    @app.post("/api/memory/working")
    def memory_working_set(body: dict):
        """Поставить заметку в рабочую память активного диалога."""
        active = agent.store.active_id()
        if active is None:
            raise HTTPException(400, "Нет активного диалога")
        key = body.get("key")
        value = body.get("value")
        if not isinstance(key, str) or not key:
            raise HTTPException(400, "Ключ не может быть пустым")
        if not isinstance(value, str):
            raise HTTPException(400, "Значение должно быть строкой")
        agent.store.wm_set(active, key, value)
        return {"ok": True}

    @app.delete("/api/memory/working/{key}")
    def memory_working_delete(key: str):
        """Удалить заметку из рабочей памяти активного диалога."""
        active = agent.store.active_id()
        if active is None:
            raise HTTPException(400, "Нет активного диалога")
        if not agent.store.wm_remove(active, key):
            raise HTTPException(404, f"Заметка «{key}» не найдена")
        return {"ok": True}

    @app.post("/api/memory/working/clear")
    def memory_working_clear():
        """Очистить рабочую память активного диалога."""
        active = agent.store.active_id()
        if active is None:
            raise HTTPException(400, "Нет активного диалога")
        agent.store.wm_clear(active)
        return {"ok": True}

    # ---------- память: LT ----------

    @app.post("/api/memory/longterm")
    def memory_longterm_set(body: dict):
        """Поставить глобальную заметку."""
        key = body.get("key")
        value = body.get("value")
        if not isinstance(key, str) or not key:
            raise HTTPException(400, "Ключ не может быть пустым")
        if not isinstance(value, str):
            raise HTTPException(400, "Значение должно быть строкой")
        agent.store.lt_set(key, value)
        return {"ok": True}

    @app.delete("/api/memory/longterm/{key}")
    def memory_longterm_delete(key: str):
        """Удалить глобальную заметку."""
        if not agent.store.lt_remove(key):
            raise HTTPException(404, f"Заметка «{key}» не найдена")
        return {"ok": True}

    @app.post("/api/memory/longterm/clear")
    def memory_longterm_clear():
        """Очистить все глобальные заметки."""
        agent.store.lt_clear()
        return {"ok": True}

    # ---------- инварианты (день 14, глобальные) ----------

    @app.get("/api/invariants")
    def invariants_list():
        """Список инвариантов: [{id, key, value}]."""
        items = agent.store.invariants_items()
        return {"invariants": [{"id": i, "key": e["key"], "value": e["value"]}
                                for i, e in items.items()]}

    @app.post("/api/invariants", status_code=201)
    def invariants_set(body: dict):
        """Создать/обновить инвариант {key, value} (оба непустые строки).
        400 — поле отсутствует, не строка или пустое."""
        for k in ("key", "value"):
            if k not in body:
                raise HTTPException(400, f"Не указано поле «{k}»")
        key = body["key"]
        value = body["value"]
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(400, "Ключ инварианта должен быть непустой строкой")
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(400, "Значение инварианта должно быть непустой строкой")
        rec = agent.store.invariants_set(key, value)
        return {"invariant": rec}

    @app.delete("/api/invariants/{iid}")
    def invariants_delete(iid: str):
        """Удалить инвариант по id; 404 — не найден."""
        if not agent.store.invariants_remove(iid):
            raise HTTPException(404, f"Инвариант «{iid}» не найден")
        return {"ok": True}

    # ---------- токены ----------

    @app.get("/api/tokens")
    def tokens():
        """Последний usage, сессионные токены и лимит контекста модели."""
        usage = agent.last_usage()
        last = None
        if usage:
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            last = {"prompt": usage.get("prompt_tokens", 0),
                    "completion": usage.get("completion_tokens", 0),
                    "reasoning": reasoning,
                    "total": usage.get("total_tokens", 0)}
        model = agent.get_config()["model"]
        return {"last": last, "session": agent.session_tokens(),
                "context_limit": CONTEXT_LIMITS.get(model, DEFAULT_CONTEXT_LIMIT)}

    # ---------- правила агента ----------

    @app.get("/api/rules")
    def rules(dialogue_id: str | None = None):
        """Активные правила: системный промпт + правило памяти
        (активно, когда в памяти диалога есть записи) + профиль (день 12).
        dialogue_id — опционально; без него — активный диалог."""
        cfg = agent.get_config()
        did = dialogue_id or agent.store.active_id()
        blocks = agent.store.build_memory_blocks(did) if did else ""
        profile = agent.store.profile_get(did) if did else \
            {"status": "pending"}
        return {"system_prompt": cfg["system_prompt"],
                "memory_rule": MEMORY_RULE,
                "rule_active": bool(blocks),
                "profile_block": agent.build_profile_block(did) if did else "",
                "profile_status": profile["status"],
                "invariants_block": agent.build_invariants_block()}

    # ---------- журнал запросов ----------

    @app.get("/api/requests")
    def requests_list():
        """Журнал LLM-запросов без тел."""
        return {"requests": agent.requests_list()}

    @app.get("/api/requests/{request_id}")
    def requests_get(request_id: int):
        """Полная запись журнала (с телом запроса)."""
        e = agent.requests_get(request_id)
        if e is None:
            raise HTTPException(404, f"Запрос №{request_id} не найден")
        return e

    @app.delete("/api/requests")
    def requests_clear():
        """Очистить журнал запросов."""
        agent.requests_clear()
        return {"ok": True}

    return app


app = create_app()

# ---------- prod-режим: раздаём собранный фронтенд (studio/frontend/dist) ----------
# Активен, если dist существует (после `npm run build`). Путь — от main.py,
# не от CWD: работает и `uvicorn main:app` из studio/backend, и
# `uvicorn studio.backend.main:app` из корня репозитория.
_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if _DIST.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """SPA-фолбэк: известные статические файлы — как есть, остальное — index.html.
        Зарегистрирован после всех /api-роутов, те имеют приоритет."""
        if full_path:
            target = (_DIST / full_path).resolve()
            if target.is_file() and str(target).startswith(str(_DIST.resolve())):
                return FileResponse(target)
        return FileResponse(_DIST / "index.html")
