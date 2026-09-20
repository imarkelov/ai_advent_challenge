# Day 13b: Task Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Задача = запрос пользователя в режиме «задача»: пайплайн planning → execution (N work-шагов, по LLM-вызову на шаг) → validation → done, видимый спавн stage-агентов и живой вывод в чате, карточка процесса со сворачиванием, тумблер режимов чат/задача, unified FSM (`planning|execution|validation|done|paused|failed`), без вкладки «Задача».

**Architecture:** Расширение hand-rolled оркестратора `agent.task_run` (день 13): новая схема хранения (unified `stage`, `task_id`, `plan[]`, `work_steps[]`, `expected_action`, `context_snapshot`), пошаговое streaming-исполнение, новые SSE-события, маркеры сообщений. Фронтенд: `TaskCard` inline в потоке чата, тумблер в `ChatPanel`, удаление `TaskTab` и 5-й вкладки. 0 новых зависимостей (design D1).

**Tech Stack:** Python 3.11 / FastAPI / httpx (MockTransport) / pytest; React 19 + Vite + TypeScript / Vitest + Testing Library; e2e — `scripts/e2e_studio.py` (prod-сервер :8100, реальный GPustack).

**Spec:** `openspec/changes/day13b-task-redesign/` (proposal.md, specs/task-state-machine/spec.md, design.md, tasks.md).

## Global Constraints

- Ветка: `day13-task-state-machine`. Код дней 1–12 не трогаем, кроме точечных изменений, указанных в задаче.
- Тексты UI/API-ошибок — русский; API-ошибки — `HTTPException(400|404, "RU-текст")`.
- Протокол `POST /api/chat` (`delta`/`done`/`error`), слои памяти, MEMORY_RULE, конфликт-гард, профиль (день 12), авто-название (день 11) — не меняем.
- Stage/work-вызовы LLM НЕ пишутся в `requests.json` (паттерн авто-заголовка дня 11 / экстракта профиля дня 12).
- LLM не управляет FSM: все переходы, паузы, ретраи — код.
- Task-промпты собираются ТОЛЬКО из состояния задачи (`description`, `plan`, `work_steps`, `instruction`); история сообщений чата в task-промпты не уходит (чат — лишь представление, design D5).
- Бэкенд-тесты — офлайн (`tmp_path` + `httpx.MockTransport`, без сети); фронтенд — Vitest со stub `fetch`.
- Коммиты: `feat(day13b-task): …`, `test(day13b-task): …`, `docs(day13b-task): …`.
- Команды верификации: `cd studio/backend; python -m pytest -q`; `cd studio/frontend; npm test`; `cd studio/frontend; npx tsc --noEmit`.
- Иконки — inline SVG (паттерн ProfileTab), не эмодзи.

## Схема состояния задачи (поле `task` записи диалога)

```json
{
  "active": true,
  "task_id": "t_a1b2c3d4e5f6",
  "stage": "execution",
  "current_step": 2,
  "total_steps": 4,
  "expected_action": "agent_response",
  "plan": [
    {"step": 1, "agent": "planning",   "status": "completed",   "output": "[\"…\"]", "verdict": null, "spawn_ts": "…", "ts": "…"},
    {"step": 2, "agent": "execution",  "status": "in_progress", "output": null,       "verdict": null, "spawn_ts": "…", "ts": null},
    {"step": 3, "agent": "validation", "status": "pending",     "output": null,       "verdict": null, "spawn_ts": null, "ts": null},
    {"step": 4, "agent": "done",       "status": "pending",     "output": null,       "verdict": null, "spawn_ts": null, "ts": null}
  ],
  "work_steps": [
    {"name": "Анализ ЦА", "status": "completed", "output": "…", "ts": "…"},
    {"name": "Генерация офферов", "status": "in_progress", "output": null, "ts": null}
  ],
  "context_snapshot": {"description": "…", "work_steps": [{"name": "…", "status": "…", "output": "…", "ts": "…"}], "instruction": ""},
  "description": "…",
  "instruction": "",
  "retries": 0,
  "error": null,
  "updated": "YYYY-MM-DD HH:MM:SS"
}
```

- `stage` — unified: одна из 6; `paused`/`failed` — значения стадии, не флаги (D4).
- Позиция при resume/повторе — производная: первая невыполненная запись `plan[]` (`_current_stage_of`).
- Нет поля `task` ИЛИ запись без `task_id` (старая схема дня 13) = задача неактивна (бэкворд-совместимость).
- `task_id` — `"t_" + uuid4().hex[:12]`; новая задача (`start` после done/failed, D9) — новый `task_id`.

## SSE-контракт `POST /api/task/run` (кадры `data: {json}\n\n`)

```
{"type":"agent_spawned","stage":"planning","agent":"Планировщик"}
{"type":"stage_done","stage":"planning","output":"[…]","plan":["Анализ ЦА","Генерация офферов"]}
{"type":"agent_spawned","stage":"execution","agent":"Исполнитель"}
{"type":"step_updated","index":0,"name":"Анализ ЦА","status":"in_progress"}
{"type":"step_delta","index":0,"text":"кусок"}
{"type":"step_updated","index":0,"name":"Анализ ЦА","status":"completed","output":"…"}
{"type":"stage_done","stage":"execution","output":"## Анализ ЦА\n…"}
{"type":"agent_spawned","stage":"validation","agent":"Валидатор"}
{"type":"stage_done","stage":"validation","output":"…","verdict":"pass"}
{"type":"agent_spawned","stage":"done","agent":"Оркестратор"}
{"type":"stage_done","stage":"done","output":"…"}
{"type":"task_done","answer":"…"}
```

Плюс: `{"type":"task_paused","stage":"paused"}`, `{"type":"task_resumed","stage":"…"}`, `{"type":"task_failed","message":"…"}`, `{"type":"error","message":"…"}`.

Семантика: `agent_spawned` — спавн stage-агента (в записях `plan[]` фиксируется `spawn_ts`); `step_updated`/`step_delta` — работа work-шага (только в стадии execution); `stage_done` — вывод стадии (+`verdict` для validation, +`plan` для planning, +`retry` при ретрае); `task_done` — финальный синтез; `task_failed` — ошибка LLM-вызова (stage→failed).

## Маркеры сообщений (в `messages` диалога, в тело LLM не уходят)

- User-запрос: `{role:"user", content, task_id}` — якорь карточки в ленте.
- Вывод стадии/шага: `{role:"assistant", content, model, task_id, task_stage: "planning"|"execution"|"validation", task_step (только для work-шага)}`.
- Финальный синтез: assistant-сообщение с `task_id` и `model`, **без** `task_stage` — рендерится как обычный bubble.
- Сообщения с `task_stage` НЕ рендерятся как bubble (вывод показан в секции карточки).

---

### Task 1: Базовая линия

**Files:** — (только проверки)

- [ ] **Step 1: Проверить ветку**

```powershell
git branch --show-current
```
Expected: `day13-task-state-machine`.

