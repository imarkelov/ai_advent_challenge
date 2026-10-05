"""FastAPI-приложение «Студия» (день 11).

Все бизнес-логика — в StudioAgent/MemoryStore; роуты здесь только
валидация (400/404 с RU detail) и формат ответа.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import httpx

try:  # пакетный режим: uvicorn studio.backend.main:app из корня репозитория
    from .agent import (CONTEXT_LIMITS, DEFAULT_CONTEXT_LIMIT, MEMORY_RULE,
                        PROFILE_INTERVIEW_TEXT, StudioAgent)
    from .kb import (APIEmbedder, HashEmbedder, KBError, KnowledgeBase,
                     UPLOAD_EXTS)
    from .memory import PROFILE_ACTIONS, MemoryStore
    from .mcp import MCPError
except ImportError:  # dev-режим: uvicorn main:app из studio/backend
    from agent import (CONTEXT_LIMITS, DEFAULT_CONTEXT_LIMIT, MEMORY_RULE,
                       PROFILE_INTERVIEW_TEXT, StudioAgent)
    from kb import (APIEmbedder, HashEmbedder, KBError, KnowledgeBase,
                    UPLOAD_EXTS)
    from memory import PROFILE_ACTIONS, MemoryStore, PROFILE_ACTIONS
    from mcp import MCPError

# Секреты/настройки — из .env в корне репозитория.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# Данные лежат в studio/data (создаётся при первой записи, в git не коммитится).
DATA_DIR = str(Path(__file__).resolve().parents[1] / "data")

# База знаний (день 21): <repo>/data/kb — тот же корень репозитория, что и
# дефолтный repo_root в kb.py (два уровня вверх от studio/backend).
KB_DIR = str(Path(__file__).resolve().parents[2] / "data" / "kb")

# Максимальная длина текста результата tools/call в сообщении диалога.
MCP_RESULT_MAX = 8000


def _format_mcp_result(result: dict) -> str:
    """Текст результата tools/call для сообщения диалога: только text-части;
    пусто — «(пусто)»; isError — префикс «Ошибка: »; длиннее MCP_RESULT_MAX —
    обрезка с пометкой."""
    text = "\n".join(p.get("text", "") for p in (result.get("content") or [])
                     if isinstance(p, dict) and p.get("type") == "text")
    if not text:
        text = "(пусто)"
    if result.get("isError"):
        text = "Ошибка: " + text
    if len(text) > MCP_RESULT_MAX:
        text = text[:MCP_RESULT_MAX] + "\n…(обрезано)"
    return text


def _list_kb_uploads(kb_dir: str) -> list:
    """Загрузки пользователя (kb_dir/uploads): отсортированные имена,
    только файлы, {name, size}. Каталога нет → пустой список (список
    работает и до сборки индекса)."""
    up_dir = os.path.join(kb_dir, "uploads")
    if not os.path.isdir(up_dir):
        return []
    return [{"name": fn, "size": os.path.getsize(os.path.join(up_dir, fn))}
            for fn in sorted(os.listdir(up_dir))
            if os.path.isfile(os.path.join(up_dir, fn))]


def create_app(agent: StudioAgent | None = None,
               kb: "KnowledgeBase | None" = None) -> FastAPI:
    """Создаёт FastAPI-приложение. agent и kb inject-ятся для тестов;
    по умолчанию — StudioAgent(DATA_DIR) и KnowledgeBase(KB_DIR) (день 21)."""
    agent = agent or StudioAgent(DATA_DIR)
    kb = kb or KnowledgeBase(KB_DIR)
    app = FastAPI(title="Студия")

    @app.on_event("shutdown")
    def _mcp_shutdown():
        """Закрыть все MCP-сессии при остановке приложения."""
        agent.mcp.close_all()

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
        Body: {dialogue_id, action}. Ответ: {profile, interview_text} —
        interview_text = константа с 4 вопросами анкеты (день 20), чтобы
        вкладка «Профили» UI показывала вопросы при явном интервью."""
        if "dialogue_id" not in body or "action" not in body:
            raise HTTPException(400, "Не указаны dialogue_id или action")
        if body["action"] not in PROFILE_ACTIONS:
            raise HTTPException(400, f"Неизвестное действие: {body['action']}")
        try:
            p = agent.store.profile_action(body["dialogue_id"], body["action"])
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"profile": p, "interview_text": PROFILE_INTERVIEW_TEXT}

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
            try:
                for event in agent.task_run(dialogue_id):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                # День 15: непойманное исключение внутри task_run (напр. ValueError
                # из task_retry_execution при паузе во время валидации) раньше
                # убивало SSE-поток. Теперь отдаём error-кадр, а не падение.
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

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

    @app.post("/api/task/approve")
    def task_approve(body: dict):
        """День 15: одобрить план (plan_review → execution).
        Body: {dialogue_id}."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        try:
            t = agent.store.task_approve(dialogue_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task": t}

    @app.post("/api/task/reject")
    def task_reject(body: dict):
        """День 15: отклонить план (plan_review → planning, запись planning
        сбрасывается, повторный прогон планировщика).
        Body: {dialogue_id, note?}."""
        dialogue_id = body.get("dialogue_id")
        if not isinstance(dialogue_id, str) or not dialogue_id:
            raise HTTPException(400, "Не передан dialogue_id")
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        note = body.get("note", "")
        if not isinstance(note, str):
            raise HTTPException(400, "note должен быть строкой")
        try:
            t = agent.store.task_reject(dialogue_id, note.strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
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

    @app.post("/api/dialogues/{dialogue_id}/rag")
    def dialogues_set_rag(dialogue_id: str, body: dict):
        """Per-диалог RAG-режим (follow-up дня 22, body: {rag: bool}).
        400 — rag отсутствует или не настоящий bool (1/0/строки);
        404 — диалог не найден."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        if "rag" not in body or not isinstance(body["rag"], bool):
            raise HTTPException(400,
                                 "RAG-режим должен быть boolean (true/false)")
        agent.store.set_dialogue_rag(dialogue_id, body["rag"])
        return {"dialogue": agent.store.get_dialogue(dialogue_id)}

    @app.post("/api/dialogues/{dialogue_id}/activate")
    def dialogues_activate(dialogue_id: str):
        """Сделать диалог активным."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        agent.store.activate(dialogue_id)
        return {"active_id": agent.store.active_id()}

    # ---------- память задачи (день 25) ----------

    @app.get("/api/dialogues/{dialogue_id}/task-state")
    def task_state_get(dialogue_id: str):
        """Память задачи диалога (день 25): {clarifications, constraints,
        goal}. 404 — диалог не найден."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        return {"task_state": agent.store.get_task_state(dialogue_id)}

    @app.post("/api/dialogues/{dialogue_id}/task-state")
    def task_state_update(dialogue_id: str, body: dict):
        """Записать память задачи диалога (день 25, целое состояние):
        body {clarifications: [str], constraints: [str], goal: str}
        (поля опциональны — отсутствуют = не менять? нет: запись
        целого состояния, отсутствующие поля = пустые).
        400 — некорректные поля; 404 — диалог не найден."""
        if agent.store.get_dialogue(dialogue_id) is None:
            raise HTTPException(404, f"Диалог «{dialogue_id}» не найден")
        for key, ru in (("clarifications", "clarifications должен быть списком строк"),
                        ("constraints", "constraints должен быть списком строк"),
                        ("goal", "goal должен быть строкой")):
            if key not in body:
                raise HTTPException(400, ru)
        try:
            ts = agent.store.update_task_state(dialogue_id, body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"task_state": ts}

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
        """Список инвариантов: [{id, title, description, forbidden, is_active}]."""
        items = agent.store.invariants_items()
        return {"invariants": [
            {"id": i, "title": e["title"], "description": e["description"],
             "forbidden": e["forbidden"], "is_active": e["is_active"]}
            for i, e in items.items()]}

    @app.post("/api/invariants", status_code=201)
    def invariants_set(body: dict):
        """Создать/обновить инвариант {title, description, forbidden?,
        is_active?} (обновление — по title). 400 — поле отсутствует,
        title/description не строка или пустые, forbidden не список str,
        is_active не bool."""
        for k in ("title", "description"):
            if k not in body:
                raise HTTPException(400, f"Не указано поле «{k}»")
        title = body["title"]
        description = body["description"]
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(400, "Название инварианта должно быть непустой строкой")
        if not isinstance(description, str) or not description.strip():
            raise HTTPException(400, "Описание инварианта должно быть непустой строкой")
        forbidden = body.get("forbidden")
        if forbidden is not None and (
                not isinstance(forbidden, list)
                or not all(isinstance(s, str) for s in forbidden)):
            raise HTTPException(400, "forbidden должен быть списком строк")
        if "is_active" in body and not isinstance(body["is_active"], bool):
            raise HTTPException(400, "is_active должен быть bool (true/false)")
        try:
            rec = agent.store.invariants_set(title, description, forbidden,
                                             body.get("is_active", True))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"invariant": rec}

    @app.delete("/api/invariants/{iid}")
    def invariants_delete(iid: str):
        """Удалить инвариант по id; 404 — не найден."""
        if not agent.store.invariants_remove(iid):
            raise HTTPException(404, f"Инвариант «{iid}» не найден")
        return {"ok": True}

    @app.post("/api/invariants/{iid}/toggle")
    def invariants_toggle(iid: str, body: dict | None = None):
        """Включить/отключить инвариант: body {is_active} или flip
        текущего значения (body опционально). 404 — не найден; 400 —
        is_active не bool."""
        items = agent.store.invariants_items()
        if iid not in items:
            raise HTTPException(404, f"Инвариант «{iid}» не найден")
        body = body or {}
        if "is_active" in body:
            if not isinstance(body["is_active"], bool):
                raise HTTPException(400, "is_active должен быть bool (true/false)")
            active = body["is_active"]
        else:
            active = not items[iid]["is_active"]
        rec = agent.store.invariants_set_active(iid, active)
        if rec is None:
            raise HTTPException(404, f"Инвариант «{iid}» не найден")
        return {"invariant": rec}

    # ---------- MCP-серверы (день 16) ----------

    @app.get("/api/mcp/servers")
    def mcp_servers_list():
        """Реестр MCP-серверов с runtime-статусом."""
        return {"servers": agent.mcp.servers()}

    @app.post("/api/mcp/servers", status_code=201)
    def mcp_servers_add(body: dict):
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return JSONResponse(status_code=400,
                                content={"detail": "Название обязательно"})
        if body.get("type") not in ("stdio", "http"):
            return JSONResponse(status_code=400,
                                content={"detail": "Тип: 'stdio' или 'http'"})
        if not isinstance(body.get("enabled", True), bool):
            return JSONResponse(status_code=400,
                                content={"detail": "enabled — bool"})
        try:
            rec = agent.mcp.add(name, body.get("type"),
                                body.get("command"), body.get("url"),
                                body.get("env"), body.get("enabled", True))
        except ValueError as e:
            return JSONResponse(status_code=400, content={"detail": str(e)})
        return {"server": rec}

    @app.delete("/api/mcp/servers/{sid}")
    def mcp_servers_delete(sid: str):
        if not agent.mcp.remove(sid):
            return JSONResponse(status_code=404,
                                content={"detail": "Сервер не найден"})
        return {"ok": True}

    @app.post("/api/mcp/servers/{sid}/connect")
    def mcp_servers_connect(sid: str):
        try:
            view = agent.mcp.connect(sid)
        except KeyError:
            return JSONResponse(status_code=404,
                                 content={"detail": "Сервер не найден"})
        return {"server": view}

    @app.post("/api/mcp/servers/{sid}/disconnect")
    def mcp_servers_disconnect(sid: str):
        try:
            view = agent.mcp.disconnect(sid)
        except KeyError:
            return JSONResponse(status_code=404,
                                 content={"detail": "Сервер не найден"})
        return {"server": view}

    @app.get("/api/mcp/tools")
    def mcp_tools():
        """Инструменты подключённых MCP-серверов."""
        return {"tools": agent.mcp.tools()}

    @app.post("/api/mcp/servers/{sid}/tools/{tool}")
    def mcp_tool_call(sid: str, tool: str, body: dict):
        """Вызвать инструмент подключённого MCP-сервера (tool-loop, день 16):
        результат сохраняется в диалог как system-сообщение с маркером
        mcp_tool — видно LLM в следующих запросах. 400/404 — RU-detail."""
        dialogue_id = body.get("dialogue_id")
        arguments = body.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(dialogue_id, str) or not dialogue_id:
            return JSONResponse(status_code=400,
                                content={"detail": "dialogue_id обязательно"})
        if not isinstance(arguments, dict):
            return JSONResponse(status_code=400,
                                content={"detail": "arguments — объект"})
        if agent.store.get_dialogue(dialogue_id) is None:
            return JSONResponse(status_code=404,
                                content={"detail":
                                         f"Диалог «{dialogue_id}» не найден"})
        try:
            result = agent.mcp.call_tool(sid, tool, arguments)
        except KeyError:
            return JSONResponse(status_code=404,
                                content={"detail": "Сервер не найден"})
        except MCPError as e:
            return JSONResponse(status_code=400, content={"detail": str(e)})
        server_name = sid
        for s in agent.mcp.servers():
            if s.get("id") == sid:
                server_name = s.get("name", sid)
                break
        args_json = (json.dumps(arguments, ensure_ascii=False)
                     if arguments else "(пусто)")
        block = (f"MCP-вызов: {server_name}/{tool}\n"
                 f"Аргументы: {args_json}\n"
                 f"Результат:\n{_format_mcp_result(result)}")
        agent.store.append_message(dialogue_id, "system", block,
                                   mcp_tool={"server": sid, "tool": tool})
        return {"ok": True}

    # ---------- база знаний (день 21) ----------

    @app.get("/api/kb/stats")
    def kb_stats():
        """Статистика индекса базы знаний; 404 «Индекс не построен» —
        если index.json отсутствует."""
        idx = kb.load_index()
        if idx is None:
            raise HTTPException(404, "Индекс не построен")
        files = sorted({c.get("file") for c in idx.get("chunks", [])
                        if c.get("file")})
        # Загрузки пользователя (data/kb/uploads): видны сразу после
        # upload, даже до пересборки индекса (в files попадают только
        # после «Индексировать»)
        uploads = _list_kb_uploads(kb.kb_dir)
        # Корпус, который ещё не попал в индекс (новые загрузки/доки)
        try:
            corpus = {d.path for d in kb.corpus_files()}
        except Exception:
            corpus = set()
        pending_files = sorted(corpus - set(files))
        return {"exists": True, "strategy": idx.get("strategy"),
                "embedder": idx.get("embedder"), "dim": idx.get("dim"),
                "built_at": idx.get("built_at"), "stats": idx.get("stats"),
                "comparison": idx.get("comparison"), "files": files,
                "uploads": uploads, "pending_files": pending_files,
                # Ключ реранкера (GPUSTACK_KEY_RERANK) настроен — для
                # бейджа «ключ не настроен» в UI при reranker=api
                "reranker_key_configured":
                    bool(os.environ.get("GPUSTACK_KEY_RERANK", ""))}

    @app.get("/api/kb/uploads")
    def kb_uploads():
        """Список загруженных файлов (data/kb/uploads):
        {uploads: [{name, size}]}. Работает БЕЗ собранного индекса
        (stats при этом 404); загрузок нет — пустой список."""
        return {"uploads": _list_kb_uploads(kb.kb_dir)}

    @app.get("/api/kb/build-status")
    def kb_build_status():
        """Прогресс сборки индекса (polling из UI): {running, phase,
        done, total}. Вне сборки — running: False."""
        return kb.progress()

    @app.delete("/api/kb/uploads/{name}")
    def kb_delete_upload(name: str):
        """Удалить загруженный файл (data/kb/uploads) + его чанки из
        индекса. 400 — некорректное имя (traversal), 404 — файла нет."""
        if kb.progress().get("running"):
            raise HTTPException(409, "Сборка индекса уже идёт")
        try:
            return kb.delete_upload(name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.delete("/api/kb")
    def kb_wipe():
        """Полная очист базы знаний: все загрузки + index.json
        (settings сохраняются). Ответ: {ok, uploads_removed}."""
        if kb.progress().get("running"):
            raise HTTPException(409, "Сборка индекса уже идёт")
        return kb.wipe()

    @app.post("/api/kb/index")
    def kb_index(body: dict):
        """Построить индекс: body {strategy: fixed|structural,
        embedder: hash|api, mode?: auto|full|incremental}.
        mode auto (дефолт): совпадении strategy+embedder с индексом —
        инкрементальная сборка (только новые файлы), иначе полная.
        400 — некорректные значения / ключ API-эмбеддера не настроен;
        409 — сборка уже идёт; 502 — сбой эмбеддинг-API. Ответ — результат
        сборки {stats, comparison, strategy, mode, added}."""
        strategy = body.get("strategy")
        embedder_name = body.get("embedder")
        mode = body.get("mode", "auto")
        if strategy not in ("fixed", "structural"):
            raise HTTPException(400, "strategy: fixed|structural")
        if embedder_name not in ("hash", "api"):
            raise HTTPException(400, "embedder: hash|api")
        if mode not in ("auto", "full", "incremental"):
            raise HTTPException(400, "mode: auto|full|incremental")
        if kb.progress().get("running"):
            raise HTTPException(409, "Сборка индекса уже идёт")
        if embedder_name == "hash":
            embedder = HashEmbedder()
        else:
            key = os.environ.get("GPUSTACK_KEY_EMBED", "")
            if not key:
                raise HTTPException(
                    400, "Ключ эмбеддингов не настроен (GPUSTACK_KEY_EMBED)")
            base = os.environ.get("GPUSTACK_BASE_URL",
                                  "https://gpustack.data.lmru.tech/v1")
            embedder = APIEmbedder(base, key)
        try:
            return kb.build(strategy, embedder, mode=mode)
        except KBError as e:
            raise HTTPException(502, f"Не удалось построить индекс: {e}")
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/kb/upload")
    async def kb_upload(file: UploadFile = File(...)):
        """Загрузить файл в kb_dir/uploads (multipart, поле «file»).
        400 — неподдерживаемый формат; имя — только basename
        (traversal-safe). Ответ: {ok, file, size}."""
        name = os.path.basename(file.filename or "")
        if not name or os.path.splitext(name)[1].lower() not in UPLOAD_EXTS:
            raise HTTPException(400, "Неподдерживаемый формат файла")
        data = await file.read()
        dest = os.path.join(kb.kb_dir, "uploads", name)
        with open(dest, "wb") as f:
            f.write(data)
        return {"ok": True, "file": name, "size": len(data)}

    @app.get("/api/kb/search")
    def kb_search(q: str = "", k: int = 5):
        """Двухэтапный поиск RAG: этап 1 — гибридный top-rag_recall
        (настройка), этап 2 — реранкер (настройка reranker) и top-k.
        Ответ: {results, recall_total, reranked}. 400 — пустой q;
        404 — индекс не построен; 400 — прочие ошибки БЗ."""
        if not q.strip():
            raise HTTPException(400, "Запрос (q) не может быть пустым")
        try:
            s = kb.settings()
            rag = kb.search_rag(q, s["rag_recall"], k, s["reranker"],
                                min_score=s["min_score"])
        except KBError as e:
            if str(e) == "Индекс не построен":
                raise HTTPException(404, "Индекс не построен")
            raise HTTPException(400, str(e))
        resp = {"results": rag["results"],
                "recall_total": rag["recall_total"],
                "reranked": rag["reranked"]}
        if "filtered" in rag:  # день 23: аддитивно при min_score > 0
            resp["filtered"] = rag["filtered"]
            resp["dropped"] = rag["dropped"]
        return resp

    @app.get("/api/kb/settings")
    def kb_settings_get():
        """Текущие настройки БЗ (agent_loop, rag, rag_top_k, strategy,
        embedder)."""
        return kb.settings()

    @app.post("/api/kb/settings")
    def kb_settings_set(body: dict):
        """Частичное обновление настроек БЗ; 400 — некорректное значение."""
        try:
            return kb.update_settings(body)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # ---------- сравнение RAG (день 22) ----------

    @app.post("/api/rag/compare")
    def rag_compare(body: dict):
        """День 22 (+ день 23): ответ на вопрос 4 способами — plain /
        rag / rag+filter / rag+rewrite — одним вызовом: все LLM-вызовы
        non-stream (T=0, max_tokens=1024, голый system-промпт),
        retrieval из search_rag по настройкам БЗ (флаг settings['rag']
        не consulted — сравнение явное). Журнал requests.json НЕ
        пишется — non-stream паттерн, как `_task_llm_call`. Ответ:
        {answer_plain, answer_rag, kb_block, chunks, rag_context} +
        аддитивно (день 23) answer_rag_filter / answer_rag_rewrite /
        chunks_rag_filter / chunks_rag_rewrite /
        rag_context_rag_filter / rag_context_rag_rewrite /
        rewritten_query / rewrite_applied. Body: {question, min_score?}
        — min_score опциональный override порога для rag+filter-руки
        (валидация как в update_settings: bool ПЕРВЫЙ, затем тип,
        затем диапазон; default — settings["min_score"]).
        400 — вопрос пустой/не строка; 400 RU — некорректный min_score;
        404 — индекс не построен."""
        q = body.get("question")
        if not isinstance(q, str) or not q.strip():
            raise HTTPException(400, "Вопрос не может быть пустым")
        min_score = None
        if "min_score" in body:
            v = body["min_score"]
            # bool — ПЕРВЫЙ: isinstance(True, int) == True (паттерн
            # kb.update_settings, день 23)
            if isinstance(v, bool) \
                    or not isinstance(v, (int, float)) \
                    or not (0.0 <= v <= 1.0):
                raise HTTPException(
                    400, "min_score должен быть числом от 0 до 1")
            min_score = float(v)
        if kb.load_index() is None:
            raise HTTPException(404, "Индекс не построен")
        return agent.rag_compare(q, min_score=min_score)

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
