# Day 13: Task State Machine (Оркестратор + stage-агенты) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Состояние задачи per-диалог как конечный автомат (planning → execution → validation → done): пайплайн stage-агентов под управлением детерминированного оркестратора, пауза/стоп на любом этапе, instruction на паузе, статус работы в UI, продолжение без повторных объяснений.

**Architecture:** Состояние задачи — поле `task` в записи диалога (`dialogues.json`), методы `MemoryStore.task_*`. Оркестратор — `StudioAgent.task_run(dialogue_id)`: синхронный генератор (паттерн `ask_stream`), цикл «выполнить стадию (non-stream LLM-вызов со stage-промптом + блоком состояния) → сохранить output → проверить паузу → следующая стадия». Stage-агенты = тот же LLM из конфига, разные system-промпты (Планировщик/Исполнитель/Валидатор/Оркестратор-синтез). Валидация: метка `<verdict>pass|fail</verdict>` (best-effort, сбой = pass); fail → один повтор execution с фидбэком, второй fail → done с пометкой. SSE `POST /api/task/run`: события `stage` / `stage_done` / `task_paused` / `task_done` / `error`. Stage-outputs — в историю сообщений с меткой `task_stage`. Гард: `/api/chat` при активной непаузанной незавершённой задаче — error-событие, сообщение не сохраняется. UI: режим «Задача» + кнопка Стоп/Продолжить + статус-строка стадий в ChatPanel, вкладка «Задача» в ContextPanel.

**Tech Stack:** Python / FastAPI / httpx (MockTransport) / pytest; React 19 + Vite + TS / Vitest + Testing Library; e2e — `scripts/e2e_studio.py` (prod-сервер :8100).

**Spec:** `openspec/changes/day13-task-state-machine/` (proposal.md, specs/task-state-machine/spec.md, design.md, tasks.md)

## Global Constraints

- Ветка: `day13-task-state-machine`, от `day12-user-profile` (HEAD `558d6ac`). Код дней 1–12 не трогаем, кроме точечных модификаций, указанных в задачах.
- Тексты UI/API-ошибок — русский; API-ошибки — `HTTPException(400|404, "RU-текст")`.
- Протокол `POST /api/chat` (`delta`/`done`/`error`, тело `{dialogue_id, message}`), слои памяти, тумблеры, MEMORY_RULE, конфликт-гард, профиль (день 12) — не меняем.
- Stage-вызовы LLM (planning/execution/validation/synthesis) — non-stream, НЕ пишутся в `requests.json` (паттерн авто-заголовка/экстракта профиля).
- LLM не управляет FSM: все переходы, паузы, ретраи — код.
- Бэкенд-тесты офлайн (tmp_path + MockTransport); фронтенд — Vitest со stub `fetch`.
- Коммиты: `feat(day13-task): …`, `test(day13-task): …`, `docs(day13-task): …`.
- Тесты: `cd studio/backend; python -m pytest -q` / `cd studio/frontend; npm test`.

**Стадии FSM:** `planning` → `execution` → `validation` → `done`. Единственный «назад» переход: `validation` → `execution` (ретрай, максимум 1).

**Схема** (поле `task` записи диалога в `dialogues.json`):

```json
{
  "active": true,
  "stage": "planning",
  "paused": false,
  "description": "...",
  "instruction": "",
  "stages": {
    "planning":   {"output": "...", "ts": "...", "verdict": null},
    "execution":  {"output": "...", "ts": "...", "verdict": null, "attempts": 1},
    "validation": {"output": "...", "ts": "...", "verdict": "pass"}
  },
  "retries": 0,
  "error": null,
  "updated": "YYYY-MM-DD HH:MM:SS"
}
```

Отсутствие поля `task` = задача неактивна (бэкворд-совместимость старых `dialogues.json`).

**SSE-контракт `POST /api/task/run`** (кадры `data: {json}\n\n`):

```
{"type":"stage","stage":"planning","agent":"Планировщик"}
{"type":"stage_done","stage":"planning","output":"..."}
{"type":"stage_done","stage":"validation","output":"...","verdict":"fail","retry":true}
{"type":"task_paused","stage":"execution"}
{"type":"task_done","answer":"..."}
{"type":"error","message":"..."}
```

---

### Task 1: Базовая линия + коммит openspec-артефактов

**Files:** ветка `day13-task-state-machine` (уже создана).

- [ ] **Step 1: Убедиться, что мы на ветке**

```powershell
git branch --show-current
```
Expected: `day13-task-state-machine`. (Если нет — `git checkout -b day13-task-state-machine day12-user-profile`.)