- [ ] **Step 2: Baseline-тесты**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
```
Expected: все PASS (базовые ~191 backend / ~99 frontend; точные числа — зафиксировать). Pre-existing failure — зафиксировать и сообщить, не чинить.

### Task 2: Хранилище: новая схема состояния задачи (memory.py)

**Files:**
- Modify: `studio/backend/memory.py` — L66-75 (константы/`new_task`), L213-232 (`append_message`), L310-466 (секция task-методов)
- Test: `studio/backend/tests/test_memory.py` — раздел task-тестов ПЕРЕПИСАТЬ под новую схему (старые удалить)

**Interfaces (produces):**
- `TASK_PIPELINE = ("planning","execution","validation","done")`; `TASK_STAGES = ("planning","execution","validation","done","paused","failed")`
- `new_task() -> dict` (новая схема, `active=False`); `new_plan() -> list` (4 записи pending); `_current_stage_of(t) -> str | None` (модульная функция)
- `MemoryStore.append_message(dialogue_id, role, content, model=None, task_stage=None, task_id=None, task_step=None)`
- `task_new` (ValueError: диалог нет / активна и не done/failed; при done/failed — новая, новый `task_id`)
- `task_spawn_stage(dialogue_id, stage)` (ValueError: не активна / стадия не в pipeline / стадия != текущей по плану)
- `task_work_steps_set(dialogue_id, names)`; `task_work_step_set(dialogue_id, index, status, output=None)` (ValueError: индекс вне диапазона / статус неизвестен)
- `task_stage_done(dialogue_id, stage, output, verdict=None)` (ValueError: не активна / стадия не в pipeline / стадия != текущей по плану)
- `task_retry_execution(dialogue_id, output, verdict)` (ValueError: не активна / stage != validation / retries >= 1)
- `task_pause` (ValueError: не активна / stage в (done, failed)); `task_resume` (ValueError: не активна / stage не в (paused, failed) / план полностью выполнен)
- `task_set_failed(dialogue_id, message)`; `task_set_instruction` (ValueError: не активна / stage != paused); `task_instruction_take` (как раньше); `task_reset` (как раньше)

- [ ] **Step 1: Failing-тесты** — в `studio/backend/tests/test_memory.py` заменить task-раздел на:

```python
class TestTaskStorage13b:
    def setup_method(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.s = MemoryStore(self.d.name)
        self.did = self.s.new_dialogue()["id"]

    def teardown_method(self):
        self.d.cleanup()

    # ---- бэкворд-совместимость ----

    def test_no_task_field_is_inactive(self):
        t = self.s.task_get(self.did)
        assert t["active"] is False
        assert t["task_id"] is None
        assert t["plan"] == []
        assert t["stage"] is None

    def test_old_schema_without_task_id_is_inactive(self):
        # запись дня 13 (без task_id, со stages) читается как неактивная
        with self.s._lock:
            data = self.s._read_dialogues()
            d = self.s._find(data, self.did)
            d["task"] = {"active": True, "stage": "execution", "paused": False,
                         "description": "old", "instruction": "",
                         "stages": {"planning": {"output": "x", "ts": "…", "verdict": None}},
                         "retries": 0, "error": None, "updated": None}
            self.s._write_dialogues(data)
        assert self.s.task_get(self.did)["active"] is False

    # ---- task_new ----

    def test_task_new_creates_planning(self):
        t = self.s.task_new(self.did, "Сделать план")
        assert t["active"] is True
        assert t["stage"] == "planning"
        assert t["current_step"] == 1 and t["total_steps"] == 4
        assert t["expected_action"] == "agent_response"
        assert t["task_id"].startswith("t_")
        assert [e["agent"] for e in t["plan"]] == ["planning", "execution", "validation", "done"]
        assert all(e["status"] == "pending" for e in t["plan"])
        assert t["work_steps"] == []
        assert t["context_snapshot"] is None

    def test_task_new_rejects_active(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_new(self.did, "б")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "уже активна" in str(e)

    def test_task_new_after_done_is_new_task(self):
        t1 = self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "план")
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_stage_done(self.did, "execution", "работа")
        self.s.task_spawn_stage(self.did, "validation")
        self.s.task_stage_done(self.did, "validation", "ок", "pass")
        self.s.task_spawn_stage(self.did, "done")
        self.s.task_stage_done(self.did, "done", "итог")
        assert self.s.task_get(self.did)["stage"] == "done"
        t2 = self.s.task_new(self.did, "б")
        assert t2["task_id"] != t1["task_id"]
        assert t2["stage"] == "planning" and t2["description"] == "б"

    def test_task_new_after_failed_is_new_task(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_set_failed(self.did, "сбой")
        t2 = self.s.task_new(self.did, "б")
        assert t2["active"] is True and t2["stage"] == "planning"

    def test_task_new_unknown_dialogue(self):
        try:
            self.s.task_new("nope", "а")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "не найден" in str(e)

    # ---- spawn / stage_done ----

    def test_spawn_marks_in_progress(self):
        self.s.task_new(self.did, "а")
        t = self.s.task_spawn_stage(self.did, "planning")
        e = t["plan"][0]
        assert e["status"] == "in_progress" and e["spawn_ts"] is not None
        assert t["stage"] == "planning" and t["expected_action"] == "agent_response"

    def test_spawn_wrong_stage_rejected(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_spawn_stage(self.did, "validation")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "не совпадает" in str(e)

    def test_stage_done_advances(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        t = self.s.task_stage_done(self.did, "planning", "план")
        assert t["plan"][0]["status"] == "completed" and t["plan"][0]["output"] == "план"
        assert t["stage"] == "execution" and t["current_step"] == 2

    def test_stage_done_wrong_stage_rejected(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_stage_done(self.did, "execution", "х")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_stage_done_terminal(self):
        self.s.task_new(self.did, "а")
        for st, out in (("planning", "п"), ("execution", "р"), ("validation", "в")):
            self.s.task_spawn_stage(self.did, st)
            self.s.task_stage_done(self.did, st, out)
        self.s.task_spawn_stage(self.did, "done")
        t = self.s.task_stage_done(self.did, "done", "итог")
        assert t["stage"] == "done" and t["current_step"] == 4

    def test_verdict_stored_on_validation(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_stage_done(self.did, "execution", "р")
        self.s.task_spawn_stage(self.did, "validation")
        t = self.s.task_stage_done(self.did, "validation", "в", "fail")
        assert t["plan"][2]["verdict"] == "fail"

    # ---- work_steps ----

    def test_work_steps_set(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "[1,2]")
        t = self.s.task_work_steps_set(self.did, ["Шаг A", "Шаг B"])
        assert [w["name"] for w in t["work_steps"]] == ["Шаг A", "Шаг B"]
        assert all(w["status"] == "pending" for w in t["work_steps"])

    def test_work_step_progress(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        t = self.s.task_work_step_set(self.did, 0, "in_progress")
        assert t["work_steps"][0]["status"] == "in_progress"
        t = self.s.task_work_step_set(self.did, 0, "completed", "выполнено")
        assert t["work_steps"][0]["output"] == "выполнено"
        assert t["work_steps"][0]["ts"] is not None

    def test_work_step_bad_index(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_work_step_set(self.did, 3, "in_progress")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    # ---- retry ----

    def test_retry_execution_resets_steps(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_work_step_set(self.did, 0, "completed", "A-ок")
        self.s.task_stage_done(self.did, "execution", "раб")
        self.s.task_spawn_stage(self.did, "validation")
        t = self.s.task_retry_execution(self.did, "плохо", "fail")
        assert t["stage"] == "execution" and t["retries"] == 1
        assert t["plan"][2]["verdict"] == "fail"
        assert all(w["status"] == "pending" for w in t["work_steps"])
        assert t["plan"][1]["status"] == "pending"

    def test_retry_rejected_outside_validation(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_spawn_stage(self.did, "execution")
        try:
            self.s.task_retry_execution(self.did, "x", "fail")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    # ---- pause / resume / failed ----

    def test_pause_sets_stage_and_snapshot(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_work_step_set(self.did, 0, "completed", "A-ок")
        t = self.s.task_pause(self.did)
        assert t["stage"] == "paused" and t["expected_action"] == "resume_wait"
        assert t["context_snapshot"]["description"] == "а"
        assert len(t["context_snapshot"]["work_steps"]) == 2

    def test_pause_rejected_on_done_and_failed(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_set_failed(self.did, "сбой")
        try:
            self.s.task_pause(self.did)
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_resume_restores_first_incomplete(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_pause(self.did)
        t = self.s.task_resume(self.did)
        assert t["stage"] == "execution" and t["expected_action"] == "agent_response"

    def test_resume_without_pause_rejected(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_resume(self.did)
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_failed_and_retry_via_resume(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_spawn_stage(self.did, "execution")
        t = self.s.task_set_failed(self.did, "модель не ответила")
        assert t["stage"] == "failed" and t["error"] == "модель не ответила"
        assert t["expected_action"] == "resume_wait"
        t = self.s.task_resume(self.did)
        assert t["stage"] == "execution" and t["error"] is None

    def test_instruction_only_on_pause(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_set_instruction(self.did, "текст")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "только на паузе" in str(e)
        self.s.task_pause(self.did)
        t = self.s.task_set_instruction(self.did, "текст")
        assert t["instruction"] == "текст" and t["expected_action"] == "human_input"
        assert self.s.task_instruction_take(self.did) == "текст"
        assert self.s.task_get(self.did)["instruction"] == ""

    # ---- reset / выдача ----

    def test_reset(self):
        self.s.task_new(self.did, "а")
        t = self.s.task_reset(self.did)
        assert t["active"] is False and t["task_id"] is None

    def test_task_in_dialogue_outputs(self):
        self.s.task_new(self.did, "а")
        assert self.s.list_dialogues()[0]["task"]["active"] is True
        assert self.s.get_dialogue(self.did)["task"]["active"] is True

    # ---- маркеры сообщений ----

    def test_append_message_task_markers(self):
        m = self.s
        m.append_message(self.did, "user", "запрос", task_id="t_1")
        m.append_message(self.did, "assistant", "план", model="m", task_id="t_1", task_stage="planning")
        m.append_message(self.did, "assistant", "шаг", model="m", task_id="t_1",
                         task_stage="execution", task_step="Шаг A")
        msgs = m.get_messages(self.did)
        assert msgs[0]["task_id"] == "t_1" and "task_stage" not in msgs[0]
        assert msgs[1]["task_stage"] == "planning" and "task_step" not in msgs[1]
        assert msgs[2]["task_step"] == "Шаг A"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -k TestTaskStorage13b -q`
Expected: FAIL (методы/схема новые; `test_no_task_field_is_inactive` и `test_old_schema_without_task_id_is_inactive` могут частично пройти — остальной блок фейлит).

- [ ] **Step 3: Реализация в `memory.py`** — см. Task 2b (ниже).

### Task 2b: Реализация новой схемы (memory.py)

**Files:** Modify: `studio/backend/memory.py`

- [ ] **Step 1: Заменить L66-75** (старые `TASK_STAGES`/`new_task`) на:

```python
# Состояние задачи (день 13b): unified FSM per-диалог, план stage-агентов,
# work-шаги, снапшот контекста. Поле «task» записи диалога; отсутствующее
# поле (или старая схема без task_id) = неактивная задача.
TASK_PIPELINE = ("planning", "execution", "validation", "done")
TASK_STAGES = ("planning", "execution", "validation", "done", "paused", "failed")


def new_plan() -> list:
    """План stage-агентов: 4 записи pending (порядок TASK_PIPELINE)."""
    return [{"step": i + 1, "agent": s, "status": "pending",
             "output": None, "verdict": None, "spawn_ts": None, "ts": None}
            for i, s in enumerate(TASK_PIPELINE)]


def new_task() -> dict:
    """Свежее (неактивное) состояние задачи (схема дня 13b)."""
    return {"active": False, "task_id": None, "stage": None,
            "current_step": 0, "total_steps": len(TASK_PIPELINE),
            "expected_action": None, "plan": [], "work_steps": [],
            "context_snapshot": None, "description": "", "instruction": "",
            "retries": 0, "error": None, "updated": None}


def _current_stage_of(t: dict) -> str | None:
    """Первая невыполненная стадия плана (позиция при resume/повторе);
    всё выполнено — None."""
    for e in t.get("plan") or []:
        if e.get("status") != "completed":
            return e.get("agent")
    return None
```

- [ ] **Step 2: `append_message` (L213-232)** — добавить параметры `task_id`, `task_step`:

```python
    def append_message(self, dialogue_id: str, role: str, content: str,
                       model: str | None = None,
                       task_stage: str | None = None,
                       task_id: str | None = None,
                       task_step: str | None = None) -> None:
        """Добавить сообщение в диалог; ValueError, если диалог не существует.

        model — метка модели (assistant); task_stage/task_id/task_step —
        маркеры задачи (день 13b, в тело LLM-запроса не уходят).
        """
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            msg = {"role": role, "content": content}
            if model is not None:
                msg["model"] = model
            if task_stage is not None:
                msg["task_stage"] = task_stage
            if task_id is not None:
                msg["task_id"] = task_id
            if task_step is not None:
                msg["task_step"] = task_step
            d.setdefault("messages", []).append(msg)
            self._write_dialogues(data)
```

- [ ] **Step 3: Заменить секцию `# ---------- состояние задачи: FSM (день 13, per-диалог) ----------` (L310-466) целиком:**

`_task_mutate`, `task_get`, `task_instruction_take`, `task_reset` сохраняются БЕЗ ИЗМЕНЕНИЙ (они не зависят от схемы, кроме `new_task()`, который обновлён в Step 1). Остальное — заменить на:

```python
    def _task_of(self, d: dict) -> dict:
        """Состояние задачи записи диалога; отсутствие поля или старая
        схема (без task_id) — неактивная задача (бэкворд-совместимость)."""
        raw = d.get("task")
        if not isinstance(raw, dict) or not raw.get("task_id"):
            return new_task()
        t = new_task()
        for k in ("active", "task_id", "stage", "current_step", "total_steps",
                  "expected_action", "description", "instruction",
                  "retries", "error", "updated"):
            if k in raw:
                t[k] = raw[k]
        if isinstance(raw.get("plan"), list):
            t["plan"] = [e for e in raw["plan"] if isinstance(e, dict)]
        if isinstance(raw.get("work_steps"), list):
            t["work_steps"] = [e for e in raw["work_steps"] if isinstance(e, dict)]
        if isinstance(raw.get("context_snapshot"), dict):
            t["context_snapshot"] = raw["context_snapshot"]
        return t

    def task_new(self, dialogue_id: str, description: str) -> dict:
        """Создать задачу (stage=planning, новый task_id). ValueError: диалог
        не найден; задача активна и незавершена (done/failed). При завершённой
        (done/failed) — новая задача, старая остаётся в истории сообщений."""
        def fn(t):
            if t["active"] and t["stage"] not in ("done", "failed"):
                raise ValueError("Задача уже активна: завершите её (reset) перед новой")
            t.update({"active": True,
                      "task_id": "t_" + uuid.uuid4().hex[:12],
                      "stage": "planning", "current_step": 1,
                      "total_steps": len(TASK_PIPELINE),
                      "expected_action": "agent_response",
                      "plan": new_plan(), "work_steps": [],
                      "context_snapshot": None, "description": description,
                      "instruction": "", "retries": 0, "error": None})
        return self._task_mutate(dialogue_id, fn)

    def task_spawn_stage(self, dialogue_id: str, stage: str) -> dict:
        """Отметить спавн stage-агента: запись плана in_progress + spawn_ts,
        stage/current_step/expected_action обновлены. ValueError: задача не
        активна; стадия не в pipeline; стадия != текущей по плану."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if stage not in TASK_PIPELINE:
                raise ValueError(f"Неизвестная стадия: {stage}")
            cur = _current_stage_of(t)
            if stage != cur:
                raise ValueError(f"Стадия не совпадает: текущая «{cur}»")
            for e in t["plan"]:
                if e["agent"] == stage:
                    e["status"] = "in_progress"
                    e["spawn_ts"] = _now()
            t["stage"] = stage
            t["current_step"] = TASK_PIPELINE.index(stage) + 1
            t["expected_action"] = "agent_response"
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_work_steps_set(self, dialogue_id: str, names: list) -> dict:
        """Декомпозиция Планировщика: work_steps из списка имён (pending)."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            t["work_steps"] = [{"name": str(n), "status": "pending",
                                "output": None, "ts": None} for n in names]
        return self._task_mutate(dialogue_id, fn)

    def task_work_step_set(self, dialogue_id: str, index: int, status: str,
                           output: str | None = None) -> dict:
        """Статус/output work-шага по индексу. ValueError: задача не активна;
        индекс вне диапазона; статус неизвестен."""
        if status not in ("pending", "in_progress", "completed"):
            raise ValueError(f"Неизвестный статус шага: {status}")
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if not 0 <= index < len(t["work_steps"]):
                raise ValueError(f"Индекс шага вне диапазона: {index}")
            ws = t["work_steps"][index]
            ws["status"] = status
            if output is not None:
                ws["output"] = output
                ws["ts"] = _now()
        return self._task_mutate(dialogue_id, fn)

    def task_stage_done(self, dialogue_id: str, stage: str, output: str,
                        verdict: str | None = None) -> dict:
        """Отметить запись плана completed (+output, +verdict) и перейти к
        следующей стадии pipeline (done — терминальная). ValueError: задача
        не активна; стадия не в pipeline; стадия != текущей по плану."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if stage not in TASK_PIPELINE:
                raise ValueError(f"Неизвестная стадия: {stage}")
            if stage != _current_stage_of(t):
                raise ValueError(f"Стадия не совпадает: текущая «{_current_stage_of(t)}»")
            for e in t["plan"]:
                if e["agent"] == stage:
                    e["status"] = "completed"
                    e["output"] = output
                    e["ts"] = _now()
                    if verdict is not None:
                        e["verdict"] = verdict
            t["stage"] = "done" if stage == "done" \
                else TASK_PIPELINE[TASK_PIPELINE.index(stage) + 1]
            t["current_step"] = TASK_PIPELINE.index(stage) + 2
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_retry_execution(self, dialogue_id: str, output: str,
                             verdict: str) -> dict:
        """Валидация не прошла: validation→completed (+verdict), запись
        execution и work_steps → pending, retries+1, stage→execution.
        ValueError: не активна; stage != validation; ретраи исчерпаны."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if t["stage"] != "validation":
                raise ValueError("Ретрай доступен только на стадии validation")
            if t["retries"] >= 1:
                raise ValueError("Повтор execution уже был")
            for e in t["plan"]:
                if e["agent"] == "validation":
                    e["status"] = "completed"
                    e["output"] = output
                    e["verdict"] = verdict
                    e["ts"] = _now()
                if e["agent"] == "execution":
                    e["status"] = "pending"
                    e["output"] = None
                    e["spawn_ts"] = None
                    e["ts"] = None
            for ws in t["work_steps"]:
                ws["status"] = "pending"
                ws["output"] = None
                ws["ts"] = None
            t["retries"] += 1
            t["stage"] = "execution"
            t["current_step"] = TASK_PIPELINE.index("execution") + 1
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_pause(self, dialogue_id: str) -> dict:
        """Пауза: stage→paused, expected_action→resume_wait, фиксируется
        context_snapshot. ValueError: не активна; stage в (done, failed)."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if t["stage"] in ("done", "failed"):
                raise ValueError("Задача завершена или упала — пауза недоступна")
            t["context_snapshot"] = {
                "description": t["description"],
                "work_steps": [dict(ws) for ws in t["work_steps"]],
                "instruction": t["instruction"],
            }
            t["stage"] = "paused"
            t["expected_action"] = "resume_wait"
        return self._task_mutate(dialogue_id, fn)

    def task_resume(self, dialogue_id: str) -> dict:
        """Снять паузу/повтор после сбоя (stage из paused или failed):
        stage — первая невыполненная запись плана, expected_action→
        agent_response, error очищена. ValueError: не активна; stage не в
        (paused, failed); план полностью выполнен."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if t["stage"] not in ("paused", "failed"):
                raise ValueError("Задача не на паузе и не упала")
            cur = _current_stage_of(t)
            if cur is None:
                raise ValueError("Все стадии плана выполнены")
            t["stage"] = cur
            t["current_step"] = TASK_PIPELINE.index(cur) + 1
            t["expected_action"] = "agent_response"
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_set_failed(self, dialogue_id: str, message: str) -> dict:
        """Ошибка LLM-вызова: stage→failed, error=message,
        expected_action→resume_wait (повтор — через resume/run)."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            t["stage"] = "failed"
            t["error"] = message
            t["expected_action"] = "resume_wait"
        return self._task_mutate(dialogue_id, fn)

    def task_set_instruction(self, dialogue_id: str, text: str) -> dict:
        """Инструкция пользователя — только на паузе. ValueError: не активна;
        stage != paused. expected_action→human_input."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if t["stage"] != "paused":
                raise ValueError("Инструкция доступна только на паузе")
            t["instruction"] = text
            t["expected_action"] = "human_input"
        return self._task_mutate(dialogue_id, fn)
```

- [ ] **Step 4: Прогнать тесты**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -q`
Expected: PASS (весь файл). Затем весь бэкенд-пакет — ожидаются падения в `test_agent.py`/`test_api.py` (они ещё под старой схемой) — это нормально, чинятся в Task 3/4; зафиксировать список падающих тестов.

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/memory.py studio/backend/tests/test_memory.py
git commit -m "feat(day13b-task): новая схема состояния задачи (task_id, unified stage, plan/work_steps, expected_action, context_snapshot)"
```

### Task 3a: Оркестратор — промпты, парсер плана, блок состояния, стриминг (agent.py)

**Files:**
- Modify: `studio/backend/agent.py` — константы задачи (L123-170), `build_task_state_block` (L583-601), новый `_task_llm_stream`, гард чата в `ask_stream` (L324-331)
- Test: `studio/backend/tests/test_agent.py` — task-раздел ПЕРЕПИСАТЬ (старые task-тесты дня 13 удалить)

- [ ] **Step 1: Failing-тесты** — в `studio/backend/tests/test_agent.py` заменить task-раздел. Общий mock-хендлер (поддерживает и stream, и non-stream; ответ — по system-промпту):

```python
def make_task_handler(steps, step_outputs, verdict="pass",
                      planning_output=None, done_output="ФИНАЛЬНЫЙ ОТВЕТ",
                      fail_stage=None, on_first_exec=None,
                      validator_verdicts=None):
    """MockTransport: stream=True — SSE-кадры, stream=False — JSON.
    calls — список {"system","user","stream"}. on_first_exec(request) —
    колбэк на ПЕРВОМ вызове Исполнителя (для тестов паузы на границе
    шага: пауза ставится «внутри» LLM-вызова). validator_verdicts —
    список вердиктов по вызовам (для ретра-сценария)."""
    calls = []
    state = {"exec": 0, "val": 0}

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        calls.append({"system": system, "user": user, "stream": body.get("stream", False)})
        if fail_stage and fail_stage in system:
            return httpx.Response(500, text="boom") if body.get("stream") \
                else httpx.Response(500, json={"error": "boom"})
        if "Планировщик" in system:
            content = planning_output if planning_output is not None \
                else json.dumps(steps, ensure_ascii=False)
        elif "Исполнитель" in system:
            state["exec"] += 1
            if on_first_exec and state["exec"] == 1:
                on_first_exec()
            step = next((s for s in steps if f"«{s}»" in system), "")
            content = step_outputs.get(step, f"Результат шага {step}")
        elif "Валидатор" in system:
            state["val"] += 1
            v = validator_verdicts[state["val"] - 1] if validator_verdicts else verdict
            content = f"Заключение {state['val']}.\n<verdict>{v}</verdict>"
        else:  # Оркестратор
            content = done_output
        if body.get("stream"):
            frame = ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]})
                     + "\n\n")
            return httpx.Response(200, text=frame + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return handler, calls
```

Тесты (класс `TestTaskRun13b`, setup: `StudioAgent` c `httpx.Client(transport=httpx.MockTransport(handler))`, новый диалог, `task_new`):

```python
def run_all(self, agent, did):
    return list(agent.task_run(did))

def event_types(self, events):
    return [e["type"] for e in events]
```

1. `test_full_cycle_events`: 2 work-шага; события ровно в порядке:
   `agent_spawned(planning) → stage_done(planning, plan=[2 шага]) → agent_spawned(execution) → step_updated(0,in_progress) → step_delta(0) → step_updated(0,completed,output) → step_updated(1,in_progress) → step_delta(1) → step_updated(1,completed,output) → stage_done(execution) → agent_spawned(validation) → stage_done(validation, verdict=pass) → agent_spawned(done) → stage_done(done) → task_done(answer)`.
   После: `store.task_get` — stage=done, все записи plan completed, work_steps completed с outputs.
2. `test_planning_prompt_no_chat_history`: в диалог до старта добавить user/assistant-сообщения ("секретное слово"); в system-промптах ВСЕХ вызовов (calls) "секретное слово" отсутствует; в каждом system есть `description` задачи.
3. `test_exec_step_prompt_has_previous_outputs`: у 2-го вызова Исполнителя в system есть: описание задачи, план, output 1-го шага ("Результат шага A"), строка "Текущий шаг: B"; в user-промпте — "Выполни шаг плана: B".
4. `test_planning_json_fallback_one_step`: `planning_output="Сначала сделать анализ, потом тексты"` (без JSON) → `work_steps == [{"name": "Выполнить запрос", ...}]`; ровно 1 вызов Исполнителя; пайплайн доходит до task_done.
5. `test_planning_limit_5_steps`: planning_output — JSON с 7 строками → 5 work_steps.
6. `test_instruction_on_pause_injected_once`: на первом вызове Исполнителя (on_first_exec) — `store.task_pause(did)`; события до `task_paused` (после step_updated(0,completed)); `store.task_set_instruction(did, "используй Kotlin")`; `store.task_resume(did)`; новый `task_run` → в system следующего вызова Исполнителя есть "Инструкция пользователя (обязательно учти): используй Kotlin"; после завершения стадии execution `task_get().instruction == ""`.
7. `test_pause_between_steps_no_next_call`: on_first_exec ставит паузу; после `task_paused` — ровно 1 вызов Исполнителя; stage=paused; context_snapshot заполнен.
8. `test_resume_no_repeats`: из п. 7 resume + task_run → первые 2 вызова Исполнителя — это шаг 1 (шаг 0 completed, не повторяется): в их system "Текущий шаг:" для B, не A; доходит до task_done.
9. `test_validation_fail_retry`: `validator_verdicts=["fail","pass"]` → после 1-го stage_done(validation, verdict=fail, retry=True) повтор execution: вызовы Исполнителя = 4 (2 шага × 2), в system повтора есть "Замечания валидатора (обязательно исправь): Заключение 1."; конец — task_done, retries=1.
10. `test_validation_fail_twice`: `validator_verdicts=["fail","fail"]` → после 2-го fail — без 3-го execution, сразу done (task_done), retries=1.
11. `test_llm_error_marks_failed`: `fail_stage="Исполнитель"` → события: … step_updated(0,in_progress) → task_failed(message содержит "Исполнитель"); stage=failed, error заполнен. Затем `store.task_resume(did)` + `task_run` (теперь fail_stage="Оркестратор" НЕ срабатывает — хендлер создаём заново с fail_stage=None, подменяя transport) → доходит до task_done.
12. `test_run_on_done_errors`: после полного цикла новый `task_run` → первое событие `error` ("Задача завершена"), LLM-вызовов нет.
13. `test_chat_guard`: active task в стадии execution (до run) — `ask_stream` → SSE `error` "Задача выполняется"; на paused/failed/done — гард НЕ срабатывает (проверка по коду условия, тест-юнит на условие опционально — основной покрывает execution).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -k TestTaskRun13b -q`
Expected: FAIL.

- [ ] **Step 3: Реализация — константы (agent.py, заменить L123-170):**

Удалить `TASK_EXPECTED` и `TASK_STAGE_PROMPTS` (старые). `TASK_AGENT_NAMES`, `MAX_TASK_RETRIES` — без изменений.

```python
# Состояние задачи (день 13b): stage-агенты + детерминированный
# оркестратор, пошаговое исполнение work-шагов. FSM, хранение, паузы —
# MemoryStore.task_* (memory.py); здесь LLM-вызовы и цикл. Stage/work-
# вызовы не пишутся в requests.json.
TASK_AGENT_NAMES = {
    "planning": "Планировщик",
    "execution": "Исполнитель",
    "validation": "Валидатор",
    "done": "Оркестратор",
}

# Планировщик: структурный JSON-план (best-effort парсинг, D3)
TASK_PLAN_PROMPT = (
    "Ты — Планировщик (стадия planning). Разбери задачу на 1–5 конкретных "
    "шагов. Ответ — СТРОГО JSON-массив строк (каждая строка — название "
    "шага, 3–8 слов), без текста и служебных меток вне массива. "
    'Пример: ["Анализ целевой аудитории", "Генерация офферов", '
    '"Сборка финального варианта"]')

# Исполнитель: один work-шаг за вызов (D2); {step} подставляется кодом
TASK_EXEC_STEP_PROMPT = (
    "Ты — Исполнитель (стадия execution). Сейчас выполняется один шаг "
    "плана: «{step}». Выполни ТОЛЬКО этот шаг с учётом результатов "
    "предыдущих шагов. Ответ — только результат шага, без отступлений "
    "и служебных меток.")

TASK_VALIDATION_PROMPT = (
    "Ты — Валидатор (стадия validation). Сверь результаты work-шагов с "
    "планом задачи: выполнено ли всё, где отклонения. Ответ — "
    "заключение, а в КОНЦЕ отдельной строкой метку: "
    "<verdict>pass</verdict> если план выполнен, "
    "<verdict>fail</verdict> если нет.")

TASK_DONE_PROMPT = (
    "Ты — Оркестратор. Из материалов стадий (план, результаты work-шагов, "
    "вердикт валидации) собери один связный финальный ответ по задаче. "
    "Ответ — только итог, без служебных меток.")

TASK_STAGE_USER = {
    "planning": "Разбери задачу на шаги.",
    "validation": "Проверь работу против плана и выдай вердикт.",
    "done": "Собери финальный ответ.",
}

MAX_TASK_RETRIES = 1  # один повтор execution после fail-вердикта
```

Парсер плана (модульная функция, рядом с `_now`):

```python
def parse_work_steps(text: str) -> list:
    """Best-effort: JSON-массив имён work-шагов из ответа Планировщика.
    Сбой/не-JSON/пусто — ["Выполнить запрос"]. Лимит: до 5 шагов."""
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            arr = json.loads(m.group(0))
        except ValueError:
            arr = None
        if isinstance(arr, list):
            names = [s.strip() for s in arr if isinstance(s, str) and s.strip()]
            if names:
                return names[:5]
    return ["Выполнить запрос"]
```

- [ ] **Step 4: `build_task_state_block` (заменить L583-601):**

```python
    def build_task_state_block(self, t: dict, stage: str,
                               step: str | None = None,
                               instruction: str = "",
                               feedback: str = "") -> str:
        """Блок состояния задачи для system-промпта stage-агента. Строго из
        состояния задачи (D5) — история чата в task-промпты не уходит."""
        lines = ["", "", "Состояние задачи:", f"Задача: {t['description']}"]
        plan = next((e for e in t["plan"] if e["agent"] == "planning"), None)
        if plan and plan.get("output"):
            lines.append(f"План: {plan['output']}")
        if t["work_steps"]:
            lines.append("Work-шаги:")
            for ws in t["work_steps"]:
                if ws["status"] == "completed" and ws.get("output"):
                    lines.append(f"- [выполнен] {ws['name']}: {ws['output']}")
                elif ws["status"] == "in_progress":
                    lines.append(f"- [выполняется] {ws['name']}")
                else:
                    lines.append(f"- [ожидает] {ws['name']}")
        if step:
            lines.append(f"Текущий шаг: {step}")
        if feedback:
            lines.append(f"Замечания валидатора (обязательно исправь): {feedback}")
        if instruction:
            lines.append(f"Инструкция пользователя (обязательно учти): {instruction}")
        return "\n".join(lines)
```

- [ ] **Step 5: `_task_llm_stream` (новый метод, рядом с `_task_llm_call`):**

```python
    def _task_llm_stream(self, cfg: dict, system: str, user: str):
        """Streaming LLM-вызов work-шага Исполнителя (D6; не входит в
        requests.json). Yield-ит дельты content; бросает исключение при
        ошибке (RuntimeError на HTTP != 200)."""
        body = {
            "model": cfg["model"],
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        with self._client.stream(
                "POST", self.base_url + "/chat/completions", json=body,
                headers={"Authorization": "Bearer " + self._key_for(cfg["model"])},
                timeout=120) as resp:
            if resp.status_code != 200:
                raise RuntimeError(f"Модель вернула ошибку HTTP {resp.status_code}")
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                choices = chunk.get("choices") or [{}]
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
```

- [ ] **Step 6: Гард чата в `ask_stream` (L324-331)** — заменить условие:

```python
        t = self.store.task_get(dialogue_id)
        if t["active"] and t["stage"] not in ("done", "paused", "failed"):
            yield {"type": "error",
                   "message": ("Задача выполняется: поставьте паузу "
                               "(кнопка в карточке задачи) или завершите "
                               "задачу")}
            return
```

- [ ] **Step 7: Прогон**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q`
Expected: новые task-тесты PASS; падающие test_api.py — фикс в Task 4.

### Task 3b: Новый `task_run` (agent.py)

**Files:** Modify: `studio/backend/agent.py` — заменить `task_run` (L635-708)

- [ ] **Step 1: Заменить `task_run` на:**

```python
    def task_run(self, dialogue_id: str):
        """Оркестратор пайплайна задачи (день 13b): синхронный генератор
        событий.

        События: {"type": "agent_spawned", "stage", "agent"},
        {"type": "step_updated", "index", "name", "status", "output"?},
        {"type": "step_delta", "index", "text"},
        {"type": "stage_done", "stage", "output", "verdict"?, "plan"?,
         "retry"?},
        {"type": "task_paused", "stage"}, {"type": "task_resumed", "stage"},
        {"type": "task_done", "answer"}, {"type": "task_failed", "message"},
        {"type": "error", "message"}. Исключение наружу не бросается.
        Пауза/сбой проверяются на границе стадии и на границе work-шага
        (текущий LLM-вызов доигрывается).
        """
        t = self.store.task_get(dialogue_id)
        if not t["active"]:
            yield {"type": "error", "message": "Задача не активна"}
            return
        cfg = self.get_config()
        tid = t["task_id"]
        while True:
            t = self.store.task_get(dialogue_id)
            if t["stage"] == "paused":
                yield {"type": "task_paused", "stage": "paused"}
                return
            if t["stage"] == "failed":
                # «Повтор» после сбоя: resume к первой невыполненной стадии
                self.store.task_resume(dialogue_id)
                yield {"type": "task_resumed",
                       "stage": self.store.task_get(dialogue_id)["stage"]}
                continue
            stage = t["stage"]
            if stage == "done":
                yield {"type": "error", "message": "Задача завершена"}
                return
            instruction = self.store.task_instruction_take(dialogue_id)
            if stage == "planning":
                yield {"type": "agent_spawned", "stage": "planning",
                       "agent": TASK_AGENT_NAMES["planning"]}
                self.store.task_spawn_stage(dialogue_id, "planning")
                try:
                    output = self._task_llm_call(
                        cfg,
                        TASK_PLAN_PROMPT
                        + self.build_task_state_block(t, "planning",
                                                      instruction=instruction),
                        TASK_STAGE_USER["planning"])
                except Exception as e:
                    self.store.task_set_failed(dialogue_id,
                                               f"Планировщик: {e}")
                    yield {"type": "task_failed",
                           "message": f"Ошибка Планировщика: {e}"}
                    return
                steps = parse_work_steps(output)
                self.store.task_work_steps_set(dialogue_id, steps)
                self.store.task_stage_done(dialogue_id, "planning", output)
                self.store.append_message(dialogue_id, "assistant", output,
                                          model=cfg["model"], task_id=tid,
                                          task_stage="planning")
                yield {"type": "stage_done", "stage": "planning",
                       "output": output, "plan": steps}
            elif stage == "execution":
                if t["plan"][1]["status"] != "in_progress":
                    yield {"type": "agent_spawned", "stage": "execution",
                           "agent": TASK_AGENT_NAMES["execution"]}
                    self.store.task_spawn_stage(dialogue_id, "execution")
                feedback = ""
                if t["retries"] > 0:
                    val = next((e for e in t["plan"]
                                if e["agent"] == "validation"), None)
                    if val and val.get("output"):
                        feedback = val["output"]
                for i in range(len(t["work_steps"])):
                    t = self.store.task_get(dialogue_id)
                    if t["stage"] == "paused":
                        yield {"type": "task_paused", "stage": "paused"}
                        return
                    ws = t["work_steps"][i]
                    if ws["status"] == "completed":
                        continue
                    self.store.task_work_step_set(dialogue_id, i,
                                                  "in_progress")
                    yield {"type": "step_updated", "index": i,
                           "name": ws["name"], "status": "in_progress"}
                    try:
                        parts = []
                        for delta in self._task_llm_stream(
                                cfg,
                                TASK_EXEC_STEP_PROMPT.format(step=ws["name"])
                                + self.build_task_state_block(
                                    t, "execution", step=ws["name"],
                                    instruction=instruction,
                                    feedback=feedback),
                                "Выполни шаг плана: " + ws["name"]):
                            parts.append(delta)
                            yield {"type": "step_delta", "index": i,
                                   "text": delta}
                        output = "".join(parts).strip()
                        if not output:
                            raise RuntimeError("Пустой ответ модели")
                    except Exception as e:
                        self.store.task_set_failed(
                            dialogue_id,
                            f"Исполнитель (шаг «{ws['name']}»): {e}")
                        yield {"type": "task_failed",
                               "message": f"Ошибка шага «{ws['name']}»: {e}"}
                        return
                    self.store.task_work_step_set(dialogue_id, i,
                                                  "completed", output)
                    self.store.append_message(
                        dialogue_id, "assistant", output, model=cfg["model"],
                        task_id=tid, task_stage="execution",
                        task_step=ws["name"])
                    yield {"type": "step_updated", "index": i,
                           "name": ws["name"], "status": "completed",
                           "output": output}
                t = self.store.task_get(dialogue_id)
                exec_output = "\n\n".join(
                    f"## {ws['name']}\n{ws['output']}"
                    for ws in t["work_steps"])
                self.store.task_stage_done(dialogue_id, "execution",
                                           exec_output)
                yield {"type": "stage_done", "stage": "execution",
                       "output": exec_output}
            elif stage == "validation":
                yield {"type": "agent_spawned", "stage": "validation",
                       "agent": TASK_AGENT_NAMES["validation"]}
                self.store.task_spawn_stage(dialogue_id, "validation")
                try:
                    output = self._task_llm_call(
                        cfg,
                        TASK_VALIDATION_PROMPT
                        + self.build_task_state_block(t, "validation",
                                                      instruction=instruction),
                        TASK_STAGE_USER["validation"])
                except Exception as e:
                    self.store.task_set_failed(dialogue_id,
                                               f"Валидатор: {e}")
                    yield {"type": "task_failed",
                           "message": f"Ошибка Валидатора: {e}"}
                    return
                verdict = self._parse_verdict(output)
                if verdict == "fail" and t["retries"] < MAX_TASK_RETRIES:
                    self.store.task_retry_execution(dialogue_id, output,
                                                    verdict)
                    self.store.append_message(
                        dialogue_id, "assistant", output,
                        model=cfg["model"], task_id=tid,
                        task_stage="validation")
                    yield {"type": "stage_done", "stage": "validation",
                           "output": output, "verdict": verdict,
                           "retry": True}
                    continue  # следующая итерация — execution с фидбэком
                self.store.task_stage_done(dialogue_id, "validation",
                                           output, verdict)
                self.store.append_message(
                    dialogue_id, "assistant", output, model=cfg["model"],
                    task_id=tid, task_stage="validation")
                yield {"type": "stage_done", "stage": "validation",
                       "output": output, "verdict": verdict}
            else:  # stage == "done"
                yield {"type": "agent_spawned", "stage": "done",
                       "agent": TASK_AGENT_NAMES["done"]}
                self.store.task_spawn_stage(dialogue_id, "done")
                try:
                    answer = self._task_llm_call(
                        cfg,
                        TASK_DONE_PROMPT
                        + self.build_task_state_block(t, "done",
                                                      instruction=instruction),
                        TASK_STAGE_USER["done"])
                except Exception as e:
                    self.store.task_set_failed(dialogue_id,
                                               f"Оркестратор: {e}")
                    yield {"type": "task_failed",
                           "message": f"Ошибка финального синтеза: {e}"}
                    return
                # Финальный синтез — обычное assistant-сообщение (якорь —
                # task_id без task_stage; в ленте — bubble под карточкой)
                self.store.append_message(dialogue_id, "assistant", answer,
                                          model=cfg["model"], task_id=tid)
                self.store.task_stage_done(dialogue_id, "done", answer)
                yield {"type": "stage_done", "stage": "done",
                       "output": answer}
                yield {"type": "task_done", "answer": answer}
                return
```

- [ ] **Step 2: Прогнать весь бэкенд-пакет**

Run: `cd studio/backend; python -m pytest -q`
Expected: тесты memory + agent PASS. `test_api.py` — task-тесты ещё под старой схемой: падает блок task (остальной API зелёный). Перечислить падающие тесты.

- [ ] **Step 3: Коммит**

```powershell
git add studio/backend/agent.py studio/backend/tests/test_agent.py
git commit -m "feat(day13b-task): оркестратор — пошаговое исполнение, JSON-план, step-события, paused/failed как стадии"
```

### Task 4: API (main.py) — новая семантика start/run/pause/resume

**Files:**
- Modify: `studio/backend/main.py` — `task_start` (L99-115)
- Test: `studio/backend/tests/test_api.py` — task-раздел обновить

**Изменения (D9):**
1. `start` создаёт задачу И сохраняет user-сообщение-запрос с маркером `task_id` (якорь карточки).
2. `start` при незавершённой задаче (включая paused/failed) — 400; при done/failed — новая задача (200, новый `task_id`).
3. `run` — 400 «Задача не активна» без задачи; 400 «Задача завершена» на done; на failed — 200 (повтор; сам `task_run` делает resume).
4. `pause` — 400 через ValueError store (нет активной / stage done/failed).
5. `resume` — 400 через ValueError store (stage не paused/failed).
6. Всё остальное (`instruction`, `reset`, `GET /api/task`, `task` в выдаче dialogues) — без изменений кода, только форма ответа изменилась (новая схема).

- [ ] **Step 1: Обновить task-раздел `tests/test_api.py`** (паттерн дня 13: FastAPI `TestClient` + MockTransport-агент). Тесты:

1. `test_start_returns_task_with_id_and_user_message`: POST `/api/task/start` {dialogue_id, description} → 200, `task.task_id` startswith "t_", `task.stage == "planning"`, 4 записи `plan`; `GET /api/dialogues/{id}` — первое сообщение `{role: user, content: description, task_id: <id>}`.
2. `test_start_rejects_active`: после start (незавершённая) повторный start → 400 «уже активна».
3. `test_start_after_done_is_new`: довести задачу до done (mock-LLM), затем start → 200, `task_id` другой; в диалоге — 2 user-сообщения с разными `task_id`.
4. `test_start_empty_description`: 400; `test_start_unknown_dialogue`: 404.
5. `test_run_without_task`: 400 «Задача не активна».
6. `test_run_on_done`: после полного цикла run → 400 «Задача завершена».
7. `test_run_full_sse`: start → run: SSE-кадры в порядке (agent_spawned planning → stage_done planning → agent_spawned execution → step_updated/step_delta → … → task_done); после — `GET /api/task` → stage done.
8. `test_pause_resume`: start → run (до task_paused, пауза ставится on_first_exec) → `GET /api/task` stage paused → instruction 200 → resume 200 → run → task_done.
9. `test_instruction_rejected_outside_pause`: без паузы → 400 «только на паузе».
10. `test_chat_guard_during_run`: активная незавершённая задача → POST `/api/chat` → SSE-кадр `error` «Задача выполняется…»; user-сообщение в диалог НЕ добавлено. На paused — гард не срабатывает.
11. `test_task_in_dialogues_output`: `GET /api/dialogues` — у диалога поле `task` с новой схемой (`task_id`, `plan`, `work_steps`).
12. `test_run_on_failed_retries`: mock 500 на первом run → `task_failed`; `GET /api/task` stage failed; run повторно (теперь mock healthy) → SSE: `task_resumed` → … → task_done.

- [ ] **Step 2: Run to verify fail**

Run: `cd studio/backend; python -m pytest tests/test_api.py -q`
Expected: падает task-блок (старые тесты + новые фейлят).

- [ ] **Step 3: Реализация в `main.py`** — `task_start` (L99-115):

```python
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
```

`task_run` (L117-136) — без изменений (условия `active`/`stage == "done"` остаются; failed теперь легален — store/task_run обрабатывают). `task_pause`/`task_resume`/`task_instruction`/`task_reset`/`task_get` — без изменений (ValueError → 400 работает через новую схему).

- [ ] **Step 4: Прогон**

Run: `cd studio/backend; python -m pytest -q`
Expected: ВЕСЬ бэкенд-пакет PASS.

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/main.py studio/backend/tests/test_api.py
git commit -m "feat(day13b-task): API — новая семантика start/run (task_id, user-маркер, done/failed → новая задача, failed → повтор)"
```

### Task 5: Фронтенд — api.ts + state.tsx (типы, схема, тумблер, новые события)

**Files:**
- Modify: `studio/frontend/src/api.ts` — task-секция (L81-184)
- Modify: `studio/frontend/src/state.tsx` — Message, ContextTab, StudioState, initialState, StudioAction, reducer, StudioApi, runTask, новые колбэки
- Test: `studio/frontend/tests/state.test.ts` — обновить под новые action'ы/схему; удалить тесты `task-current-stage`

**Interfaces (produces):**
- `TaskStage = 'planning'|'execution'|'validation'|'done'|'paused'|'failed'`
- `TaskPlanEntry`, `TaskWorkStep`, `TaskState` (новая схема), `TaskEvent` (9 событий)
- state: `chatMode: 'chat'|'task'` (+ `CHAT_MODE_KEY`, localStorage), `taskLive: {index:number; text:string} | null`; action'ы `chat-mode`, `task-step`, `task-step-delta`; удалён `task-current-stage`/`taskCurrentStage`
- api: `setChatMode(mode)`, `sendTaskMessage(text)`; `runTask` — обработчики новых событий + `reloadDialogue` в finally

- [ ] **Step 1: `api.ts` — заменить task-секцию (L81-184) на:**

```ts
// ── Состояние задачи (день 13b): unified FSM per-диалог ────────────────────
// POST /api/task/start {dialogue_id, description} → {task} (+ user-маркер)
// POST /api/task/run {dialogue_id} → SSE: agent_spawned/step_updated/
//   step_delta/stage_done/task_paused/task_resumed/task_done/task_failed/
//   error
// POST /api/task/pause|resume|reset {dialogue_id} → {task}
// POST /api/task/instruction {dialogue_id, text} → {task} (только на паузе)
// GET  /api/task?dialogue_id= → {task}

export type TaskStage =
  | 'planning' | 'execution' | 'validation' | 'done' | 'paused' | 'failed'
export type TaskPlanStatus = 'pending' | 'in_progress' | 'completed'
export type TaskExpectedAction = 'agent_response' | 'resume_wait' | 'human_input'

export interface TaskPlanEntry {
  step: number
  agent: 'planning' | 'execution' | 'validation' | 'done'
  status: TaskPlanStatus
  output: string | null
  verdict: 'pass' | 'fail' | null
  spawn_ts: string | null
  ts: string | null
}

export interface TaskWorkStep {
  name: string
  status: TaskPlanStatus
  output: string | null
  ts: string | null
}

export interface TaskState {
  active: boolean
  task_id: string | null
  stage: TaskStage | null
  current_step: number
  total_steps: number
  expected_action: TaskExpectedAction | null
  plan: TaskPlanEntry[]
  work_steps: TaskWorkStep[]
  context_snapshot: { description: string; work_steps: TaskWorkStep[]; instruction: string } | null
  description: string
  instruction: string
  retries: number
  error: string | null
  updated: string | null
}

export type TaskEvent =
  | { type: 'agent_spawned'; stage: TaskStage; agent: string }
  | { type: 'step_updated'; index: number; name: string; status: TaskPlanStatus; output?: string }
  | { type: 'step_delta'; index: number; text: string }
  | { type: 'stage_done'; stage: TaskStage; output: string; verdict?: 'pass' | 'fail'; plan?: string[]; retry?: boolean }
  | { type: 'task_paused'; stage: string }
  | { type: 'task_resumed'; stage: TaskStage }
  | { type: 'task_done'; answer: string }
  | { type: 'task_failed'; message: string }
  | { type: 'error'; message: string }
```

Функции `apiPostTaskStart/Pause/Resume/Instruction/Reset`, `apiGetTask`, `taskStream` — БЕЗ ИЗМЕНЕНИЙ (парсер кадров общий; типы событий заменены).

- [ ] **Step 2: `state.tsx` — точечные изменения:**

2.1 `Message` (L35-42) — добавить поля:

```ts
   // Маркеры задачи (день 13b): id задачи + work-шаг (обычные — без полей)
   task_id?: string
   task_step?: string
```

2.2 `ContextTab` (L120) — убрать `'task'`:

```ts
export type ContextTab = 'memory' | 'tokens' | 'request' | 'profile'
```

2.3 `StudioState` (L122-145) — заменить `taskCurrentStage` на `chatMode` + `taskLive`:

```ts
   // Пайплайн задачи выполняется прямо сейчас (SSE-стрим открыт)
   taskRunning: boolean
   // Живой вывод текущего work-шага (step_delta; сбрасывается при смене шага)
   taskLive: { index: number; text: string } | null
   // Режим ввода (день 13b, D8): глобальный, persist в localStorage
   chatMode: 'chat' | 'task'
```

2.4 Ключ + initialState (L147-178):

```ts
// Ключ localStorage для режима ввода (день 13b, D8)
export const CHAT_MODE_KEY = 'studio.chatMode'
```

В `initialState()` — рядом с чтением showRequests:

```ts
  let mode: 'chat' | 'task' = 'chat'
  try {
    const v = localStorage.getItem(CHAT_MODE_KEY)
    if (v === 'chat' || v === 'task') mode = v
  } catch {
    // нет localStorage — держим default
  }
```

И в возвращаемом объекте: `taskLive: null,` (вместо `taskCurrentStage: null`) и `chatMode: mode,`.

2.5 `StudioAction` (L234-270) — убрать `'task-current-stage'`, добавить:

```ts
  | { type: 'chat-mode'; mode: 'chat' | 'task' }
  | { type: 'task-step'; index: number; name: string; status: TaskPlanStatus; output?: string }
  | { type: 'task-step-delta'; index: number; text: string }
```

(импорт `type TaskPlanStatus` из `./api`).

2.6 `reducer` (L372-379 зона) — удалить case `'task-current-stage'`; case `'task-running'` без `taskCurrentStage`:

```ts
    case 'task-running':
      return { ...state, taskRunning: action.on }
    case 'chat-mode':
      return { ...state, chatMode: action.mode }
    case 'task-step': {
      const id = state.activeId
      if (id == null) return state
      const task = state.tasks[id]
      if (!task) return state
      const work_steps = task.work_steps.map((ws, j) =>
        j === action.index
          ? { ...ws, name: action.name, status: action.status,
              output: action.output !== undefined ? action.output : ws.output }
          : ws,
      )
      return {
        ...state,
        tasks: { ...state.tasks, [id]: { ...task, work_steps } },
        taskLive: action.status === 'in_progress'
          ? { index: action.index, text: '' }
          : action.status === 'completed'
            ? null
            : state.taskLive,
      }
    }
    case 'task-step-delta':
      return {
        ...state,
        taskLive: {
          index: action.index,
          text: (state.taskLive && state.taskLive.index === action.index
            ? state.taskLive.text
            : '') + action.text,
        },
      }
```

2.7 `StudioApi` (L383-411) — добавить:

```ts
   // Режим ввода (день 13b): 'chat' | 'task', persist в localStorage
   chatMode: 'chat' | 'task'
   setChatMode: (mode: 'chat' | 'task') => void
   // Режим «задача»: сообщение = инструкция на паузе / запуск задачи
   sendTaskMessage: (text: string) => Promise<void>
```

2.8 `runTask` (L473-519) — заменить обработчик событий:

```ts
      await taskStream(id, (e: TaskEvent) => {
        if (e.type === 'agent_spawned') {
          // план-запись in_progress + spawn_ts — авторитетно из бэкенда
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'step_updated') {
          dispatch({
            type: 'task-step',
            index: e.index, name: e.name, status: e.status, output: e.output,
          })
        } else if (e.type === 'step_delta') {
          dispatch({ type: 'task-step-delta', index: e.index, text: e.text })
        } else if (e.type === 'stage_done' || e.type === 'task_paused'
            || e.type === 'task_resumed') {
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_done') {
          // Финальный ответ — НОВОЕ assistant-сообщение (bubble под карточкой)
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.answer },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else {  // task_failed | error
          dispatch({ type: 'error-message', text: `Ошибка: ${e.message}` })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        }
      })
```

В `finally` — доп. перечитывание сообщений (user-маркер + stage-сообщения с маркерами):

```ts
    } finally {
      dispatch({ type: 'task-running', on: false })
      void reloadDialogue().catch((err) => console.error('reloadDialogue:', err))
    }
```

(`reloadDialogue` объявлен ниже — перенести определение ВВЕРХ, перед `runTask`, или использовать stateRef+fetch напрямую; проще: `reloadDialogue` уже существует как useCallback ниже `sendMessage` — объявить его ДО `runTask` и включить в deps.)

2.9 Новые/обновлённые колбэки:

```ts
  // Режим ввода (день 13b, D8): persist + dispatch
  const setChatMode = useCallback((mode: 'chat' | 'task') => {
    try {
      localStorage.setItem(CHAT_MODE_KEY, mode)
    } catch {
      // нет localStorage — переключатель просто не сохранится
    }
    dispatch({ type: 'chat-mode', mode })
  }, [])

  // Режим «задача»: сообщение = инструкция (paused) / запуск (иначе).
  // На failed ввод заблокирован UI — повтор кнопкой «Повтор» в карточке.
  const sendTaskMessage = useCallback(async (text: string) => {
    const id = stateRef.current.activeId
    const trimmed = text.trim()
    if (id == null || !trimmed || stateRef.current.taskRunning) return
    const task = stateRef.current.tasks[id]
    if (task && task.active) {
      if (task.stage === 'paused') {
        await sendTaskInstruction(trimmed)
        return
      }
      if (task.stage === 'failed' || task.stage === 'done') return
    }
    await startTask(trimmed)
    await reloadDialogue()
    await runTask()
  }, [startTask, sendTaskInstruction, reloadTask, reloadDialogue])
```

`startTask`, `pauseTask`, `resumeTask`, `sendTaskInstruction`, `resetTask` — без изменений. В `api`-объект (L755-779) добавить `chatMode: state.chatMode, setChatMode, sendTaskMessage`.

- [ ] **Step 3: Обновить `tests/state.test.ts`**:
- Тесты `task-current-stage` — удалить; `task-running` без сброса стадии.
- Новые: `chat-mode` (set/initial default 'chat'), `task-step` (in_progress → taskLive reset; completed → null + work_steps обновлён; чужой индекс не трогает), `task-step-delta` (аппенд по индексу, чужой индекс → новый бокс).

- [ ] **Step 4: Прогон**

Run: `cd studio/frontend; npm test; npx tsc --noEmit`
Expected: state-тесты PASS; tsc — ошибки в `ChatPanel.tsx`/`ContextPanel.tsx` (используют `STAGE_LABELS` из TaskTab, `task.paused`, `taskCurrentStage`) — ожидаемо, чинится в Task 7. Тесты `task-tab.test.tsx`/`chat-panel` с падениями — фикс в Task 7.

- [ ] **Step 5: Коммит**

```powershell
git add studio/frontend/src/api.ts studio/frontend/src/state.tsx studio/frontend/tests/state.test.ts
git commit -m "feat(day13b-task): frontend api+state — новая схема, step-события, тумблер режимов"
```

### Task 6: TaskCard — карточка процесса в чате (новый компонент)

**Files:**
- Create: `studio/frontend/src/components/TaskCard.tsx`
- Test: `studio/frontend/tests/task-card.test.tsx`

**Interfaces (produces):**
- `STAGE_LABELS: Record<string, string>` (6 стадий; переезжает из TaskTab)
- `taskFromMarkers(messages, taskId): TaskState` — восстановление истории задачи из маркеров
- `TaskCard({ task, live, running, liveStep }): JSX` — `liveStep: {index:number;text:string}|null`
- Иконки-агенты: inline SVG 14px (шестерня/молния/лупа/круг-галка)

- [ ] **Step 1: TDD — сначала тесты** (`tests/task-card.test.tsx`, render через `StudioProvider`-обёртку как в chat-panel.test.tsx):

1. Заголовок: описание задачи видно; статус-бейдж по стадии: done → «Готово», paused → «Пауза», failed → «Ошибка», pipeline-стадия + running → «Выполняется».
2. Прогресс: `Этап N/4: <label>` по `current_step`; прогресс-бар с шириной по числу completed записей плана.
3. Секции: 4 `<details>` по `plan[]` (иконка + label + бейдж статуса pending/выполняется/готово); planning-секция содержит output; execution-секция — чек-лист work_steps (✓/⏳/○ по статусу); validation — output + вердикт-чип; done — output.
4. Авто-развёртывание: live-карточка с in_progress-записью planning → `details[0]` open; все completed → все closed.
5. Кнопки: running + pipeline-стадия → «Пауза» (onClick → `apiPostTaskPause` fetch stub); paused → «Продолжить»; failed → «Повтор»; done → кнопок нет.
6. Live-бокс: `liveStep` задан → бокс с текстом в execution-секции; `liveStep` нет → бокса нет.
7. `task_from_markers`: сообщения [user(task_id), assistant(task_stage=planning), assistant(task_stage=execution, task_step=A), assistant(task_stage=validation), assistant(без task_stage)] → TaskState: stage done, 4 записи plan completed, work_steps=[A], description из user-сообщения.
8. Бэкворд-рендер: маркеры без `task_id`/старой формы (только `task_stage`) не ломают карточку (пустая/деградированная, без краша).

- [ ] **Step 2: Реализация `TaskCard.tsx`:**

```tsx
// Карточка процесса задачи (день 13b): stage-агенты в потоке чата.
// Шапка (описание, статус, действия), прогресс, секции plan[]
// (details/summary, время спавна), чек-лист work-шагов + живой бокс.
// История задачи (record перезаписан новой) — из маркеров сообщений.
import { useEffect, useState } from 'react'
import { useStudio, type Message } from '../state'
import type { TaskPlanEntry, TaskPlanStatus, TaskState, TaskWorkStep } from '../api'

// Подписи стадий (6 — unified FSM; переезд из TaskTab)
export const STAGE_LABELS: Record<string, string> = {
  planning: 'Планирование',
  execution: 'Исполнение',
  validation: 'Валидация',
  done: 'Завершение',
  paused: 'Пауза',
  failed: 'Ошибка',
}

const PIPELINE: TaskPlanEntry['agent'][] = ['planning', 'execution', 'validation', 'done']

function statusChip(status: TaskPlanStatus): { cls: string; label: string } {
  if (status === 'completed') return { cls: 'tc-chip ok', label: 'готово' }
  if (status === 'in_progress') return { cls: 'tc-chip run', label: 'выполняется' }
  return { cls: 'tc-chip', label: 'ожидает' }
}

// Relative-время «спавн N с назад»; тик 1 c только когда живая
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

function spawnAge(spawnTs: string | null, now: number): string | null {
  if (!spawnTs) return null
  const t = new Date(spawnTs.replace(' ', 'T')).getTime()
  if (Number.isNaN(t)) return null
  const s = Math.max(0, Math.round((now - t) / 1000))
  return s < 90 ? `спавн ${s} с назад` : `спавн ${Math.round(s / 60)} мин назад`
}

// Иконки-агенты (inline SVG 14px, паттерн ProfileTab)
function AgentIcon({ agent }: { agent: string }) {
  const common = { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const }
  if (agent === 'planning')
    return <svg {...common}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
  if (agent === 'execution')
    return <svg {...common}><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z" /></svg>
  if (agent === 'validation')
    return <svg {...common}><circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" /></svg>
  return <svg {...common}><circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" /></svg>
}

// Восстановить состояние задачи из маркеров сообщений (история: record
// перезаписан новой задачей). done-вывод = assistant-сообщение с task_id
// без task_stage (финальный синтез).
export function taskFromMarkers(messages: Message[], taskId: string): TaskState {
  const msgs = messages.filter((m) => m.task_id === taskId)
  const stageOut = (s: string): string =>
    msgs.find((m) => m.task_stage === s && m.role === 'assistant')?.content ?? ''
  const planOut = stageOut
  const doneOut = msgs.find((m) => m.role === 'assistant' && !m.task_stage)?.content ?? ''
  const stepMsgs = msgs.filter((m) => m.task_stage === 'execution' && m.task_step)
  const work_steps: TaskWorkStep[] = stepMsgs.map((m) => ({
    name: m.task_step as string,
    status: 'completed',
    output: m.content,
    ts: null,
  }))
  const execOut = work_steps.map((w) => `## ${w.name}\n${w.output}`).join('\n\n')
  const hasDone = doneOut !== ''
  const plan: TaskPlanEntry[] = PIPELINE.map((agent, i) => ({
    step: i + 1,
    agent,
    status: (agent === 'planning' ? planOut('planning') : agent === 'execution' ? execOut : agent === 'validation' ? planOut('validation') : doneOut) ? 'completed' : 'pending',
    output: agent === 'planning' ? planOut('planning') || null
      : agent === 'execution' ? execOut || null
      : agent === 'validation' ? planOut('validation') || null
      : doneOut || null,
    verdict: agent === 'validation'
      ? /<verdict>\s*fail\s*<\/verdict>/i.test(planOut('validation')) ? 'fail' : 'pass'
      : null,
    spawn_ts: null,
    ts: null,
  }))
  return {
    active: true,
    task_id: taskId,
    stage: hasDone ? 'done' : plan[0].status === 'completed' ? 'execution' : 'planning',
    current_step: hasDone ? 4 : plan.findIndex((p) => p.status !== 'completed') + 1 || 1,
    total_steps: 4,
    expected_action: null,
    plan,
    work_steps,
    context_snapshot: null,
    description: msgs.find((m) => m.role === 'user')?.content ?? '',
    instruction: '',
    retries: 0,
    error: null,
    updated: null,
  }
}

export interface TaskCardProps {
  task: TaskState
  live: boolean // true — живая задача (record активного диалога)
}

export default function TaskCard({ task, live }: TaskCardProps) {
  const { state, pauseTask, resumeTask } = useStudio()
  const running = state.taskRunning && live
  const now = useNow(live)
  const liveStep = live ? state.taskLive : null

  const completedCount = task.plan.filter((e) => e.status === 'completed').length
  const stageLabel = task.stage && STAGE_LABELS[task.stage]
    ? STAGE_LABELS[task.stage]
    : STAGE_LABELS[PIPELINE[task.current_step - 1] ?? 'planning']
  const badge = !task.active
    ? { cls: '', label: '' }
    : task.stage === 'done' ? { cls: 'ok', label: 'Готово' }
    : task.stage === 'paused' ? { cls: 'warn', label: 'Пауза' }
    : task.stage === 'failed' ? { cls: 'err', label: 'Ошибка' }
    : running ? { cls: 'run', label: 'Выполняется' }
    : { cls: '', label: 'Готово к запуску' }

  // Авто-развёртывание: live — активная запись; failed — упавшая; иная — всё свёрнуто
  const activeEntry = live
    ? task.plan.find((e) => e.status === 'in_progress')
    : null
  const failedEntry = task.stage === 'failed'
    ? task.plan.find((e) => e.status !== 'completed')
    : null

  const stepIcon = (st: TaskPlanStatus) =>
    st === 'completed' ? '✓' : st === 'in_progress' ? '⏳' : '○'

  return (
    <div className="task-card">
      <div className="task-card-head">
        <span className="task-card-title" title={task.description}>
          Задача: {task.description}
        </span>
        {badge.label && <span className={`tc-badge ${badge.cls}`}>{badge.label}</span>}
        {live && task.stage && PIPELINE.includes(task.stage) && running && (
          <button type="button" className="btn danger tc-btn" onClick={() => void pauseTask()}>
            Пауза
          </button>
        )}
        {live && task.stage === 'paused' && (
          <button type="button" className="btn tc-btn" onClick={() => void resumeTask()}>
            Продолжить
          </button>
        )}
        {live && task.stage === 'failed' && (
          <button type="button" className="btn tc-btn" onClick={() => void resumeTask()}>
            Повтор
          </button>
        )}
      </div>

      <div className="task-card-progress" aria-label="Прогресс задачи">
        <div className="task-card-bar">
          <div className="task-card-bar-fill"
               style={{ width: `${(completedCount / task.total_steps) * 100}%` }} />
        </div>
        <span className="task-card-step">
          Этап {task.current_step}/{task.total_steps}: {stageLabel}
        </span>
      </div>

      {task.error && task.stage === 'failed' && (
        <div className="task-card-error">{task.error}</div>
      )}

      {task.plan.map((entry) => {
        const chip = statusChip(entry.status)
        const age = spawnAge(entry.spawn_ts, now)
        const open = entry === activeEntry || entry === failedEntry
        return (
          <details key={entry.agent + entry.status} className="task-agent" open={open}>
            <summary>
              <AgentIcon agent={entry.agent} />
              <span className="task-agent-name">{STAGE_LABELS[entry.agent]}</span>
              <span className={chip.cls}>{chip.label}</span>
              {age && <span className="task-agent-age">{age}</span>}
              {entry.verdict && (
                <span className={`task-verdict ${entry.verdict}`}>
                  {entry.verdict === 'pass' ? 'прошла' : 'не прошла'}
                </span>
              )}
            </summary>
            <div className="task-agent-body">
              {entry.agent === 'execution' ? (
                <ul className="task-checklist">
                  {task.work_steps.map((ws, i) => (
                    <li key={i} className={`task-check ${ws.status}`}>
                      <span className="task-check-icon" aria-hidden>{stepIcon(ws.status)}</span>
                      <span className="task-check-name">{ws.name}</span>
                      {ws.output && <pre className="task-check-output">{ws.output}</pre>}
                    </li>
                  ))}
                  {liveStep && entry.status === 'in_progress' && (
                    <div className="task-live">
                      <span className="task-live-prompt" aria-hidden>»</span>
                      {liveStep.text}
                      <span className="caret" aria-hidden />
                    </div>
                  )}
                </ul>
              ) : (
                entry.output && <pre className="task-agent-output">{entry.output}</pre>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}
```

Примечание: `<details open={...} key={agent+status}>` — смена status перемонтирует секцию (авто-сворачивание при завершении); интерактивный клик пользователя работает (uncontrolled после mount).

- [ ] **Step 3: Прогон**

Run: `cd studio/frontend; npm test -- task-card; npx tsc --noEmit`
Expected: task-card-тесты PASS; tsc — остальные ошибки (ChatPanel/ContextPanel) ещё в Task 7.

- [ ] **Step 4: Коммит**

```powershell
git add studio/frontend/src/components/TaskCard.tsx studio/frontend/tests/task-card.test.tsx
git commit -m "feat(day13b-task): TaskCard — карточка процесса (спавн, чек-лист, живой вывод, восстановление из маркеров)"
```

### Task 7: ChatPanel — рендер карточки, тумблер режимов; удаление TaskTab/вкладки

**Files:**
- Modify: `studio/frontend/src/components/ChatPanel.tsx`
- Modify: `studio/frontend/src/components/ContextPanel.tsx` (убрать 5-ю вкладку)
- Delete: `studio/frontend/src/components/TaskTab.tsx`, `studio/frontend/tests/task-tab.test.tsx`
- Modify: `studio/frontend/src/styles.css` (стили карточки/тумблера; удалить мёртвые классы `.chat-task-strip`, `.task-chip`, `.task-tab*` — но `.task-verdict`/`.task-output` если используются карточкой — оставить/переименовать)
- Test: `studio/frontend/tests/chat-panel.test.tsx` — обновить

- [ ] **Step 1: Обновить `tests/chat-panel.test.tsx`** (failing):

1. Тумблер: два кнопки «Чат»/«Задача» (aria-label), активная подсвечена; клик → `chatMode` меняется + localStorage; при активной непаузанной задаче (`taskRunning`) — кнопки disabled.
2. Карточка в ленте: сообщения [user(task_id=t_1), assistant(task_stage=planning, task_id=t_1)] → в ленте: user-bubble + `TaskCard` (заголовок «Задача: …»); stage-сообщение НЕ рендерится как bubble.
3. Две задачи (2 task_id) — 2 карточки (chain).
4. Плейсхолдеры: chatMode=chat + нет задачи → «Сообщение…»; taskMode + нет задачи → «Опишите задачу… (Enter — запустить)»; taskMode + paused → «Инструкция для агентов…»; taskMode + failed → disabled; taskMode + running → disabled «Задача выполняется…».
5. Send в taskMode без задачи → `apiPostTaskStart` + `taskStream` (fetch stub) — не `chatStream`; в paused → `apiPostTaskInstruction`.
6. Старые тесты на кнопку «Стоп» в шапке и на статус-строку стадий — УДАЛИТЬ (кнопки теперь в карточке).

- [ ] **Step 2: `ChatPanel.tsx` — переписать:**

```tsx
// Центральная панель: шапка (название + бейдж профиля + дропдаун модели),
// лента сообщений (включая карточки процесса задачи, день 13b),
// тумблер режимов чат/задача и инпут-капсула.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useStudio, type Message } from '../state'
import TaskCard from './TaskCard'
import SaveMessageModal from './SaveMessageModal'

export default function ChatPanel() {
  const {
    state, activeProfile, sendMessage, setModel, setContextTab,
    activeTask, chatMode, setChatMode, sendTaskMessage,
  } = useStudio()
  const [draft, setDraft] = useState('')
  const [saveMsg, setSaveMsg] = useState<Message | null>(null)
  const feedRef = useRef<HTMLDivElement>(null)
  const active = state.dialogues.find((d) => d.id === state.activeId)

  // День 13b: живая задача активного диалога
  const task = activeTask && activeTask.active ? activeTask : null
  const taskRunning = state.taskRunning
  const taskBusy = task != null && taskRunning
    && task.stage != null
    && !['done', 'paused', 'failed'].includes(task.stage)

  // Карточки: каждое первое сообщение с данным task_id — якорь карточки
  const cardTaskIds = useMemo(() => {
    const seen = new Set<string>()
    const out: { taskId: string; index: number }[] = []
    state.messages.forEach((m, i) => {
      if (m.task_id && !seen.has(m.task_id)) {
        seen.add(m.task_id)
        out.push({ taskId: m.task_id, index: i })
      }
    })
    return out
  }, [state.messages])
  const cardAt = useMemo(() => {
    const map = new Map<number, string>()
    for (const c of cardTaskIds) map.set(c.index, c.taskId)
    return map
  }, [cardTaskIds])

  const currentModel = state.config?.model ?? ''
  const currentInList = state.models.some((m) => m.id === currentModel)
  const modelOptions = currentInList
    ? state.models
    : currentModel
      ? [{ id: currentModel, context_limit: 0 }, ...state.models]
      : state.models

  useEffect(() => {
    const el = feedRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.messages, state.streaming, state.taskLive])

  const canSend = !state.streaming && state.activeId != null
    && draft.trim().length > 0 && !taskBusy
  const toggleLocked = taskRunning

  const submit = () => {
    if (!canSend) return
    if (chatMode === 'task') void sendTaskMessage(draft)
    else void sendMessage(draft)
    setDraft('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const placeholder =
    state.activeId == null
      ? 'Сначала создайте диалог (слева)'
      : taskBusy
        ? 'Задача выполняется…'
        : chatMode === 'chat'
          ? 'Сообщение… (Enter — отправить, Shift+Enter — перенос)'
          : task?.stage === 'paused'
            ? 'Инструкция для агентов… (Enter — сохранить)'
            : task?.stage === 'failed'
              ? 'Задача упала — «Повтор» в карточке'
              : 'Опишите задачу… (Enter — запустить пайплайн)'

  return (
    <main className="panel chat">
      <header className="chat-head">
        <h1 className="chat-title">{active ? active.title : 'Нет активного диалога'}</h1>
        <div className="chat-head-actions">
          {activeProfile && activeProfile.status !== 'active' && (
            <button
              type="button"
              className={`profile-chip profile-badge ${
                activeProfile.status === 'declined' ? 'declined' : 'pending'
              }`}
              title="Открыть вкладку «Профили»"
              onClick={() => setContextTab('profile')}
            >
              {activeProfile.status === 'declined' ? 'Профиль отключён' : 'Профиль не заполнен'}
            </button>
          )}
          <select
            className="model-select"
            title="Модель LLM"
            value={currentModel}
            disabled={state.streaming || modelOptions.length === 0}
            onChange={(e) => void setModel(e.target.value)}
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.id}</option>
            ))}
          </select>
        </div>
      </header>

      <div className="chat-feed" ref={feedRef}>
        {state.messages.length === 0 && <p className="chat-empty">Отправьте первое сообщение…</p>}
        {state.messages.map((m, i) => {
          const isTail = i === state.messages.length - 1
          // Stage/work-сообщения с task_stage рендерятся внутри карточки
          if (m.task_stage) return null
          return (
            <div key={i}>
              <div className={m.role === 'user' ? 'msg user' : 'msg assistant'}>
                <div className="msg-role">
                  {m.role === 'user' ? 'вы' : 'модель'}
                  <button
                    type="button"
                    className="btn-icon msg-save"
                    title="Сохранить в память"
                    onClick={() => setSaveMsg(m)}
                  >
                    в память
                  </button>
                </div>
                <div className="msg-text">
                  {m.content}
                  {state.streaming && isTail && m.role === 'assistant' && (
                    <span className="caret" aria-hidden />
                  )}
                </div>
                {m.role === 'assistant' && m.model && (
                  <span className="msg-model-chip" title="Модель, которой выполнен запрос">
                    {m.model}
                  </span>
                )}
              </div>
              {cardAt.has(i) && (
                <TaskCard task={taskCardData(cardAt.get(i) as string)} live={false} />
              )}
            </div>
          )
        })}
        {task && !cardAt.has(state.messages.findIndex((m) => m.task_id === task.task_id)) && (
          // Живая задача, якорь ещё не в ленте (start в процессе) — карточка хвостом
          <TaskCard task={task} live />
        )}
      </div>

      <div className="chat-input">
        <div className="mode-toggle" role="tablist" aria-label="Режим ввода">
          <button
            type="button"
            role="tab"
            aria-selected={chatMode === 'chat'}
            className={chatMode === 'chat' ? 'mode-btn active' : 'mode-btn'}
            disabled={toggleLocked}
            onClick={() => setChatMode('chat')}
          >
            Чат
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={chatMode === 'task'}
            className={chatMode === 'task' ? 'mode-btn active' : 'mode-btn'}
            disabled={toggleLocked}
            onClick={() => setChatMode('task')}
          >
            Задача
          </button>
        </div>
        <textarea
          className="input-capsule"
          rows={2}
          value={draft}
          placeholder={placeholder}
          disabled={state.streaming || state.activeId == null || taskBusy
            || (chatMode === 'task' && task?.stage === 'failed')}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="button" className="btn send" onClick={submit} disabled={!canSend}>
          {chatMode === 'chat' ? 'Отправить' : task?.stage === 'paused' ? 'Сохранить' : 'Запустить'}
        </button>
      </div>

      {saveMsg && (
        <SaveMessageModal message={saveMsg.content} onClose={() => setSaveMsg(null)} />
      )}
    </main>
  )
}

// Данные карточки: live — record активного диалога (если task_id совпадает),
// иначе восстановление из маркеров (история задачи)
function taskCardData(taskId: string) {
  // закрывающий хелпер: использует useStudio через замыкание НЕЛЬЗЯ — данные
  // передаются извне (см. реализацию ниже)
}
```

ВАЖНО (реализация хелпера): `taskCardData` — НЕ функция в модуле без доступа к state. Реализовать INSIDE компонента:

```tsx
  const cardData = (taskId: string) => {
    if (task && task.task_id === taskId) return { t: task, live: true }
    return { t: taskFromMarkers(state.messages, taskId), live: false }
  }
```

и в рендере: `cardAt.has(i) && (() => { const { t, live } = cardData(cardAt.get(i) as string); return <TaskCard task={t} live={live} /> })()`; хвостовая живая карточка: `{task && !cardAt.has(...) && <TaskCard task={task} live />}`. Импортировать `taskFromMarkers` из `./TaskCard`.

- [ ] **Step 3: `ContextPanel.tsx`** — убрать `import TaskTab`, удалить `{ id: 'task', label: 'Задача' }` из TABS и `{tab === 'task' && <TaskTab />}`.

- [ ] **Step 4: Удалить** `studio/frontend/src/components/TaskTab.tsx` и `studio/frontend/tests/task-tab.test.tsx`.

- [ ] **Step 5: `styles.css`** — удалить мёртвые классы: `.chat-task-strip`, `.task-chip`, `.task-tab`, `.task-label`, `.task-desc`, `.task-desc-text`, `.task-strip`, `.task-actions`, `.task-error`, `.task-instruction*`, `.task-input`, `.task-output*`, `.msg-task-chip`. Оставить `.task-verdict` (карточка). Добавить (на существующих токенах: переменные фона/границ/текста, шрифты как у `.msg`):

```css
/* ── Карточка процесса задачи (день 13b) ─────────────────────────── */
.task-card { margin: 8px 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.task-card-head { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: var(--panel2); }
.task-card-title { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
.tc-badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--line); }
.tc-badge.ok { color: #2e7d32; border-color: #2e7d32; }
.tc-badge.warn { color: #b26a00; border-color: #b26a00; }
.tc-badge.err { color: #c62828; border-color: #c62828; }
.tc-badge.run { color: #1565c0; border-color: #1565c0; }
.tc-btn { margin-left: auto; padding: 2px 10px; font-size: 12px; }
.task-card-progress { display: flex; align-items: center; gap: 10px; padding: 6px 12px; border-top: 1px solid var(--line); }
.task-card-bar { flex: 1; height: 6px; border-radius: 3px; background: var(--panel2); overflow: hidden; }
.task-card-bar-fill { height: 100%; background: #1565c0; transition: width .3s; }
.task-card-step { font-size: 12px; opacity: .8; white-space: nowrap; }
.task-card-error { margin: 8px 12px; padding: 8px 10px; border: 1px solid #c62828; color: #c62828; border-radius: 8px; font-size: 13px; }
.task-agent { border-top: 1px solid var(--line); }
.task-agent summary { display: flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; list-style: none; }
.task-agent summary::-webkit-details-marker { display: none; }
.task-agent-name { font-weight: 500; }
.tc-chip { font-size: 11px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--line); opacity: .7; }
.tc-chip.run { color: #1565c0; border-color: #1565c0; opacity: 1; }
.tc-chip.ok { color: #2e7d32; border-color: #2e7d32; opacity: 1; }
.task-agent-age { font-size: 11px; opacity: .6; margin-left: auto; }
.task-agent-body { padding: 4px 12px 10px; }
.task-agent-output { margin: 0; font-size: 13px; white-space: pre-wrap; word-break: break-word; opacity: .85; }
.task-checklist { list-style: none; margin: 0; padding: 0; }
.task-check { display: flex; flex-direction: column; padding: 3px 0; font-size: 13px; }
.task-check-icon { display: inline-block; width: 18px; }
.task-check.completed .task-check-name { opacity: .8; }
.task-check.pending { opacity: .5; }
.task-check-output { margin: 2px 0 4px 18px; font-size: 12px; white-space: pre-wrap; word-break: break-word; opacity: .75; }
.task-live { margin: 4px 0 0 18px; font-size: 13px; border-left: 2px solid #1565c0; padding-left: 8px; white-space: pre-wrap; }
.task-live-prompt { opacity: .5; }

/* ── Тумблер режимов чат/задача (день 13b) ───────────────────────── */
.mode-toggle { display: flex; gap: 4px; margin-bottom: 6px; }
.mode-btn { flex: 1; padding: 4px 10px; font-size: 12px; border: 1px solid var(--line); background: transparent; border-radius: 8px; cursor: pointer; color: inherit; }
.mode-btn.active { background: var(--panel2); font-weight: 600; border-color: #1565c0; }
.mode-btn:disabled { opacity: .5; cursor: not-allowed; }
```

(Имена CSS-переменных — по текущим в styles.css: если `--line`/`--panel2` не существуют, взять фактические токены из файла; не изобретать новые.)

- [ ] **Step 6: Прогон**

Run: `cd studio/frontend; npm test; npx tsc --noEmit`
Expected: ВЕСЬ фронтенд PASS (state + task-card + chat-panel + остальные), tsc clean.

- [ ] **Step 7: Коммит**

```powershell
git add -A studio/frontend
git commit -m "feat(day13b-task): UI — карточка процесса в чате, тумблер режимов, удаление вкладки «Задача»"
```

### Task 8: E2E — блок «Задача» (scripts/e2e_studio.py)

**Files:**
- Modify: `scripts/e2e_studio.py` — task-блок (день 13, ~L568-641) переписать

**Подход (как в e2e дня 13):** API-слой детерминированный (prod-сервер :8100 + реальный GPustack), live-часть best-effort (сеть/модель — SKIP, не FAIL).

- [ ] **Step 1: Детерминированное ядро (PASS/FAIL):**

1. `POST /api/dialogues` → новый диалог.
2. `POST /api/task/start` {dialogue_id, description: "Составь план запуска тестовой кампании"} → 200; `task.task_id` startswith "t_"; `task.stage == "planning"`; `len(task["plan"]) == 4`.
3. `GET /api/dialogues/{id}` → первое сообщение `{role: user, task_id: <t>}`.
4. `POST /api/task/run` (SSE, читать кадры до конца; реальный LLM): последовательность типов событий содержит `agent_spawned(planning)`, `stage_done(planning)` c `plan` (1–5 имён), `agent_spawned(execution)`, ≥1 `step_updated(in_progress)`, ≥1 `step_delta`, ≥1 `step_updated(completed)`, `stage_done(execution)`, `agent_spawned(validation)`, `stage_done(validation)`, `agent_spawned(done)`, `task_done`.
5. `GET /api/task?dialogue_id=` → `stage == "done"`, все plan-записи completed, work_steps completed с outputs.
6. `GET /api/dialogues/{id}` → в messages: assistant-сообщения с `task_stage` (planning/execution+task_step/validation) + финальное assistant БЕЗ task_stage c тем же `task_id`; user-сообщение с `task_id`.
7. Новая задача после done: `POST /api/task/start` → 200, другой `task_id`; старая карточка-история не мешает (2 user-якоря в диалоге).
8. `POST /api/task/pause` на завершённой → 400; `instruction` вне паузы → 400; `run` на done → 400 «Задача завершена».
9. Гард чата: активная незавершённая задача (start, не run) → `POST /api/chat` → SSE `error` «Задача выполняется»; сообщение не сохранено. `POST /api/task/reset` → `active == false`.

- [ ] **Step 2: Live best-effort (SKIP при недоступности GPustack/таймауте):**

1. Пауза на границе work-шага: start → run; по первому `step_updated(completed)` (агенты живые — пауза между шагами) параллельно `POST /api/task/pause`; ожидание `task_paused` (таймаут 180 c); `GET /api/task` → stage paused, `context_snapshot` непуст. `POST /api/task/instruction` {text: "Сделай акцент на метриках"} → 200; `POST /api/task/resume` → 200; `run` → … → `task_done` (таймаут 600 c). Ответ task_done — непустая строка.
2. Чат-режим: сообщение в чат (mode не влияет на API) — ответ `done` (обычный чат, не задача): после `POST /api/chat` — `done`, в диалоге NO новых `task_id`-маркеров.

- [ ] **Step 3: Синтаксис + прогон**

```powershell
python -m py_compile scripts/e2e_studio.py
python scripts/e2e_studio.py
```
Expected: exit 0 (PASS/SKIP), task-блок: ядро — все PASS; live — PASS/SKIP.

- [ ] **Step 4: Коммит**

```powershell
git add scripts/e2e_studio.py
git commit -m "test(day13b-task): e2e-блок «Задача» (новые события, пошаговый пайплайн, маркеры, новая семантика)"
```

### Task 9: Документация

**Files:**
- Modify: `README.md` (корень) — секция «День 13»
- Modify: `studio/backend/README.md` — блок «Задача (день 13)» + API-таблица

- [ ] **Step 1: `README.md` — заменить секцию «## День 13: Состояние задачи (Task State Machine)»** на «День 13b» содержимым (RU, тот же стиль секций):

- Что это: задача = запрос пользователя в режиме «задача» (тумблер чат/задача у поля ввода, глобальный, localStorage); пайплайн planning → execution (N work-шагов) → validation → done; unified FSM (`planning|execution|validation|done|paused|failed`).
- Пошаговое исполнение: Планировщик — JSON-план (1–5 шагов, best-effort, фолбэк 1 шаг «Выполнить запрос»); Исполнитель — по LLM-вызову на work-шаг (streaming, `step_delta`); пауза на границе work-шага; ретрай execution после fail-вердикта (макс 1) с фидбэком валидатора.
- Карточка процесса в чате: спавн stage-агентов (иконка, «спавн N с назад»), чек-лист work-шагов [✓]/[⏳]/[○], живой бокс вывода, сворачивание секций (DONE — всё свёрнуто, FAILED — секция ошибки развёрнута); кнопки Пауза/Продолжить/Повтор в карточке; история задачи — из маркеров сообщений (`task_id`/`task_stage`/`task_step`).
- Контекст: промпты только из состояния задачи (description/plan/work_steps/instruction), история чата не уходит в task-промпты.
- API-таблица (обновлённая): start (200 + task_id + user-маркер; 400 незавершённая/пустое; новая задача после done/failed), run (SSE: `agent_spawned`/`step_updated`/`step_delta`/`stage_done`/`task_paused`/`task_resumed`/`task_done`/`task_failed`/`error`; 400 нет задачи/done; failed — повтор), pause (400 нет активной/done/failed), resume (400 не paused/failed), instruction (400 вне паузы), reset, GET /api/task.
- Статус: новые baseline-счёты (заполнить после Task 10), ветка.

- [ ] **Step 2: `studio/backend/README.md`**:
- API-таблица: обновить строки `/api/task/*` (семантика D9, новые SSE-события).
- Блок «## Задача (день 13)» → «день 13b»: unified FSM, пошаговое исполнение (N+3 LLM-вызовов, до 2N+3 с ретраем), SSE-протокол с полным списком событий, хранение (новая схема поля `task`: task_id, plan[], work_steps[], expected_action, context_snapshot; нет task_id = неактивна), маркеры сообщений, семантика start/run/pause/resume, пауза на границе work-шага.

- [ ] **Step 3: Коммит**

```powershell
git add README.md studio/backend/README.md
git commit -m "docs(day13b-task): README — режимы/карточка/новая схема/события"
```

### Task 10: Финальная верификация

- [ ] **Step 1: Полный прогон (всё зелёное)**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
cd studio/frontend; npx tsc --noEmit
python -m py_compile scripts/e2e_studio.py
python scripts/e2e_studio.py
```
Expected: backend PASS (записать точное число), frontend PASS (число), tsc clean, e2e exit 0 (PASS/SKIP).

- [ ] **Step 2: Rebuild + перезапуск uvicorn (ручная проверка)**

```powershell
cd studio/frontend; npm run build
# убить старый uvicorn, запустить fresh (порт 8000), логи в temp
```
Ручная проверка: режим «задача» — сообщение запускает пайплайн; карточка живая (спавн, чек-лист, живой вывод); Пауза → instruction → Продолжить; «Повтор» после сбоя; режим «чат» — обычный ответ; вкладка «Задача» отсутствует; перезагрузка страницы — карточка восстанавливается.

- [ ] **Step 3: Зафиксировать baseline** — точные счёты тестов (backend/frontend/e2e) в секцию «Статус» README (если не заполнены в Task 9).

- [ ] **Step 4: Финальный коммит (если есть незакоммиченные изменения)**

```powershell
git status; git diff --stat
git add -A; git commit -m "chore(day13b-task): baseline-счёты после финальной верификации"
```

---

## Self-Review (заполнено автором плана)

- Spec-покрытие: D1–D9 → Tasks 2/3/4 (схема/оркестратор/семантика), 5/6/7 (UI/тумблер), 8 (e2e), 9 (docs). Каждое требование delta-spec покрыто тестами (memory — Task 2, agent — Task 3a, api — Task 4, frontend — Tasks 5-7, e2e — Task 8).
- Типы/имена согласованы: `task_spawn_stage`, `task_work_step_set`, `parse_work_steps`, `_task_llm_stream`, `taskFromMarkers`, `TaskCardProps`, `TaskEvent` (9 событий) — идентичны в бэкенде/фронтенде/тестах.
- Нет placeholder'ов: каждый шаг — код или точная команда. Единственная гибкость: CSS-токены в Task 7 (привязка к фактическим переменным styles.css) и тексты e2e (не ломать ядро-ассерты).
- Риск: `details` remount по key (Task 6) — проверено тестом авто-развёртывания.
- Риск: `reloadDialogue` в finally `runTask` (Task 5) — перезаписывает messages на авторитетные из бэкенда (включая optimistic-бубль user) — согласуется с существующим паттерном activateDialogue.