- [ ] **Step 2: Baseline-тесты**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
```
Expected: все PASS (159 backend / 93 frontend по README дня 12). Pre-existing failure — зафиксировать и сообщить, не чинить.

- [ ] **Step 3: Закоммитить openspec-артефакты**

```powershell
git add openspec/changes/day13-task-state-machine
git commit -m "docs(day13-task): openspec-спецификация (proposal, spec, design, tasks)"
```

---

### Task 2: Хранилище состояния задачи в MemoryStore

**Files:**
- Modify: `studio/backend/memory.py`
- Test: `studio/backend/tests/test_memory.py`

**Interfaces:**
- Produces: `memory.TASK_STAGES = ("planning", "execution", "validation", "done")`; `memory.new_task() -> dict`; `MemoryStore.task_get(dialogue_id) -> dict` (ValueError — диалог не найден; нет поля — `new_task()`); `task_new(dialogue_id, description) -> dict` (ValueError: диалог нет / задача уже активна); `task_stage_done(dialogue_id, stage, output, verdict=None) -> dict` (ValueError: не активна / стадия != текущей / stage «done»); `task_retry_execution(dialogue_id, output, verdict) -> dict` (ValueError: не validation / retries >= 1); `task_pause(dialogue_id) -> dict`; `task_resume(dialogue_id) -> dict` (снимает paused + очищает error); `task_set_instruction(dialogue_id, text) -> dict` (ValueError: не на паузе); `task_instruction_take(dialogue_id) -> str` (возвращает и очищает); `task_set_error(dialogue_id, message) -> dict` (ставит error + paused); `task_reset(dialogue_id) -> dict` (= `new_task()`). `append_message` получает параметр `task_stage: str | None`. `get_dialogue`/`list_dialogues` получают поле `task`.

- [ ] **Step 1: Написать фейлинговые тесты**

Добавить в `studio/backend/tests/test_memory.py` (в шапку импортов: `from memory import MemoryStore, new_profile, new_task`):

```python
class TestTaskStorage:
    def setup_method(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.s = MemoryStore(self.d.name)

    def teardown_method(self):
        self.d.cleanup()

    def test_old_dialogue_task_inactive(self):
        d = self.s.new_dialogue()
        t = self.s.task_get(d["id"])
        assert t["active"] is False
        assert t["stage"] is None
        assert self.s.get_dialogue(d["id"])["task"]["active"] is False

    def test_task_new_sets_planning(self):
        d = self.s.new_dialogue()
        t = self.s.task_new(d["id"], "Сделай сайт")
        assert t["active"] is True
        assert t["stage"] == "planning"
        assert t["paused"] is False
        assert t["description"] == "Сделай сайт"
        assert t["stages"] == {}
        assert t["retries"] == 0

    def test_task_new_rejects_active(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "Задача 1")
        with pytest.raises(ValueError):
            self.s.task_new(d["id"], "Задача 2")
        assert self.s.task_get(d["id"])["description"] == "Задача 1"

    def test_task_stage_done_advances_forward(self):
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        t = self.s.task_stage_done(d["id"], "planning", "1. шаг")
        assert t["stage"] == "execution"
        assert t["stages"]["planning"]["output"] == "1. шаг"
        t = self.s.task_stage_done(d["id"], "execution", "код")
        assert t["stage"] == "validation"
        assert t["stages"]["execution"]["attempts"] == 1
        t = self.s.task_stage_done(d["id"], "validation", "ок", verdict="pass")
        assert t["stage"] == "done"
        assert t["stages"]["validation"]["verdict"] == "pass"

    def test_task_stage_done_rejects_wrong_stage(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        with pytest.raises(ValueError):
            self.s.task_stage_done(d["id"], "execution", "прыжок")
        assert self.s.task_get(d["id"])["stage"] == "planning"

    def test_task_retry_execution(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        self.s.task_stage_done(d["id"], "planning", "план")
        self.s.task_stage_done(d["id"], "execution", "работа")
        t = self.s.task_retry_execution(d["id"], "криво", "fail")
        assert t["stage"] == "execution"
        assert t["retries"] == 1
        assert t["stages"]["validation"]["verdict"] == "fail"
        with pytest.raises(ValueError):
            self.s.task_retry_execution(d["id"], "снова", "fail")

    def test_task_pause_resume(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        with pytest.raises(ValueError):
            self.s.task_resume(d["id"])  # resume без паузы — ошибка
        assert self.s.task_pause(d["id"])["paused"] is True
        assert self.s.task_resume(d["id"])["paused"] is False
        self.s.task_reset(d["id"])
        with pytest.raises(ValueError):
            self.s.task_pause(d["id"])

    def test_task_instruction_only_on_pause(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        with pytest.raises(ValueError):
            self.s.task_set_instruction(d["id"], "правка")
        self.s.task_pause(d["id"])
        assert self.s.task_set_instruction(d["id"], "правка")["instruction"] == "правка"
        assert self.s.task_instruction_take(d["id"]) == "правка"
        assert self.s.task_get(d["id"])["instruction"] == ""

    def test_task_set_error_and_resume_clears(self):
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        t = self.s.task_set_error(d["id"], "API down")
        assert t["error"] == "API down"
        assert t["paused"] is True
        assert self.s.task_resume(d["id"])["error"] is None

    def test_task_reset_clears_and_persists(self):
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        self.s.task_stage_done(d["id"], "planning", "план")
        assert self.s.task_reset(d["id"]) == new_task()
        s2 = MemoryStore(self.d.name)
        assert s2.task_get(d["id"])["active"] is False

    def test_task_in_get_and_list_dialogue(self):
        d = self.s.new_dialogue()
        self.s.task_new(d["id"], "X")
        assert self.s.get_dialogue(d["id"])["task"]["stage"] == "planning"
        assert self.s.list_dialogues()[0]["task"]["active"] is True

    def test_task_get_unknown_dialogue(self):
        import pytest
        with pytest.raises(ValueError):
            self.s.task_get("нет-такого")

    def test_append_message_task_stage(self):
        d = self.s.new_dialogue()
        self.s.append_message(d["id"], "user", "привет")
        self.s.append_message(d["id"], "assistant", "план", model="m1",
                              task_stage="planning")
        msgs = self.s.get_messages(d["id"])
        assert msgs[0].get("task_stage") is None
        assert msgs[1]["task_stage"] == "planning"
        assert msgs[1]["model"] == "m1"
```

- [ ] **Step 2: Прогнать — убедиться, что фейлят**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -q -k TestTaskStorage`
Expected: FAIL (`AttributeError: 'MemoryStore' object has no attribute 'task_get'` и пр.).

- [ ] **Step 3: Реализация в `memory.py`**

Константы — после `PROFILE_ACTIONS` (строка ~57):

```python
# Состояние задачи (день 13): FSM per-диалог, поле «task» записи диалога.
# Отсутствующее поле = неактивная задача (бэкворд-совместимость).
TASK_STAGES = ("planning", "execution", "validation", "done")


def new_task() -> dict:
    """Свежее (неактивное) состояние задачи."""
    return {"active": False, "stage": None, "paused": False,
            "description": "", "instruction": "", "stages": {},
            "retries": 0, "error": None, "updated": None}
```

`append_message` — добавить параметр `task_stage`:

```python
    def append_message(self, dialogue_id: str, role: str, content: str,
                       model: str | None = None,
                       task_stage: str | None = None) -> None:
        """Добавить сообщение в диалог; ValueError, если диалог не существует.

        model — метка модели (assistant); task_stage — стадия задачи,
        которую выполнил stage-агент (день 13).
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
            d.setdefault("messages", []).append(msg)
            self._write_dialogues(data)
```

`get_dialogue` — добавить `"task": self._task_of(d)` в возвращаемый dict (рядом с `"profile"`). `list_dialogues` — добавить `"task": self._task_of(d)` в dict списка.

Новая секция — после `profile_action`, перед блоком WM:

```python
    # ---------- состояние задачи: FSM (день 13, per-диалог) ----------

    def _task_of(self, d: dict) -> dict:
        """Состояние задачи записи диалога; отсутствие поля/битое значение —
        неактивная задача."""
        raw = d.get("task")
        if not isinstance(raw, dict):
            return new_task()
        t = new_task()
        for k in ("active", "stage", "paused", "description", "instruction",
                  "retries", "error", "updated"):
            if k in raw:
                t[k] = raw[k]
        stages = raw.get("stages")
        t["stages"] = ({s: e for s, e in stages.items() if isinstance(e, dict)}
                       if isinstance(stages, dict) else {})
        return t

    def _task_mutate(self, dialogue_id: str, fn) -> dict:
        """Применить fn(task) к состоянию задачи диалога; запись атомарная.
        fn бросает ValueError — состояние не меняется."""
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            t = self._task_of(d)
            fn(t)
            t["updated"] = _now()
            d["task"] = t
            self._write_dialogues(data)
            return t

    def task_get(self, dialogue_id: str) -> dict:
        """Состояние задачи диалога; ValueError, если диалог не существует.
        Нет поля — неактивная задача (new_task)."""
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            return self._task_of(d)

    def task_new(self, dialogue_id: str, description: str) -> dict:
        """Создать задачу (stage=planning). ValueError: диалог не найден;
        задача уже активна (сначала reset)."""
        def fn(t):
            if t["active"]:
                raise ValueError("Задача уже активна: завершите её (reset) перед новой")
            t.update({"active": True, "stage": "planning", "paused": False,
                      "description": description, "instruction": "",
                      "stages": {}, "retries": 0, "error": None})
        return self._task_mutate(dialogue_id, fn)

    def task_stage_done(self, dialogue_id: str, stage: str, output: str,
                        verdict: str | None = None) -> dict:
        """Записать output завершённой стадии и перейти к следующей.
        ValueError: задача не активна; стадия != текущей; stage «done»."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if stage not in TASK_STAGES or stage == "done":
                raise ValueError(f"Неизвестная стадия: {stage}")
            if t["stage"] != stage:
                raise ValueError(f"Стадия не совпадает: текущая «{t['stage']}»")
            entry = dict(t["stages"].get(stage) or {})
            entry.update({"output": output, "ts": _now(), "verdict": verdict})
            if stage == "execution":
                entry["attempts"] = entry.get("attempts", 0) + 1
            t["stages"][stage] = entry
            t["stage"] = TASK_STAGES[TASK_STAGES.index(stage) + 1]
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_retry_execution(self, dialogue_id: str, output: str,
                             verdict: str) -> dict:
        """Валидация не прошла: сохранить её output/вердикт и вернуться в
        execution (единственный разрешённый «назад» переход). ValueError:
        задача не активна; стадия != validation; ретраи исчерпаны."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if t["stage"] != "validation":
                raise ValueError("Ретрай доступен только на стадии validation")
            if t["retries"] >= 1:
                raise ValueError("Повтор execution уже был")
            t["stages"]["validation"] = {"output": output, "ts": _now(),
                                         "verdict": verdict}
            t["retries"] += 1
            t["stage"] = "execution"
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_pause(self, dialogue_id: str) -> dict:
        """Поставить паузу (вступает на границе стадии). ValueError:
        задача не активна."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            t["paused"] = True
        return self._task_mutate(dialogue_id, fn)

    def task_resume(self, dialogue_id: str) -> dict:
        """Снять паузу и очистить ошибку. ValueError: задача не активна;
        задача не на паузе (resume без паузы — бессмысленное действие,
        API отвечает 400)."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if not t["paused"]:
                raise ValueError("Задача не на паузе")
            t["paused"] = False
            t["error"] = None
        return self._task_mutate(dialogue_id, fn)

    def task_set_instruction(self, dialogue_id: str, text: str) -> dict:
        """Инструкция пользователя — только на паузе. ValueError: задача
        не активна; пауза не установлена."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            if not t["paused"]:
                raise ValueError("Инструкция доступна только на паузе")
            t["instruction"] = text
        return self._task_mutate(dialogue_id, fn)

    def task_instruction_take(self, dialogue_id: str) -> str:
        """Вернуть instruction и очистить (вызывается перед запуском стадии)."""
        out = {"text": ""}

        def fn(t):
            out["text"] = t["instruction"]
            t["instruction"] = ""

        self._task_mutate(dialogue_id, fn)
        return out["text"]

    def task_set_error(self, dialogue_id: str, message: str) -> dict:
        """Ошибка LLM-вызова стадии: отметить и поставить паузу (повтор —
        через resume). ValueError: задача не активна."""
        def fn(t):
            if not t["active"]:
                raise ValueError("Задача не активна")
            t["error"] = message
            t["paused"] = True
        return self._task_mutate(dialogue_id, fn)

    def task_reset(self, dialogue_id: str) -> dict:
        """Сбросить состояние задачи (новая задача готова). ValueError:
        диалог не найден."""
        def fn(t):
            t.clear()
            t.update(new_task())

        return self._task_mutate(dialogue_id, fn)
```

- [ ] **Step 4: Прогнать — все зелёные**

Run: `cd studio/backend; python -m pytest -q`
Expected: PASS (159 + 13 новых).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/memory.py studio/backend/tests/test_memory.py
git commit -m "feat(day13-task): хранилище состояния задачи per-диалог (FSM-стадии, paused, instruction, outputs, retry, reset; task в выдаче диалогов; append_message task_stage)"
```
---

### Task 3: Оркестратор пайплайна в StudioAgent

**Files:**
- Modify: `studio/backend/agent.py`
- Test: `studio/backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `store.task_get/task_new/task_stage_done/task_retry_execution/task_pause/task_resume/task_set_instruction/task_instruction_take/task_set_error/task_reset` (Task 2); `store.append_message(..., task_stage=...)`.
- Produces: константы `TASK_AGENT_NAMES`, `TASK_EXPECTED`, `TASK_STAGE_PROMPTS`, `TASK_STAGE_USER`, `MAX_TASK_RETRIES = 1`; `StudioAgent.build_task_state_block(t: dict, stage: str, instruction: str = "") -> str`; `StudioAgent._task_llm_call(cfg, system, user) -> str` (non-stream, бросает исключение при ошибке); `StudioAgent._parse_verdict(output) -> str`; `StudioAgent.task_run(dialogue_id)` — синхронный генератор событий (SSE-контракт в шапке); гард в `ask_stream`.

- [ ] **Step 1: Написать фейлинговые тесты**

Добавить в `studio/backend/tests/test_agent.py` (`json`, `httpx` уже импортированы):

```python
# ---------- задача: оркестратор stage-агентов (день 13) ----------

def make_task_script_handler(script):
    """Mock-LLM для пайплайна задачи: non-stream, ответ — по роли стадии.

    script: {"planning": str, "execution": [str, ...], "validation": [str, ...]}
    Возвращает (handler, calls); calls — список system-промптов вызовов.
    """
    state = {"execution": list(script.get("execution", [])),
             "validation": list(script.get("validation", []))}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = (payload["messages"][0] or {}).get("content", "")
        calls.append(system)
        if "Планировщик" in system:
            text = script["planning"]
        elif "Исполнитель" in system:
            text = state["execution"].pop(0)
        elif "Валидатор" in system:
            text = state["validation"].pop(0)
        else:
            text = "ИТОГОВОЕ ОТВЕЧЕНИЕ"
        return httpx.Response(200, json={"choices": [
            {"message": {"content": text}}]})

    return handler, calls


def task_ready(agent):
    """Диалог: профиль declined (день 12) — готов к обычным запросам."""
    d = agent.store.new_dialogue()
    ready(agent, d)
    return d


def test_task_run_full_cycle(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг А\n2. Шаг Б",
        "execution": ["Код готов"],
        "validation": ["Всё ок <verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "Сделать кнопку")
    events = list(agent.task_run(d["id"]))
    kinds = [(e["type"], e.get("stage")) for e in events]
    assert ("stage", "planning") in kinds
    assert ("stage_done", "planning") in kinds
    assert ("stage", "execution") in kinds
    assert ("stage", "validation") in kinds
    assert ("stage", "done") in kinds
    assert events[-1]["type"] == "task_done"
    t = agent.store.task_get(d["id"])
    assert t["active"] is True and t["stage"] == "done"
    assert t["stages"]["planning"]["output"] == "1. Шаг А\n2. Шаг Б"
    assert t["stages"]["validation"]["verdict"] == "pass"
    # инъекция state: у Исполнителя — план, у Валидатора — план и работа
    assert any("Планировщик" in s and "Сделать кнопку" in s for s in calls)
    assert any("Исполнитель" in s and "1. Шаг А" in s for s in calls)
    assert any("Валидатор" in s and "Код готов" in s for s in calls)
    # stage-сообщения в истории с меткой, финальный ответ — обычный assistant
    msgs = agent.store.get_messages(d["id"])
    stage_msgs = [m for m in msgs if m.get("task_stage")]
    assert [m["task_stage"] for m in stage_msgs] == \
        ["planning", "execution", "validation"]
    assert msgs[-1].get("task_stage") is None
    assert msgs[-1]["role"] == "assistant"


def test_task_run_retry_on_validation_fail(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг",
        "execution": ["Криво", "Правильно"],
        "validation": ["Не то <verdict>fail</verdict>",
                       "Ок <verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["retries"] == 1
    assert t["stages"]["execution"]["output"] == "Правильно"
    assert t["stages"]["execution"]["attempts"] == 2
    assert any(e["type"] == "stage_done" and e.get("retry") for e in events)
    # фидбэк: промпт повторного Исполнителя — старая работа + замечания
    executor_prompts = [s for s in calls if "Исполнитель" in s]
    assert len(executor_prompts) == 2
    assert "Криво" in executor_prompts[1]
    assert "Не то" in executor_prompts[1]


def test_task_run_second_fail_goes_done(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг",
        "execution": ["Криво", "Снова криво"],
        "validation": ["Нет <verdict>fail</verdict>",
                       "Снова нет <verdict>fail</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["retries"] == 1
    assert t["stages"]["validation"]["verdict"] == "fail"
    assert events[-1]["type"] == "task_done"


def test_task_run_missing_verdict_treated_pass(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг", "execution": ["Код"],
        "validation": ["Видимо ок"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["stages"]["validation"]["verdict"] == "pass"
    assert events[-1]["type"] == "task_done"


def test_task_run_pause_between_stages(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Работа"],
        "validation": ["<verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    # пауза на границе: после stage_done(planning) — флаг; execution не стартует
    for e in agent.task_run(d["id"]):
        if e["type"] == "stage_done" and e["stage"] == "planning":
            agent.store.task_pause(d["id"])
            break
    t = agent.store.task_get(d["id"])
    assert t["paused"] is True
    assert t["stage"] == "execution"
    assert "execution" not in t["stages"]
    # повторный run на паузе — сразу task_paused, LLM не вызывается
    before = len(calls)
    events2 = list(agent.task_run(d["id"]))
    assert events2 == [{"type": "task_paused", "stage": "execution"}]
    assert len(calls) == before
    # resume — продолжение с сохранённого состояния, planning не повторяется
    agent.store.task_resume(d["id"])
    before_planner = len([s for s in calls if "Планировщик" in s])
    events3 = list(agent.task_run(d["id"]))
    assert events3[-1]["type"] == "task_done"
    assert agent.store.task_get(d["id"])["stage"] == "done"
    after_planner = len([s for s in calls if "Планировщик" in s])
    assert after_planner == before_planner


def test_task_run_instruction_applied_once(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Работа"],
        "validation": ["<verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    agent.store.task_pause(d["id"])
    agent.store.task_set_instruction(d["id"], "Используй Kotlin")
    agent.store.task_resume(d["id"])
    list(agent.task_run(d["id"]))
    planner = next(s for s in calls if "Планировщик" in s)
    assert "Используй Kotlin" in planner
    executor = next(s for s in calls if "Исполнитель" in s)
    assert "Используй Kotlin" not in executor
    assert agent.store.task_get(d["id"])["instruction"] == ""


def test_task_run_stage_error_and_retry_stage(data_dir):
    state = {"fail_first": True}
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Р"],
        "validation": ["<verdict>pass</verdict>"],
    })

    def flaky(request: httpx.Request) -> httpx.Response:
        if state["fail_first"]:
            state["fail_first"] = False
            payload = json.loads(request.content)
            system = (payload["messages"][0] or {}).get("content", "")
            if "Планировщик" in system:
                return httpx.Response(500, json={"error": "boom"})
        return handler(request)

    agent = make_agent(data_dir, flaky)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    assert events[-1]["type"] == "error"
    t = agent.store.task_get(d["id"])
    assert t["error"] is not None
    assert t["paused"] is True
    assert t["stage"] == "planning"
    # повтор после resume — та же стадия, дальше — полный прогон
    agent.store.task_resume(d["id"])
    events2 = list(agent.task_run(d["id"]))
    assert events2[-1]["type"] == "task_done"
    assert agent.store.task_get(d["id"])["stage"] == "done"


def test_task_run_inactive_task(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    events = list(agent.task_run(d["id"]))
    assert events == [{"type": "error", "message": "Задача не активна"}]


def test_ask_stream_blocked_by_active_task(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "Задача выполняется" in events[0]["message"]
    assert agent.store.get_messages(d["id"]) == []  # не сохранено
    # на паузе чат работает
    agent.store.task_pause(d["id"])
    events2 = list(agent.ask_stream(d["id"], "привет"))
    assert events2[-1]["type"] == "done"
```

- [ ] **Step 2: Прогнать — убедиться, что фейлят**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k task_run`
Expected: FAIL (`AttributeError: 'StudioAgent' object has no attribute 'task_run'`).
- [ ] **Step 3: Реализация в `agent.py`**

В шапку — `import re` (после `import json`). Константы — после `PROFILE_DECLINE_MARKERS` (строка ~120):

```python
# Состояние задачи (день 13): stage-агенты + детерминированный оркестратор.
# FSM, хранение, паузы — MemoryStore.task_* (memory.py); здесь только
# LLM-вызовы стадий и цикл. Stage-вызовы не пишутся в requests.json.
TASK_AGENT_NAMES = {
    "planning": "Планировщик",
    "execution": "Исполнитель",
    "validation": "Валидатор",
    "done": "Оркестратор",
}

TASK_EXPECTED = {
    "planning": "составить план шагов задачи",
    "execution": "выполнить работу по плану задачи",
    "execution_retry": "исправить работу по замечаниям валидации и дать обновлённый результат",
    "validation": "сверить работу с планом и выдать вердикт",
    "done": "собрать финальный ответ из материалов всех стадий",
}

TASK_STAGE_PROMPTS = {
    "planning": (
        "Ты — Планировщик (стадия planning). По описанию задачи составь план: "
        "3–7 пронумерованных шагов, каждый — что конкретно сделать. "
        "Ответ — только план, без отступлений и служебных меток."),
    "execution": (
        "Ты — Исполнитель (стадия execution). Выполни работу по плану задачи "
        "и дай результат. Ответ — только работа, без отступлений и служебных "
        "меток."),
    "validation": (
        "Ты — Валидатор (стадия validation). Сверь результат работы с планом "
        "задачи: выполнено ли всё, где отклонения. Ответ — заключение, а в "
        "КОНЦЕ отдельной строкой метку: <verdict>pass</verdict> если план "
        "выполнен, <verdict>fail</verdict> если нет."),
    "done": (
        "Ты — Оркестратор. Из материалов стадий (план, работа, вердикт "
        "валидации) собери один связный финальный ответ по задаче. "
        "Ответ — только итог, без служебных меток."),
}

TASK_STAGE_USER = {
    "planning": "Составь план для задачи, описанной выше.",
    "execution": "Выполни работу по плану задачи.",
    "execution_retry": ("Исправь работу по замечаниям валидации и дай "
                        "обновлённый результат."),
    "validation": "Проверь работу против плана и выдай вердикт.",
    "done": "Собери финальный ответ.",
}

MAX_TASK_RETRIES = 1  # один повтор execution после fail-вердикта
```

Методы `StudioAgent` — после `_profile_init_turn` (строка ~521):

```python
    # ---------- задача: оркестратор stage-агентов (день 13) ----------

    def build_task_state_block(self, t: dict, stage: str,
                               instruction: str = "") -> str:
        """Блок состояния задачи для system-промпта stage-агента."""
        lines = ["", "", "Состояние задачи:", f"Задача: {t['description']}"]
        done = []
        for s in ("planning", "execution", "validation"):
            e = t["stages"].get(s)
            if e and e.get("output"):
                extra = f" (вердикт: {e['verdict']})" if e.get("verdict") else ""
                done.append(f"- {s}: {e['output']}{extra}")
        if done:
            lines.append("Выполнено:")
            lines.extend(done)
        key = "execution_retry" if (stage == "execution" and t["retries"] > 0) \
            else stage
        lines.append(f"Ожидаемое действие: {TASK_EXPECTED[key]}")
        if instruction:
            lines.append(f"Инструкция пользователя (обязательно учти): {instruction}")
        return "\n".join(lines)

    def _task_llm_call(self, cfg: dict, system: str, user: str) -> str:
        """Non-stream LLM-вызов stage-агента (не входит в requests.json).
        Бросает исключение при ошибке (httpx.HTTPError, ValueError,
        RuntimeError) — оркестратор превратит его в error-событие."""
        body = {
            "model": cfg["model"],
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        resp = self._client.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + self._key_for(cfg["model"])},
            json=body, timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Модель вернула ошибку HTTP {resp.status_code}")
        content = (((resp.json().get("choices") or [{}])[0]
                    .get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("Пустой ответ модели")
        return content

    @staticmethod
    def _parse_verdict(output: str) -> str:
        """Вердикт валидатора: <verdict>pass|fail</verdict>; отсутствие/сбой
        метки — pass (пайплайн не должен ломаться о метку мелких моделей)."""
        m = re.search(r"<verdict>\s*(pass|fail)\s*</verdict>", output,
                      re.IGNORECASE)
        return m.group(1).lower() if m else "pass"

    def task_run(self, dialogue_id: str):
        """Оркестратор пайплайна задачи: синхронный генератор событий.

        События: {"type": "stage", "stage", "agent"},
        {"type": "stage_done", "stage", "output", "verdict"?},
        {"type": "task_paused", "stage"}, {"type": "task_done", "answer"},
        {"type": "error", "message"}. Исключение наружу не бросается.
        Пауза/стоп проверяются на границе стадий (текущий LLM-вызов
        доигрывается).
        """
        t = self.store.task_get(dialogue_id)
        if not t["active"]:
            yield {"type": "error", "message": "Задача не активна"}
            return
        cfg = self.get_config()
        while True:
            t = self.store.task_get(dialogue_id)
            if t["paused"]:
                yield {"type": "task_paused", "stage": t["stage"]}
                return
            stage = t["stage"]
            instruction = self.store.task_instruction_take(dialogue_id)
            if stage == "done":
                yield {"type": "stage", "stage": "done",
                       "agent": TASK_AGENT_NAMES["done"]}
                try:
                    answer = self._task_llm_call(
                        cfg,
                        TASK_STAGE_PROMPTS["done"]
                        + self.build_task_state_block(t, "done", instruction),
                        TASK_STAGE_USER["done"])
                except Exception as e:
                    self.store.task_set_error(dialogue_id,
                                              f"Оркестратор: {e}")
                    yield {"type": "error",
                           "message": f"Ошибка финального синтеза: {e}"}
                    return
                self.store.append_message(dialogue_id, "assistant", answer,
                                          model=cfg["model"])
                yield {"type": "task_done", "answer": answer}
                return
            yield {"type": "stage", "stage": stage,
                   "agent": TASK_AGENT_NAMES[stage]}
            key = "execution_retry" if (stage == "execution"
                                        and t["retries"] > 0) else stage
            try:
                output = self._task_llm_call(
                    cfg,
                    TASK_STAGE_PROMPTS[stage]
                    + self.build_task_state_block(t, stage, instruction),
                    TASK_STAGE_USER[key])
            except Exception as e:
                self.store.task_set_error(dialogue_id,
                                          f"{TASK_AGENT_NAMES[stage]}: {e}")
                yield {"type": "error",
                       "message": f"Ошибка стадии {TASK_AGENT_NAMES[stage]}: {e}"}
                return
            verdict = None
            if stage == "validation":
                verdict = self._parse_verdict(output)
                if verdict == "fail" and t["retries"] < MAX_TASK_RETRIES:
                    self.store.task_retry_execution(dialogue_id, output,
                                                    verdict)
                    yield {"type": "stage_done", "stage": stage,
                           "output": output, "verdict": verdict,
                           "retry": True}
                    continue  # следующая итерация — execution с фидбэком
            self.store.task_stage_done(dialogue_id, stage, output, verdict)
            self.store.append_message(dialogue_id, "assistant", output,
                                      model=cfg["model"], task_stage=stage)
            event = {"type": "stage_done", "stage": stage, "output": output}
            if verdict:
                event["verdict"] = verdict
            yield event
```

Гард в `ask_stream` — сразу после `d = self.store.get_dialogue(dialogue_id)` (строка ~273), ПЕРЕД `append_message`:

```python
        # День 13: активная непаузанная незавершённая задача — чат
        # приостановлен (сообщение не сохраняется, LLM не вызывается).
        t = self.store.task_get(dialogue_id)
        if t["active"] and t["stage"] != "done" and not t["paused"]:
            yield {"type": "error",
                   "message": ("Задача выполняется: поставьте паузу (кнопка "
                               "в шапке чата) или завершите задачу")}
            return
```

- [ ] **Step 4: Прогнать — все зелёные**

Run: `cd studio/backend; python -m pytest -q`
Expected: PASS (включая 11 новых task_run-тестов; старые ask_stream-тесты не сломаны — гард не срабатывает без активной задачи).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/agent.py studio/backend/tests/test_agent.py
git commit -m "feat(day13-task): оркестратор пайплайна stage-агентов (FSM-цикл, SSE-события, verdict-ретрай, пауза на границе стадии, гард /api/chat)"
```

---

### Task 4: REST API /api/task/*

**Files:**
- Modify: `studio/backend/main.py`
- Test: `studio/backend/tests/test_api.py`

**Interfaces:**
- Consumes: Task 2 (store), Task 3 (`agent.task_run`).
- Produces: роуты `POST /api/task/start|run|pause|resume|instruction|reset`, `GET /api/task?dialogue_id=`. Тела: `{dialogue_id}` (+ `description` для start, `text` для instruction). 400/404 RU-detail. Ответы — `{"task": {...}}`; run — SSE.

- [ ] **Step 1: Написать фейлинговые тесты**

Добавить в `studio/backend/tests/test_api.py` (фикстуры `client`, `dialogue_id`, `parse_sse` уже есть; `parse_sse` принимает список строк `response.text.splitlines()`):

```python
# ---------- задача: FSM + stage-агенты (день 13) ----------

def task_dialogue(client):
    """Диалог с declined-профилем (день 12) и активной задачей."""
    did = client.post("/api/dialogues").json()["dialogue"]["id"]
    client.post("/api/profile/action",
                json={"dialogue_id": did, "action": "decline"})
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Сделать кнопку"})
    assert r.status_code == 200
    return did


def test_task_start_and_get(client):
    r = client.post("/api/task/start",
                    json={"dialogue_id": "nope", "description": "X"})
    assert r.status_code == 404
    r = client.post("/api/task/start",
                    json={"dialogue_id": "x", "description": "  "})
    assert r.status_code == 400
    did = client.post("/api/dialogues").json()["dialogue"]["id"]
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Сделать кнопку"})
    assert r.status_code == 200
    assert r.json()["task"]["stage"] == "planning"
    r = client.get("/api/task", params={"dialogue_id": did})
    assert r.json()["task"]["description"] == "Сделать кнопку"
    # task в выдаче диалогов
    assert client.get("/api/dialogues").json()["dialogues"][0]["task"]["active"] is True
    assert client.get(f"/api/dialogues/{did}").json()["dialogue"]["task"]["active"] is True


def test_task_start_rejects_active(client):
    did = task_dialogue(client)
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Ещё"})
    assert r.status_code == 400


def test_task_run_sse_cycle(client):
    did = task_dialogue(client)
    with client.stream("POST", "/api/task/run",
                       json={"dialogue_id": did}) as resp:
        assert resp.status_code == 200
        lines = resp.iter_lines()
        events = parse_sse(list(lines))
    kinds = [(e["type"], e.get("stage")) for e in events]
    assert ("stage", "planning") in kinds
    assert ("stage", "execution") in kinds
    assert ("stage", "validation") in kinds
    assert events[-1]["type"] == "task_done"
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "done"
    # stage-сообщения с меткой в истории диалога
    msgs = client.get(f"/api/dialogues/{did}").json()["dialogue"]["messages"]
    assert [m["task_stage"] for m in msgs if m.get("task_stage")] == \
        ["planning", "execution", "validation"]


def test_task_run_requires_active(client):
    did = client.post("/api/dialogues").json()["dialogue"]["id"]
    r = client.post("/api/task/run", json={"dialogue_id": did})
    assert r.status_code == 400


def test_task_pause_resume_instruction(client):
    did = task_dialogue(client)
    assert client.post("/api/task/resume",
                       json={"dialogue_id": did}).status_code == 400
    # instruction вне паузы — 400
    assert client.post("/api/task/instruction",
                       json={"dialogue_id": did, "text": "x"}).status_code == 400
    assert client.post("/api/task/pause",
                       json={"dialogue_id": did}).status_code == 200
    r = client.post("/api/task/instruction",
                    json={"dialogue_id": did, "text": "Используй Kotlin"})
    assert r.status_code == 200
    assert r.json()["task"]["instruction"] == "Используй Kotlin"
    assert client.post("/api/task/resume",
                       json={"dialogue_id": did}).status_code == 200
    # run на паузе снята: пайплайн доходит до done
    with client.stream("POST", "/api/task/run",
                       json={"dialogue_id": did}) as resp:
        events = parse_sse(list(resp.iter_lines()))
    assert events[-1]["type"] == "task_done"
    # instruction попала в planning-промпт (видна в body-запросах)
    # и очищена
    assert client.get("/api/task", params={"dialogue_id": did}).json()["task"]["instruction"] == ""


def test_task_reset(client):
    did = task_dialogue(client)
    assert client.post("/api/task/reset",
                       json={"dialogue_id": did}).status_code == 200
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["active"] is False
    # после reset — новая задача
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Новая"})
    assert r.status_code == 200
    assert r.json()["task"]["description"] == "Новая"


def test_chat_blocked_by_active_task(client):
    did = task_dialogue(client)
    r = client.post("/api/chat", json={"dialogue_id": did, "message": "привет"})
    assert r.status_code == 200  # SSE; внутри — error-событие
    text = r.text
    assert "Задача выполняется" in text
    # на паузе чат работает
    client.post("/api/task/pause", json={"dialogue_id": did})
    r2 = client.post("/api/chat", json={"dialogue_id": did, "message": "привет"})
    assert '"type": "done"' in r2.text
```

- [ ] **Step 2: Прогнать — убедиться, что фейлят**

Run: `cd studio/backend; python -m pytest tests/test_api.py -q -k task`
Expected: FAIL (404 «не найдено» на `/api/task/*`).

- [ ] **Step 3: Реализация в `main.py`**

Секция — после блока профиля (строка ~95), перед «конфиг»:

```python
    # ---------- задача: FSM + stage-агенты (день 13) ----------

    @app.post("/api/task/start")
    def task_start(body: dict):
        """Создать задачу: {dialogue_id, description}. 400 — пустое описание
        или уже есть активная задача; 404 — диалог."""
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
        if not agent.store.task_get(dialogue_id)["active"]:
            raise HTTPException(400, "Задача не активна")

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
```

- [ ] **Step 4: Прогнать — все зелёные**

Run: `cd studio/backend; python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/main.py studio/backend/tests/test_api.py
git commit -m "feat(day13-task): REST /api/task (start/run/pause/resume/instruction/reset/get) + task в выдаче диалогов"
```---

### Task 5: Фронтенд — api.ts + state.tsx (задача: типы, SSE, state, провайдер)

**Files:**
- Modify: `studio/frontend/src/api.ts`
- Modify: `studio/frontend/src/state.tsx`
- Test: `studio/frontend/tests/state.test.ts` (чистые функции)

**Interfaces:**
- Produces (api.ts): типы `TaskStage`, `TaskStageEntry`, `TaskState`, `TaskEvent`; `apiPostTaskStart(dialogue_id, description)`, `apiPostTaskPause(dialogue_id)`, `apiPostTaskResume(dialogue_id)`, `apiPostTaskInstruction(dialogue_id, text)`, `apiPostTaskReset(dialogue_id)`, `apiGetTask(dialogue_id)`; `taskStream(dialogueId, onEvent)` — SSE `POST /api/task/run` (тот же fetch+ReadableStream паттерн, что `chatStream`).
- Produces (state.tsx): `Message.task_stage?: string`; `DialogueMeta.task?: TaskState`; `StudioState.tasks: Record<string, TaskState>`; `StudioState.taskRunning: boolean`; `StudioState.taskCurrentStage: TaskStage | null`; `ContextTab` += `'task'`; чистые функции `tasksFrom(dialogues)`, `activeTaskOf(state)`; action `{type:'task-set'; id; task}`; StudioApi += `startTask`, `runTask`, `pauseTask`, `resumeTask`, `sendTaskInstruction`, `resetTask`.

- [ ] **Step 1: Тесты чистых функций (state.test.ts)**

Добавить в `studio/frontend/tests/state.test.ts`:

```ts
// ── задача: tasksFrom / activeTaskOf (день 13) ──

const taskA: TaskState = {
  active: true, stage: 'planning', paused: false,
  description: 'Сделать кнопку', instruction: '', stages: {},
  retries: 0, error: null, updated: null,
}

describe('tasksFrom', () => {
  it('извлекает task из диалогов, пропускает записи без поля', () => {
    const out = tasksFrom([
      { id: 'a', title: '', created: '', message_count: 0, task: taskA },
      { id: 'b', title: '', created: '', message_count: 0 },
    ])
    expect(out.a).toEqual(taskA)
    expect(out.b).toBeUndefined()
  })
})

describe('activeTaskOf', () => {
  it('возвращает task активного диалога или null', () => {
    const st = { activeId: 'a', tasks: { a: taskA } }
    expect(activeTaskOf(st)).toEqual(taskA)
    expect(activeTaskOf({ activeId: 'b', tasks: { a: taskA } })).toBeNull()
    expect(activeTaskOf({ activeId: null, tasks: {} })).toBeNull()
  })
})
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/frontend; npm test -- --run tests/state.test.ts`
Expected: FAIL (`tasksFrom is not defined`).

- [ ] **Step 3: api.ts**

Добавить секцию после блока профиля (строка ~80):

```ts
// ── Состояние задачи (день 13): FSM per-диалог ─────────────────────────────
// POST /api/task/start {dialogue_id, description} → {task}
// POST /api/task/run {dialogue_id} → SSE: stage/stage_done/task_paused/
//   task_done/error
// POST /api/task/pause|resume|reset {dialogue_id} → {task}
// POST /api/task/instruction {dialogue_id, text} → {task} (только на паузе)
// GET  /api/task?dialogue_id= → {task}

export type TaskStage = 'planning' | 'execution' | 'validation' | 'done'

export interface TaskStageEntry {
  output: string
  ts: string
  verdict: 'pass' | 'fail' | null
  attempts?: number
}

export interface TaskState {
  active: boolean
  stage: TaskStage | null
  paused: boolean
  description: string
  instruction: string
  stages: Record<string, TaskStageEntry>
  retries: number
  error: string | null
  updated: string | null
}

export type TaskEvent =
  | { type: 'stage'; stage: TaskStage; agent: string }
  | { type: 'stage_done'; stage: TaskStage; output: string; verdict?: 'pass' | 'fail'; retry?: boolean }
  | { type: 'task_paused'; stage: TaskStage }
  | { type: 'task_done'; answer: string }
  | { type: 'error'; message: string }

export function apiPostTaskStart(
  dialogue_id: string,
  description: string,
): Promise<{ task: TaskState }> {
  return apiPost('/task/start', { dialogue_id, description })
}

export function apiPostTaskPause(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/pause', { dialogue_id })
}

export function apiPostTaskResume(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/resume', { dialogue_id })
}

export function apiPostTaskInstruction(
  dialogue_id: string,
  text: string,
): Promise<{ task: TaskState }> {
  return apiPost('/task/instruction', { dialogue_id, text })
}

export function apiPostTaskReset(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiPost('/task/reset', { dialogue_id })
}

export function apiGetTask(dialogue_id: string): Promise<{ task: TaskState }> {
  return apiGet(`/task?dialogue_id=${encodeURIComponent(dialogue_id)}`)
}

// SSE POST /api/task/run — тот же паттерн, что chatStream (fetch + ReadableStream)
export async function taskStream(
  dialogueId: string,
  onEvent: (e: TaskEvent) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/task/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ dialogue_id: dialogueId }),
  })
  if (!res.ok) await fail(res)
  if (!res.body) throw new ApiError(0, 'Пустой SSE-поток (нет тела ответа)')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const line of frame.split('\n')) {
        const l = line.trim()
        if (!l.startsWith('data:')) continue
        const raw = l.slice(5).trim()
        if (!raw) continue
        try {
          onEvent(JSON.parse(raw) as TaskEvent)
        } catch {
          // битый кадр — пропускаем
        }
      }
    }
  }
}
```

- [ ] **Step 4: state.tsx**

`Message` — добавить поле (после `model?`):

```ts
  // Стадия задачи, выполненная stage-агентом (день 13; обычные сообщения — без поля)
  task_stage?: string
```

`DialogueMeta` — добавить (после `profile?`):

```ts
  // Состояние задачи (день 13): опционально — старые dialogues.json без поля
  task?: TaskState
```

`ContextTab`:

```ts
export type ContextTab = 'memory' | 'tokens' | 'request' | 'profile' | 'task'
```

Импорт: `taskStream`, `apiGetTask`, `apiPostTaskStart`, `apiPostTaskPause`, `apiPostTaskResume`, `apiPostTaskInstruction`, `apiPostTaskReset`, `type TaskEvent`, `type TaskState`, `type TaskStage` — в import из `./api`.

`StudioState` — добавить поля (после `profiles`):

```ts
  // Задачи диалогов (день 13): id диалога → состояние задачи (из GET /api/dialogues)
  tasks: Record<string, TaskState>
  // Пайплайн задачи выполняется прямо сейчас (SSE-стрим открыт)
  taskRunning: boolean
  // Стадия, которую исполняет stage-агент прямо сейчас (из события «stage»)
  taskCurrentStage: TaskStage | null
```

`initialState` — `tasks: {}, taskRunning: false, taskCurrentStage: null`.

Чистые функции (после `activeProfileOf`):

```ts
// Извлечь задачи из списка диалогов (записи без task пропускаются —
// бэкворд-совместимость со старыми dialogues.json)
export function tasksFrom(dialogues: DialogueMeta[]): Record<string, TaskState> {
  const out: Record<string, TaskState> = {}
  for (const d of dialogues) {
    if (d.task) out[d.id] = d.task
  }
  return out
}

// Задача активного диалога (derived: из tasks + activeId)
export function activeTaskOf(
  state: Pick<StudioState, 'activeId' | 'tasks'>,
): TaskState | null {
  if (state.activeId == null) return null
  return state.tasks[state.activeId] ?? null
}
```

`StudioAction` — добавить варианты:

```ts
  | { type: 'task-set'; id: string; task: TaskState }
  | { type: 'task-running'; on: boolean }
  | { type: 'task-current-stage'; stage: TaskStage | null }
```

Reducer:
- кейсы `'loaded'`, `'activated'`, `'created'` — добавить в возвращаемый state: `tasks: tasksFrom(action.dialogues)` (для `loaded`/`activated`/`dialogues-updated` — из `action.dialogues`; для `created` — `{ ...state.tasks, [action.dialogue.id]: action.dialogue.task }`, если `task` есть, иначе `state.tasks`);
- `'dialogues-refresh'` — `tasks: tasksFrom(action.dialogues)`;
- новые кейсы:

```ts
    case 'task-set':
      return { ...state, tasks: { ...state.tasks, [action.id]: action.task } }
    case 'task-running':
      return { ...state, taskRunning: action.on, taskCurrentStage: action.on ? state.taskCurrentStage : null }
    case 'task-current-stage':
      return { ...state, taskCurrentStage: action.stage }
```

`StudioApi` — добавить:

```ts
  // Задача (день 13): FSM-действия активного диалога
  startTask: (description: string) => Promise<void>
  runTask: () => Promise<void>
  pauseTask: () => Promise<void>
  resumeTask: () => Promise<void>
  sendTaskInstruction: (text: string) => Promise<void>
  resetTask: () => Promise<void>
  activeTask: TaskState | null
```

`StudioProvider` — вычисление и колбэки (рядом с `activeProfile`/`setProfile`):

```ts
  // Задача активного диалога (день 13) — вычисляется из state каждый рендер
  const activeTask = activeTaskOf(state)

  // Перечитать задачу активного диалога из API (источник правды — бэкенд)
  const reloadTask = useCallback(async (): Promise<TaskState | null> => {
    const id = stateRef.current.activeId
    if (id == null) return null
    try {
      const { task } = await apiGetTask(id)
      dispatch({ type: 'task-set', id, task })
      return task
    } catch (err) {
      console.error('reloadTask:', err)
      return null
    }
  }, [])

  // Запустить задачу: POST /api/task/start → обновить state
  const startTask = useCallback(async (description: string) => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskStart(id, description)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Пайплайн задачи: SSE-стрим POST /api/task/run.
  // stage_done — stage-сообщение в чат + перечитать задачу;
  // task_done — финальный ответ (как done чата); task_paused/error — стоп.
  const runTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null || stateRef.current.taskRunning) return
    dispatch({ type: 'task-running', on: true })
    try {
      await taskStream(id, (e: TaskEvent) => {
        if (e.type === 'stage') {
          dispatch({ type: 'task-current-stage', stage: e.stage })
        } else if (e.type === 'stage_done') {
          dispatch({ type: 'task-current-stage', stage: null })
          // stage-output — в историю как assistant-сообщение с меткой стадии
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.output, task_stage: e.stage },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_done') {
          // Финальный ответ — НОВОЕ assistant-сообщение. Не action «done»:
          // finishAssistant перезаписал бы последнее сообщение (вывод
          // стадии validation), а stage-история должна сохраниться.
          dispatch({
            type: 'messages',
            messages: [
              ...stateRef.current.messages,
              { role: 'assistant', content: e.answer },
            ],
          })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else if (e.type === 'task_paused') {
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        } else {
          dispatch({ type: 'error-message', text: `Ошибка: ${e.message}` })
          void reloadTask().catch((err) => console.error('reloadTask:', err))
        }
      })
    } catch (err) {
      dispatch({
        type: 'error-message',
        text: `Ошибка: ${err instanceof Error ? err.message : String(err)}`,
      })
    } finally {
      dispatch({ type: 'task-running', on: false })
    }
  }, [reloadTask])

  // Стоп: POST /api/task/pause (вступает на границе стадии)
  const pauseTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    try {
      const { task } = await apiPostTaskPause(id)
      dispatch({ type: 'task-set', id, task })
    } catch (err) {
      console.error('pauseTask:', err)
      await reloadTask()
    }
  }, [reloadTask])

  // Продолжить: POST /api/task/resume + повторный запуск пайплайна
  const resumeTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    await apiPostTaskResume(id)
    await reloadTask()
    await runTask()
  }, [reloadTask, runTask])

  // Инструкция на паузе: POST /api/task/instruction
  const sendTaskInstruction = useCallback(async (text: string) => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskInstruction(id, text)
    dispatch({ type: 'task-set', id, task })
  }, [])

  // Сброс: POST /api/task/reset
  const resetTask = useCallback(async () => {
    const id = stateRef.current.activeId
    if (id == null) return
    const { task } = await apiPostTaskReset(id)
    dispatch({ type: 'task-set', id, task })
  }, [])
```

Объект `api` — добавить: `activeTask`, `startTask`, `runTask`, `pauseTask`, `resumeTask`, `sendTaskInstruction`, `resetTask`.

- [ ] **Step 5: Прогнать — зелёные + типы**

```powershell
cd studio/frontend; npm test -- --run
cd studio/frontend; npx tsc --noEmit
```
Expected: PASS (93 + 2 новых), tsc без ошибок.

- [ ] **Step 6: Коммит**

```powershell
git add studio/frontend/src/api.ts studio/frontend/src/state.tsx studio/frontend/tests/state.test.ts
git commit -m "feat(day13-task): фронтенд api+state (TaskState/TaskEvent, taskStream SSE, tasks в state, startTask/runTask/pauseTask/resumeTask/instruction/resetTask)"
```---

### Task 6: Вкладка «Задача» (TaskTab) в панели «Контекст»

**Files:**
- Create: `studio/frontend/src/components/TaskTab.tsx`
- Modify: `studio/frontend/src/components/ContextPanel.tsx`
- Modify: `studio/frontend/src/styles.css`
- Test: `studio/frontend/tests/task-tab.test.tsx` (new; паттерн — `chat-panel.test.tsx`: `API_FIXTURES` + `jsonResponse` + `normalizeUrl` + `render(<StudioProvider>…</StudioProvider>)`)

**Interfaces:**
- Consumes: Task 5 (`activeTask`, `startTask`, `runTask`, `pauseTask`, `resumeTask`, `sendTaskInstruction`, `resetTask`, `state.taskRunning`, `state.taskCurrentStage`).
- Produces: компонент `TaskTab`; экспорты `STAGE_LABELS: Record<TaskStage, string>`, `STAGE_ORDER: TaskStage[]` (общий словарь для TaskTab и ChatPanel); 5-я вкладка панели `ContextTab = 'task'`.

- [ ] **Step 1: Тесты (`tests/task-tab.test.tsx`)**

```tsx
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StudioProvider } from '../src/state'
import TaskTab from '../src/components/TaskTab'

// Контракты GET /api/* для StudioProvider (loadAll при старте)
const TASK_FIXTURE = {
  active: true, stage: 'execution', paused: true,
  description: 'Сделать кнопку', instruction: '',
  stages: { planning: { output: '1. Шаг', ts: '', verdict: null } },
  retries: 0, error: null, updated: null,
}

const API_FIXTURES: Record<string, unknown> = {
  '/api/config': { model: 'qwen3.8-27b', temperature: 0.7, max_tokens: 1024, system_prompt: 'sp' },
  '/api/dialogues': {
    active_id: 'd1',
    dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: TASK_FIXTURE }],
  },
  '/api/memory': {
    active_id: 'd1',
    dialogue: { message_count: 0, tokens_est: 0 },
    working: { entries: 0, tokens_est: 0, items: {} },
    long_term: { entries: 0, tokens_est: 0, items: {} },
    toggles: { st: true, wm: true, lt: true },
  },
  '/api/tokens': { last: null, session: { prompt: 0, completion: 0, total: 0 }, context_limit: 32768 },
  '/api/requests': { requests: [] },
  '/api/models': { models: [{ id: 'qwen3.8-27b', context_limit: 32768 }] },
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}
function normalizeUrl(input: RequestInfo | URL): string {
  return String(input).replace(/^https?:\/\/[^/]+/, '')
}

beforeEach(() => { localStorage.clear() })

describe('TaskTab', () => {
  it('на паузе: описание, стадия, «Продолжить», instruction-форма', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) =>
        jsonResponse(API_FIXTURES[normalizeUrl(input)] ?? { ok: true })),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    expect(await screen.findByText('Сделать кнопку')).toBeTruthy()
    // «Планирование» встречается в статус-строке и в summary вывода стадии
    expect(screen.getAllByText('Планирование').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: 'Продолжить' })).toBeTruthy()
    expect(screen.getByLabelText(/Инструкция/)).toBeTruthy()
  })

  it('«Сохранить» — POST /api/task/instruction, поле очищается', async () => {
    const posted: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = normalizeUrl(input)
        if (init?.method === 'POST' && url === '/api/task/instruction') {
          posted.push(JSON.parse(String(init.body)))
          return jsonResponse({ task: { ...TASK_FIXTURE, instruction: 'Используй Kotlin' } })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    const input = (await screen.findByLabelText(/Инструкция/)) as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Используй Kotlin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(posted).toEqual([{ dialogue_id: 'd1', text: 'Используй Kotlin' }]))
    expect((screen.getByLabelText(/Инструкция/) as HTMLInputElement).value).toBe('')
  })

  it('задачи нет: форма описания, «Запустить задачу» при пустом — disabled', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0 }],
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(<StudioProvider><TaskTab /></StudioProvider>)
    const btn = await screen.findByRole('button', { name: 'Запустить задачу' })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })
})
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/frontend; npm test -- --run tests/task-tab.test.tsx`
Expected: FAIL (нет модуля `../src/components/TaskTab`).
- [ ] **Step 3: Компонент `TaskTab.tsx`**

```tsx
// Вкладка «Задача» (день 13) правой панели «Контекст»: состояние задачи
// (FSM per-диалог) — описание, прогресс стадий, выводы stage-агентов с
// вердиктами, Стоп/Продолжить, инструкция на паузе, сброс.
import { useState } from 'react'
import { useStudio } from '../state'
import type { TaskStage } from '../api'

// Подписи стадий FSM (RU) — общий словарь для TaskTab и ChatPanel
export const STAGE_LABELS: Record<TaskStage, string> = {
  planning: 'Планирование',
  execution: 'Исполнение',
  validation: 'Валидация',
  done: 'Завершение',
}

export const STAGE_ORDER: TaskStage[] = ['planning', 'execution', 'validation', 'done']

export default function TaskTab() {
  const {
    state, activeTask, startTask, runTask,
    pauseTask, resumeTask, sendTaskInstruction, resetTask,
  } = useStudio()
  const [desc, setDesc] = useState('')
  const [instruction, setInstruction] = useState('')

  if (state.activeId == null) {
    return <p className="tab-hint">Сначала создайте диалог (слева)</p>
  }

  // Задача неактивна (или отсутствует) — форма запуска
  if (!activeTask || !activeTask.active) {
    return (
      <div className="task-tab">
        <label className="task-label" htmlFor="task-desc">Описание задачи</label>
        <textarea
          id="task-desc"
          className="task-desc"
          rows={4}
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
          placeholder="Что сделать? (например: «Создать калькулятор на Python»)"
        />
        <button
          type="button"
          className="btn"
          disabled={!desc.trim()}
          onClick={() => {
            void (async () => {
              await startTask(desc)
              setDesc('')
              await runTask()
            })()
          }}
        >
          Запустить задачу
        </button>
      </div>
    )
  }

  const task = activeTask
  const isDone = task.stage === 'done'
  const isPaused = task.paused
  const isRunning = state.taskRunning && !isPaused

  const stageClass = (s: TaskStage): string => {
    if (task.stages[s]?.output) return 'task-chip done'
    if (state.taskCurrentStage === s || (!isDone && task.stage === s)) {
      return 'task-chip active'
    }
    return 'task-chip'
  }

  return (
    <div className="task-tab">
      <div>
        <span className="task-label">Задача</span>
        <p className="task-desc-text">{task.description}</p>
      </div>

      <div className="task-strip" aria-label="Стадии задачи">
        {STAGE_ORDER.map((s) => (
          <span key={s} className={stageClass(s)}>
            {STAGE_LABELS[s]}
          </span>
        ))}
        {isPaused && <span className="task-chip paused">на паузе</span>}
      </div>

      <div className="task-actions">
        {isRunning && (
          <button type="button" className="btn danger" onClick={() => void pauseTask()}>
            Стоп
          </button>
        )}
        {isPaused && !isDone && (
          <button type="button" className="btn" onClick={() => void resumeTask()}>
            Продолжить
          </button>
        )}
        {isDone && (
          <button type="button" className="btn" onClick={() => void resetTask()}>
            Новая задача
          </button>
        )}
      </div>

      {task.error && <p className="task-error">{task.error}</p>}

      {isPaused && (
        <div className="task-instruction">
          <label className="task-label" htmlFor="task-instr">
            Инструкция (учтётся при продолжении)
          </label>
          <div className="task-instruction-row">
            <input
              id="task-instr"
              className="task-input"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="Например: используй Kotlin"
            />
            <button
              type="button"
              className="btn"
              disabled={!instruction.trim()}
              onClick={() => {
                void (async () => {
                  await sendTaskInstruction(instruction)
                  setInstruction('')
                })()
              }}
            >
              Сохранить
            </button>
          </div>
          {task.instruction && (
            <p className="task-instruction-current">Сохранено: {task.instruction}</p>
          )}
        </div>
      )}

      {STAGE_ORDER.filter((s) => task.stages[s]).map((s) => {
        const e = task.stages[s]
        return (
          <details key={s} className="task-output">
            <summary>
              {STAGE_LABELS[s]}
              {e.attempts != null && e.attempts > 1 && (
                <span className="task-attempts"> (попыток: {e.attempts})</span>
              )}
              {e.verdict && (
                <span className={'task-verdict ' + e.verdict}>
                  {e.verdict === 'pass' ? 'прошла' : 'не прошла'}
                </span>
              )}
            </summary>
            <pre className="task-output-text">{e.output}</pre>
          </details>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: `ContextPanel.tsx` — 5-я вкладка**

- Импорт: `import TaskTab from './TaskTab'`.
- `TABS`: добавить `{ id: 'task', label: 'Задача' }`.
- Body: добавить `{tab === 'task' && <TaskTab />}`.
- Комментарий в шапке файла: «…/ Профили / Задача».
- [ ] **Step 5: Стили — в конец `styles.css`**

(Цвета/отступы — по конвенциям файла; ниже — необходимые классы.)

```css
/* ── День 13: задача (FSM) ─────────────────────────────────────────────── */
.task-tab { display: flex; flex-direction: column; gap: 10px; }
.task-label { font-size: 12px; opacity: .7; }
.task-desc, .task-input { width: 100%; box-sizing: border-box; font: inherit; }
.task-desc-text { margin: 2px 0 0; font-size: 14px; }
.task-strip { display: flex; flex-wrap: wrap; gap: 6px; }
.task-chip {
  padding: 2px 8px; border-radius: 999px; font-size: 12px;
  border: 1px solid rgba(128, 128, 128, .5);
}
.task-chip.done { border-color: #2e7d32; color: #2e7d32; }
.task-chip.active { border-color: #1e88e5; color: #1e88e5; background: rgba(30, 136, 229, .12); }
.task-chip.paused { border-color: #f9a825; color: #f9a825; }
.task-actions { display: flex; gap: 8px; }
.btn.danger { border-color: #c62828; color: #c62828; }
.task-error { color: #e53935; font-size: 13px; margin: 0; }
.task-instruction-row { display: flex; gap: 8px; }
.task-instruction-current { font-size: 12px; opacity: .7; margin: 4px 0 0; }
.task-output summary { cursor: pointer; font-size: 13px; }
.task-verdict.pass { color: #2e7d32; }
.task-verdict.fail { color: #e53935; }
.task-attempts { opacity: .7; }
.task-output-text {
  white-space: pre-wrap; font-family: inherit; font-size: 13px;
  max-height: 220px; overflow: auto; margin: 6px 0 0; padding: 6px;
  border: 1px solid rgba(128, 128, 128, .3);
}
/* Чат: статус-строка стадий и чип stage-сообщений */
.chat-task-strip { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 12px; }
.msg-task-chip {
  font-size: 11px; padding: 1px 6px; border-radius: 999px;
  border: 1px solid rgba(128, 128, 128, .5); margin-left: 6px;
}
```

- [ ] **Step 6: Прогнать — зелёные + типы**

```powershell
cd studio/frontend; npm test -- --run
cd studio/frontend; npx tsc --noEmit
```
Expected: PASS (все прежние + 3 новых), tsc без ошибок.

- [ ] **Step 7: Коммит**

```powershell
git add studio/frontend/src/components/TaskTab.tsx studio/frontend/src/components/ContextPanel.tsx studio/frontend/src/styles.css studio/frontend/tests/task-tab.test.tsx
git commit -m "feat(day13-task): вкладка «Задача» (FSM-статус, выводы стадий с вердиктами, стоп/продолжить, инструкция на паузе, сброс)"
```

---

### Task 7: Режим задачи в ChatPanel (кнопки, статус-строка, stage-сообщения)

**Files:**
- Modify: `studio/frontend/src/components/ChatPanel.tsx`
- Test: `studio/frontend/tests/chat-panel.test.tsx` (добавить describe-блок)

**Interfaces:**
- Consumes: Task 5/6 (`activeTask`, `pauseTask`, `resumeTask`, `state.taskRunning`, `state.taskCurrentStage`, `STAGE_LABELS`, `STAGE_ORDER` из `./TaskTab`).
- Produces: кнопки «Стоп»/«Продолжить» в шапке; статус-строка стадий под шапкой; у stage-сообщений — чип стадии вместо «модель»; инпут заблокирован при активной непаузанной незавершённой задаче.

- [ ] **Step 1: Тест (chat-panel.test.tsx)**

Добавить (хелперы `jsonResponse`/`normalizeUrl` уже есть в файле; общий `API_FIXTURES` не менять — в тесте переопределять `/api/dialogues`):

```tsx
describe('ChatPanel — режим задачи (день 13)', () => {
  const TASK_RUNNING = {
    active: true, stage: 'execution', paused: false,
    description: 'Сделать кнопку', instruction: '', stages: {},
    retries: 0, error: null, updated: null,
  }

  it('активная задача: статус-строка стадий, инпут заблокирован', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = normalizeUrl(input)
        if (url === '/api/dialogues') {
          return jsonResponse({
            active_id: 'd1',
            dialogues: [{ id: 'd1', title: 'Д', created: '', message_count: 0, task: TASK_RUNNING }],
          })
        }
        return jsonResponse(API_FIXTURES[url] ?? { ok: true })
      }),
    )
    render(
      <StudioProvider>
        <ChatPanel />
      </StudioProvider>,
    )
    // статус-строка: активная стадия «Исполнение»
    await screen.findByText('Исполнение')
    const ta = document.querySelector('.input-capsule') as HTMLTextAreaElement
    expect(ta.disabled).toBe(true)
    expect(ta.placeholder).toMatch(/Задача выполняется/)
  })
})
```

- [ ] **Step 2: Прогнать — фейлит**

Run: `cd studio/frontend; npm test -- --run tests/chat-panel.test.tsx`
Expected: FAIL (нет статус-строки / инпут не заблокирован).

- [ ] **Step 3: Модификация `ChatPanel.tsx`**

Импорт: `import { STAGE_LABELS, STAGE_ORDER } from './TaskTab'`.
Деструктурировать из `useStudio()` дополнительно: `activeTask, pauseTask, resumeTask`.
После `const active = ...`:

```tsx
  // День 13: активная задача — режим задачи в чате
  const task = activeTask && activeTask.active ? activeTask : null
  const taskBusy = task != null && task.stage !== 'done' && !task.paused
```

Шапка (`chat-head-actions`) — перед `<select className="model-select">`:

```tsx
          {task && task.stage !== 'done' && (
            <>
              {taskBusy && state.taskRunning && (
                <button
                  type="button"
                  className="btn danger"
                  title="Остановить пайплайн (вступит на границе стадии)"
                  onClick={() => void pauseTask()}
                >
                  Стоп
                </button>
              )}
              {task.paused && !state.taskRunning && (
                <button
                  type="button"
                  className="btn"
                  title="Продолжить пайплайн задачи"
                  onClick={() => void resumeTask()}
                >
                  Продолжить
                </button>
              )}
            </>
          )}
```

Под шапкой (между `</header>` и `<div className="chat-feed">`):

```tsx
      {task && task.stage !== 'done' && (
        <div className="chat-task-strip" aria-label="Стадии задачи">
          {STAGE_ORDER.map((s) => {
            const cls = task.stages[s]?.output
              ? 'task-chip done'
              : state.taskCurrentStage === s || task.stage === s
                ? 'task-chip active'
                : 'task-chip'
            return (
              <span key={s} className={cls}>
                {STAGE_LABELS[s]}
              </span>
            )
          })}
          {task.paused && <span className="task-chip paused">на паузе</span>}
        </div>
      )}
```

Сообщения (в `msg-role`): stage-метка вместо «модель»:

```tsx
              <div className="msg-role">
                {m.role === 'user' ? 'вы' : m.task_stage ? STAGE_LABELS[m.task_stage as TaskStage] : 'модель'}
                {m.task_stage && (
                  <span className="msg-task-chip" title="Сделано stage-агентом задачи">
                    задача
                  </span>
                )}
```

Импорт типа: `import type { TaskStage } from '../api'`.

Инпут: placeholder и disabled:

```tsx
          placeholder={
            state.activeId == null
              ? 'Сначала создайте диалог (слева)'
              : taskBusy
                ? 'Задача выполняется — чат на паузе (кнопка «Стоп» в шапке)'
                : 'Сообщение… (Enter — отправить, Shift+Enter — перенос)'
          }
          disabled={state.streaming || state.activeId == null || taskBusy}
```

`canSend` — добавить `&& !taskBusy`.

- [ ] **Step 4: Прогнать — зелёные + типы**

```powershell
cd studio/frontend; npm test -- --run
cd studio/frontend; npx tsc --noEmit
```
Expected: PASS, tsc чистый.

- [ ] **Step 5: Коммит**

```powershell
git add studio/frontend/src/components/ChatPanel.tsx studio/frontend/tests/chat-panel.test.tsx
git commit -m "feat(day13-task): чат — режим задачи (стоп/продолжить, статус-строка стадий, stage-сообщения, блокировка инпута)"
```
---

### Task 8: E2E — smoke пайплайна задачи

**Files:**
- Modify: `scripts/e2e_studio.py`

**Interfaces:**
- Consumes: хелперы скрипта `http(method, path, body, timeout)`, `record(step, status, detail)`, `log`, `probe_gpustack`, SSE-разбор (как в чат-блоке); prod-сервер на :8100.
- Produces: секция «Задача» в e2e-отчёте (PASS/SKIP/FAIL, exit 0 для PASS/SKIP).

- [ ] **Step 1: Секция в `main()` — после чат-блока (день 12), до финального подведения итогов**

Логика (темплат — чат-блок: SKIP, если GPustack недоступен; чат-шаги уже SKIP — секция задаёт то же условие):

```python
    # 9. задача: FSM-пайплайн (день 13) — SKIP, если GPustack недоступен
    if gpustack_skip:
        record("TASK: пайплайн (FSM)", "SKIP", gpustack_skip)
    else:
        # 9.1 старт
        code, raw, _ = http("POST", "/api/task/start",
                            {"dialogue_id": did, "description": "Сделать простую кнопку"})
        task_ok = code == 200 and json.loads(raw)["task"]["stage"] == "planning"
        # 9.2 run в потоке: SSE блокирует до конца; параллельно — polling + стоп
        box = {}
        def run_task():
            c, r, _ = http("POST", "/api/task/run", {"dialogue_id": did}, timeout=300)
            box["code"], box["raw"] = c, r
        th = threading.Thread(target=run_task, daemon=True)
        th.start()
        # polling: ждём границу стадии (stage != planning)
        boundary = None
        for _ in range(60):
            time.sleep(0.5)
            c, r, _ = http("GET", f"/api/task?dialogue_id={did}")
            st = json.loads(r)["task"]
            if st["stage"] != "planning":
                boundary = st["stage"]
                break
            if not th.is_alive():
                break
        # 9.3 гард чата: активная задача — чат отвечает error (best-effort)
        chat_guard = "n/a"
        if boundary and th.is_alive():
            c, r, _ = http("POST", "/api/chat",
                           {"dialogue_id": did, "message": "привет"}, timeout=60)
            chat_guard = "ок" if "Задача выполняется".encode() in r else "не сработал"
        # 9.4 стоп на границе + instruction
        if boundary:
            http("POST", "/api/task/pause", {"dialogue_id": did})
        th.join(timeout=330)
        paused = b"task_paused" in box.get("raw", b"")
        i_code, _, _ = http("POST", "/api/task/instruction",
                            {"dialogue_id": did, "text": "Добавь обработку нажатия"})
        # 9.5 resume + повторный run до done
        http("POST", "/api/task/resume", {"dialogue_id": did})
        c2, r2, _ = http("POST", "/api/task/run", {"dialogue_id": did}, timeout=300)
        done = b"task_done" in r2
        c3, r3, _ = http("GET", f"/api/task?dialogue_id={did}")
        t3 = json.loads(r3)["task"]
        # 9.6 stage-сообщения в истории диалога
        c4, r4, _ = http("GET", f"/api/dialogues/{did}")
        msgs = json.loads(r4)["dialogue"]["messages"]
        stage_msgs = [m for m in msgs if m.get("task_stage")]
        # 9.7 сброс
        c5, _, _ = http("POST", "/api/task/reset", {"dialogue_id": did})
        ok = (task_ok and paused and i_code == 200 and done
              and t3["stage"] == "done" and len(stage_msgs) >= 3 and c5 == 200)
        record("TASK: пайплайн (FSM)",
               "PASS" if ok else "FAIL",
               f"start={task_ok} paused={paused} instruction={i_code} done={done} "
               f"stage={t3['stage']} stage_msgs={len(stage_msgs)} reset={c5} "
               f"chat_guard={chat_guard}")
```

Импорты: `threading`, `time` (если ещё не импортированы). В `_cleanup` — добавить сброс задачи (`/api/task/reset` для `did`, best-effort).

- [ ] **Step 2: Прогнать**

```powershell
python scripts/e2e_studio.py
```
Expected: PASS (или SKIP chat-шагов при недоступном GPustack — тогда и TASK-секция SKIP); exit 0.

- [ ] **Step 3: Коммит**

```powershell
git add scripts/e2e_studio.py
git commit -m "test(day13-task): e2e — FSM-пайплайн (старт, стоп на границе, instruction, resume, done, stage-сообщения, гард чата, сброс)"
```

---

### Task 9: Документация + финальная проверка

**Files:**
- Modify: `README.md` (таблица веток + секция «День 13»)
- Modify: `openspec/changes/day13-task-state-machine/tasks.md` (галочки по факту)

- [ ] **Step 1: `README.md`**

1. Таблица веток — строка:

```
| День 13 | [`day13-task-state-machine`](https://github.com/imarkelov/ai_advent_challenge/tree/day13-task-state-machine) | Состояние задачи: FSM planning → execution → validation → done, stage-агенты + оркестратор, стоп/продолжение на любом этапе, инструкция на паузе, статус в UI |
```

2. Новая секция в конце (паттерн секции дня 12): «День 13: Состояние задачи (Task State Machine)» — подразделы:
   - **Что это** — задача per-диалог как конечный автомат; stage-агенты (Планировщик/Исполнитель/Валидатор/Оркестратор) = один LLM из конфига, разные system-промпты; оркестратор — детерминированный код (LLM FSM не управляет).
   - **FSM** — `planning → execution → validation → done`; единственный «назад» — `validation → execution` (ретрай, максимум 1, фидбэк = работа + замечания валидатора); метка `<verdict>pass|fail</verdict>` (best-effort, отсутствие = pass).
   - **Хранение** — поле `task` записи диалога (`dialogues.json`); нет поля = неактивна (бэкворд-совместимость); stage-outputs — сообщения истории с меткой `task_stage`.
   - **Стоп/продолжение** — пауза вступает на границе стадии (текущий LLM-вызов доигрывается); на паузе — инструкция (инжектится в ближайшую стадию, используется один раз); возобновление — с сохранённого состояния, прошедшие стадии не повторяются; ошибка LLM-вызова — `error` + пауза, повтор через «Продолжить».
   - **Гард чата** — активная непаузанная незавершённая задача: `POST /api/chat` → SSE error «Задача выполняется…», сообщение не сохраняется; на паузе чат работает.
   - **API** — таблица: `POST /api/task/start|run|pause|resume|instruction|reset`, `GET /api/task?dialogue_id=`; run — SSE `stage/stage_done/task_paused/task_done/error`; 400/404 RU-detail; `task` в выдаче `/api/dialogues*`.
   - **UI** — режим задачи в чате (кнопки Стоп/Продолжить в шапке, статус-строка стадий, чип «задача» у stage-сообщений, блокировка инпута); вкладка «Задача» в панели «Контекст» (запуск по описанию, прогресс, выводы стадий с вердиктами, instruction на паузе, сброс).
   - **Проверка задания** — e2e: старт → стоп на границе planning/execution → instruction → resume → done → stage-сообщения в истории → гард чата → сброс.
   - **Статус** — цифры после финального прогона: backend N тестов PASS; frontend M тестов PASS; E2E K/K PASS; ветка `day13-task-state-machine` (от `day12-user-profile`).

- [ ] **Step 2: `openspec/changes/day13-task-state-machine/tasks.md`** — отметить `[x]` реализованные пункты.

- [ ] **Step 3: Финальная проверка**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
cd studio/frontend; npx tsc --noEmit
python scripts/e2e_studio.py
```
Expected: все PASS (e2e — PASS/SKIP); зафиксировать цифры для README.

- [ ] **Step 4: Коммит**

```powershell
git add README.md openspec/changes/day13-task-state-machine/tasks.md
git commit -m "docs(day13-task): README (День 13) + отметка задач openspec"
```

- [ ] **Step 5: openspec** — `openspec archive day13-task-state-machine` (по процедуре дня 12: только если все артефакты согласованы и реализация завершена).