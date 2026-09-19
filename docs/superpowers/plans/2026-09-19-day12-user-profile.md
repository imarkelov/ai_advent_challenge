# Day 12: User Profile (Персонализация) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Профиль пользователя per-диалог (имя, роль/сфера, тон/стиль, стоп-слова) с инициализацией (интервью / вручную / отказ) и автоматической инъекцией в system-промпт каждого запроса.

**Architecture:** Профиль — поле `profile` в записи диалога (`dialogues.json`). State machine в `StudioAgent.ask_stream`: в `pending`-диалоге ход НЕ уходит в LLM — детерминированное приглашение (вручную/интервью/отказ); интервью = 4 вопроса одним сообщением, из ответа нестриминговый LLM-вызов извлекает JSON-профиль (паттерн `_generate_title`). `active`-профиль — блок в `build_payload` между базовым промптом и блоками памяти. UI: вкладка «Профили» (4-й таб ContextPanel) + бейдж в шапке чата.

**Tech Stack:** Python / FastAPI / httpx (MockTransport) / pytest; React 19 + Vite + TS / Vitest + Testing Library; e2e — `scripts/e2e_studio.py` (prod-сервер :8100).

**Spec:** `openspec/changes/day12-user-profile/` (proposal.md, specs/user-profile/spec.md, design.md, tasks.md)

## Global Constraints

- Ветка: `day12-user-profile`, от `day11-studio` (HEAD `936d112`). Код дней 1–10 не трогаем.
- Тексты UI/API-ошибок — русский; API-ошибки — `HTTPException(400|404, "RU-текст")`.
- Тело `/api/chat` НЕ меняется (`{dialogue_id, message}`). Слои памяти, тумблеры, MEMORY_RULE, `_detect_memory_conflict` — не трогаем.
- Профиль не участвует в конфликт-гарде WM/LT (предпочтения, не факты).
- Служебные LLM-вызовы (экстракт профиля) НЕ пишутся в `requests.json` (паттерн авто-заголовка).
- Бэкенд-тесты офлайн (tmp_path + MockTransport); фронтенд — Vitest со stub `fetch`.
- Коммиты: `feat(day12-profile): …`, `test(day12-profile): …`, `docs(day12-profile): …`.
- Тесты: `cd studio/backend; python -m pytest -q` / `cd studio/frontend; npm test`.

**Статусы профиля:** `pending` → (`interview`-флаг) → `active` | `declined`; `reset` → `pending` (поля очищены).

**Схема** (поле записи диалога в `dialogues.json`):

```json
"profile": {
  "status": "pending | active | declined",
  "interview": false,
  "name": "", "role": "", "tone": "", "taboos": ""
}
```

Отсутствие поля = `pending` (бэкворд-совместимость старых `dialogues.json`).

---

### Task 1: Ветка + baseline

**Files:** Create: ветка `day12-user-profile`.

- [ ] **Step 1: Создать ветку**

```powershell
git checkout day11-studio
git checkout -b day12-user-profile
git branch --show-current
```
Expected: `day12-user-profile`.

- [ ] **Step 2: Baseline-тесты**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
```
Expected: все PASS. Pre-existing failure — зафиксировать и сообщить, не чинить.

- [ ] **Step 3: Закоммитить openspec-артефакты**

```powershell
git add openspec/changes/day12-user-profile
git commit -m "docs(day12-profile): openspec-спецификация (proposal, spec, design, tasks)"
```

---

### Task 2: Хранилище профиля в MemoryStore

**Files:**
- Modify: `studio/backend/memory.py`
- Test: `studio/backend/tests/test_memory.py`

**Interfaces:**
- Produces: `memory.new_profile() -> dict`; `PROFILE_FIELDS`, `PROFILE_ACTIONS` (константы модуля); `MemoryStore.profile_get(dialogue_id) -> dict` (ValueError — диалог не найден); `MemoryStore.profile_set(dialogue_id, name, role, tone, taboos) -> dict` (все 4 — str, ValueError на не-str/неизвестный диалог); `MemoryStore.profile_action(dialogue_id, action) -> dict` (action ∈ `interview|decline|reset`, ValueError на неизвестное). `get_dialogue`/`list_dialogues` получают поле `profile`; `new_dialogue` создаёт запись с `profile: new_profile()`.

- [ ] **Step 1: Написать фейлинговые тесты**

Добавить в `studio/backend/tests/test_memory.py` (импорт в шапке: `from memory import MemoryStore, new_profile`):

```python
class TestProfile:
    def setup_method(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.s = MemoryStore(self.d.name)

    def teardown_method(self):
        self.d.cleanup()

    def test_new_dialogue_has_pending_profile(self):
        d = self.s.new_dialogue()
        assert d["profile"] == new_profile()
        p = self.s.profile_get(d["id"])
        assert p["status"] == "pending"
        assert p["interview"] is False
        assert p["name"] == "" and p["role"] == "" and p["tone"] == "" and p["taboos"] == ""

    def test_profile_set_active_on_nonempty(self):
        d = self.s.new_dialogue()
        p = self.s.profile_set(d["id"], "Иван", "backend-разработчик, e-commerce",
                               "дружелюбно и по делу", "мат, политика")
        assert p["status"] == "active"
        assert self.s.profile_get(d["id"])["name"] == "Иван"
        s2 = MemoryStore(self.d.name)  # переживает рестарт
        assert s2.profile_get(d["id"])["status"] == "active"
        assert s2.profile_get(d["id"])["taboos"] == "мат, политика"

    def test_profile_set_all_empty_keeps_pending(self):
        d = self.s.new_dialogue()
        assert self.s.profile_set(d["id"], "", "", "", "")["status"] == "pending"

    def test_profile_set_rejects_non_str(self):
        import pytest
        d = self.s.new_dialogue()
        with pytest.raises(ValueError):
            self.s.profile_set(d["id"], 5, "", "", "")
        assert self.s.profile_get(d["id"])["status"] == "pending"

    def test_profile_set_unknown_dialogue(self):
        import pytest
        with pytest.raises(ValueError):
            self.s.profile_set("nope", "a", "", "", "")

    def test_profile_action_interview_keeps_pending_sets_flag(self):
        d = self.s.new_dialogue()
        p = self.s.profile_action(d["id"], "interview")
        assert p["status"] == "pending" and p["interview"] is True

    def test_profile_action_decline(self):
        d = self.s.new_dialogue()
        p = self.s.profile_action(d["id"], "decline")
        assert p["status"] == "declined" and p["interview"] is False

    def test_profile_action_reset_clears_fields(self):
        d = self.s.new_dialogue()
        self.s.profile_set(d["id"], "Иван", "роль", "тон", "табу")
        assert self.s.profile_action(d["id"], "reset") == new_profile()

    def test_profile_action_unknown(self):
        import pytest
        d = self.s.new_dialogue()
        with pytest.raises(ValueError):
            self.s.profile_action(d["id"], "bogus")

    def test_profile_isolated_per_dialogue(self):
        a = self.s.new_dialogue()["id"]
        b = self.s.new_dialogue()["id"]
        self.s.profile_set(a, "Иван", "", "", "")
        assert self.s.profile_get(b)["status"] == "pending"
        assert self.s.profile_get(b)["name"] == ""

    def test_profile_removed_with_dialogue(self):
        import pytest
        d = self.s.new_dialogue()
        self.s.profile_set(d["id"], "Иван", "", "", "")
        self.s.delete_dialogue(d["id"])
        with pytest.raises(ValueError):
            self.s.profile_get(d["id"])

    def test_old_dialogue_without_profile_field_is_pending(self):
        """Бэкворд-совместимость: запись диалога без поля profile."""
        import json
        d = self.s.new_dialogue()
        import os
        path = os.path.join(self.d.name, "dialogues.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for dd in data["dialogues"]:
            dd.pop("profile", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        assert self.s.profile_get(d["id"])["status"] == "pending"
        assert self.s.get_dialogue(d["id"])["profile"]["status"] == "pending"
```

- [ ] **Step 2: Прогнать — убедиться, что фейлят**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -q -k Profile`
Expected: FAIL (ImportError `new_profile`).

- [ ] **Step 3: Реализовать в `studio/backend/memory.py`**

После import-блока, до `class MemoryStore`:

```python
# Профиль пользователя (день 12): 4 текстовых поля + статус + флаг интервью.
# Профиль принадлежит диалогу (поле записи в dialogues.json).
PROFILE_FIELDS = ("name", "role", "tone", "taboos")
PROFILE_ACTIONS = ("interview", "decline", "reset")


def new_profile() -> dict:
    """Свежий профиль: pending, пустые поля, интервью не начато."""
    return {"status": "pending", "interview": False,
            "name": "", "role": "", "tone": "", "taboos": ""}
```

В `MemoryStore` — новая секция после `clear_st`:

```python
    # ---------- профиль пользователя (день 12, per-диалог) ----------

    def _profile_of(self, d: dict) -> dict:
        """Профиль записи диалога; отсутствие поля/битое значение — дефолт (pending)."""
        raw = d.get("profile")
        if not isinstance(raw, dict):
            return new_profile()
        p = new_profile()
        for k in ("status", "interview", *PROFILE_FIELDS):
            if k in raw:
                p[k] = raw[k]
        return p

    def profile_get(self, dialogue_id: str) -> dict:
        """Профиль диалога; ValueError, если диалог не существует."""
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            return self._profile_of(d)

    def profile_set(self, dialogue_id: str, name: str, role: str,
                    tone: str, taboos: str) -> dict:
        """Сохранить 4 поля профиля; непустое содержимое → active
        (все пустые → pending); interview сбрасывается. ValueError на не-str
        значения или неизвестный диалог. Возвращает сохранённый профиль."""
        for v in (name, role, tone, taboos):
            if not isinstance(v, str):
                raise ValueError("Поля профиля должны быть строками")
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            d["profile"] = {
                "status": "active" if any((name, role, tone, taboos)) else "pending",
                "interview": False,
                "name": name, "role": role, "tone": tone, "taboos": taboos,
            }
            self._write_dialogues(data)
            return d["profile"]

    def profile_action(self, dialogue_id: str, action: str) -> dict:
        """Действие с профилем: interview (флаг, статус остаётся pending),
        decline (status declined), reset (свежий профиль). ValueError на
        неизвестное действие или неизвестный диалог."""
        if action not in PROFILE_ACTIONS:
            raise ValueError(f"Неизвестное действие профиля: {action}")
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            p = self._profile_of(d)
            if action == "interview":
                p["interview"] = True
            elif action == "decline":
                p["status"] = "declined"
                p["interview"] = False
            else:  # reset
                p = new_profile()
            d["profile"] = p
            self._write_dialogues(data)
            return d["profile"]
```

Три модификации существующих методов:

`new_dialogue` (строка 113-114):

```python
            d = {"id": uuid.uuid4().hex, "title": "Новый диалог",
                 "created": _now(), "messages": [], "profile": new_profile()}
```

`list_dialogues` (строки 124-127) — элемент списка:

```python
            return [{"id": d["id"], "title": d.get("title", ""),
                     "created": d.get("created", ""),
                     "message_count": len(d.get("messages", [])),
                     "profile": self._profile_of(d)}
                    for d in data["dialogues"]]
```

`get_dialogue` (строки 136-138) — возвращаемый словарь:

```python
            return {"id": d["id"], "title": d.get("title", ""),
                    "created": d.get("created", ""),
                    "messages": list(d.get("messages", [])),
                    "profile": self._profile_of(d)}
```

- [ ] **Step 4: Прогнать профиль-тесты**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -q -k Profile`
Expected: PASS (12 тестов).

- [ ] **Step 5: Полный прогон test_memory.py**

Run: `cd studio/backend; python -m pytest tests/test_memory.py -q`
Expected: PASS. Если старый тест сверял выдачу диалога deep-equals без `profile` — добавить поле в ожидаемое.

- [ ] **Step 6: Коммит**

```powershell
git add studio/backend/memory.py studio/backend/tests/test_memory.py
git commit -m "feat(day12-profile): хранилище профиля per-диалог в MemoryStore (статусы pending/active/declined, interview, reset, бэкворд-совместимость)"
```

---

### Task 3: Инъекция профиля в build_payload

**Files:**
- Modify: `studio/backend/agent.py` (`build_profile_block`; строка 179 `build_payload`)
- Test: `studio/backend/tests/test_agent.py`

**Interfaces:**
- Consumes: `store.profile_get/profile_set/profile_action` (Task 2).
- Produces: `StudioAgent.build_profile_block(dialogue_id: str) -> str` — `""` для non-active/пустых полей; иначе `"\n\nПрофиль пользователя:\n- Имя: …\n…"` (+ `"\nИзбегай: <табу>"` при непустых табутах). Порядок в system: базовый промпт → профиль → блоки памяти → MEMORY_RULE.

- [ ] **Step 1: Написать фейлинговые тесты**

В `studio/backend/tests/test_agent.py`, новая секция после `test_build_payload_no_memory_no_rule`:

```python
# ---------- профиль пользователя (день 12): инъекция ----------

def test_profile_block_active_in_system(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "backend-разработчик",
                            "кратко и по делу", "мат")
    agent.store.append_message(d["id"], "user", "привет")
    system = agent.build_payload(d["id"])[0]["content"]
    base = agent.get_config()["system_prompt"]
    assert system.startswith(base + "\n\nПрофиль пользователя:\n")
    assert "- Имя: Иван" in system
    assert "- Роль и сфера: backend-разработчик" in system
    assert "- Тон и стиль: кратко и по делу" in system
    assert "- Стоп-слова/табу: мат" in system
    assert "\nИзбегай: мат" in system


def test_profile_block_empty_fields_skipped(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "")
    system = agent.build_payload(d["id"])[0]["content"]
    assert "\n\nПрофиль пользователя:\n- Имя: Иван" in system
    assert "Избегай:" not in system
    assert "Роль и сфера" not in system  # пустые поля не светим


def test_profile_block_pending_declined_absent(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    assert "Профиль пользователя" not in agent.build_payload(d["id"])[0]["content"]
    agent.store.profile_action(d["id"], "decline")
    assert "Профиль пользователя" not in agent.build_payload(d["id"])[0]["content"]


def test_profile_coexists_with_memory_blocks(data_dir):
    """Профиль + WM/LT: все блоки на месте, порядок — профиль до памяти."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "роль", "тон", "")
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    agent.store.append_message(d["id"], "user", "привет")
    base = agent.get_config()["system_prompt"]
    expected = (base + "\n\nПрофиль пользователя:\n- Имя: Иван\n"
                "- Роль и сфера: роль\n- Тон и стиль: тон"
                + "\n\nТекущая задача:\n- t: задача"
                + "\n\nДолговременная память:\n- u: юзер"
                + MEMORY_RULE)
    assert agent.build_payload(d["id"])[0]["content"] == expected
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k "profile_block or profile_coexists"`
Expected: FAIL.

- [ ] **Step 3: Реализовать в `studio/backend/agent.py`**

Метод `build_profile_block` — сразу перед `build_payload` (строка ~165):

```python
    def build_profile_block(self, dialogue_id: str) -> str:
        """Блок профиля пользователя для system-промпта (день 12).

        Пусто, если профиль не active или все поля пусты. Порядок в
        build_payload: базовый промпт → профиль → блоки памяти."""
        p = self.store.profile_get(dialogue_id)
        if p["status"] != "active":
            return ""
        lines = []
        if p["name"]:
            lines.append(f"- Имя: {p['name']}")
        if p["role"]:
            lines.append(f"- Роль и сфера: {p['role']}")
        if p["tone"]:
            lines.append(f"- Тон и стиль: {p['tone']}")
        if p["taboos"]:
            lines.append(f"- Стоп-слова/табу: {p['taboos']}")
        if not lines:
            return ""
        block = "\n\nПрофиль пользователя:\n" + "\n".join(lines)
        if p["taboos"]:
            block += f"\nИзбегай: {p['taboos']}"
        return block
```

Строка 179 `build_payload`:

```python
        system = (cfg["system_prompt"] + self.build_profile_block(dialogue_id)
                  + blocks + (MEMORY_RULE if blocks else ""))
```

- [ ] **Step 4: Прогнать весь test_agent.py**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q`
Expected: PASS (старые build_payload-тесты не меняются: профиль pending → блок пуст).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/agent.py studio/backend/tests/test_agent.py
git commit -m "feat(day12-profile): инъекция active-профиля в system-промпт (build_profile_block, строка «Избегай» при табутах)"
```

---

### Task 4: LLM-экстракт профиля из ответа на интервью

**Files:**
- Modify: `studio/backend/agent.py` (`_extract_profile`)
- Test: `studio/backend/tests/test_agent.py`

**Interfaces:**
- Produces: `StudioAgent._extract_profile(model: str, answer: str) -> dict | None` — `{"name","role","tone","taboos": str}` или `None` (сбой API / не-JSON). Non-stream; в `requests.json` не пишется.

- [ ] **Step 1: Написать фейлинговые тесты**

```python
# ---------- LLM-экстракт профиля (день 12) ----------

def _profile_extract_handler(content_or_status, calls):
    """non-stream → str (JSON-ответ с content) или int (HTTP-статус)."""
    def handler(request):
        assert "stream" not in json.loads(request.content)
        calls["n"] += 1
        if isinstance(content_or_status, int):
            return httpx.Response(content_or_status, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [
            {"message": {"content": content_or_status}}]})
    return handler


def test_extract_profile_valid_json(data_dir):
    calls = {"n": 0}
    agent = make_agent(data_dir, _profile_extract_handler(
        '{"name": "Иван", "role": "backend", "tone": "кратко", "taboos": "мат"}',
        calls))
    p = agent._extract_profile("qwen3.8-27b", "Иван, backend, кратко, не мат")
    assert p == {"name": "Иван", "role": "backend", "tone": "кратко", "taboos": "мат"}
    assert calls["n"] == 1


def test_extract_profile_fenced_with_garbage(data_dir):
    """Модель «думает» + markdown-ограды: вырезаем объект по {...}."""
    content = ('Хорошо, извлекаю поля.\n```json\n'
               '{"name": "Мария", "role": "дизайнер", "tone": "формально", '
               '"taboos": "шутки"}\n```')
    agent = make_agent(data_dir, _profile_extract_handler(content, {"n": 0}))
    p = agent._extract_profile("glm-5.3-flash", "ответ")
    assert p["name"] == "Мария"
    assert p["taboos"] == "шутки"


def test_extract_profile_missing_fields_become_empty(data_dir):
    content = '{"name": "Иван", "role": "", "tone": "", "taboos": ""}'
    agent = make_agent(data_dir, _profile_extract_handler(content, {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "только имя Иван") == \
        {"name": "Иван", "role": "", "tone": "", "taboos": ""}


def test_extract_profile_non_json_returns_none(data_dir):
    agent = make_agent(data_dir, _profile_extract_handler(
        "не удалось разобрать", {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "мусор") is None


def test_extract_profile_api_error_returns_none(data_dir):
    agent = make_agent(data_dir, _profile_extract_handler(500, {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "ответ") is None
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k extract_profile`
Expected: FAIL (AttributeError).

- [ ] **Step 3: Реализовать `_extract_profile`** — рядом с `_generate_title` (строка ~425):

```python
    def _extract_profile(self, model: str, answer: str) -> dict | None:
        """Извлечь профиль из ответа на анкету (best-effort, non-stream).

        Не входит в requests.json (как авто-заголовок). Успех →
        {"name","role","tone","taboos": str}; сбой/не-JSON → None
        (интервью остаётся активным, агент попросит повторить)."""
        body = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 600,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system",
                 "content": ("Пользователь ответил на анкету профиля: имя; "
                             "профессиональная роль и сфера; тон и стиль "
                             "общения; стоп-слова/табу. Извлеки значения и "
                             "верни ТОЛЬКО JSON-объект без пояснений и без "
                             'markdown: {"name": "...", "role": "...", '
                             '"tone": "...", "taboos": "..."}. Если значение '
                             "не указано — пустая строка.")},
                {"role": "user", "content": answer[:1000]},
            ],
        }
        try:
            resp = self._client.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self._key_for(model)},
                json=body, timeout=60,
            )
            if resp.status_code != 200:
                return None
            content = (((resp.json().get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or "")
            s, e = content.find("{"), content.rfind("}")
            if s == -1 or e <= s:
                return None
            obj = json.loads(content[s:e + 1])
            if not isinstance(obj, dict):
                return None
            return {f: (obj[f].strip() if isinstance(obj.get(f), str) else "")
                    for f in ("name", "role", "tone", "taboos")}
        except (httpx.HTTPError, ValueError):
            return None
```

- [ ] **Step 4: Прогнать**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k extract_profile`
Expected: PASS (5 тестов).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/agent.py studio/backend/tests/test_agent.py
git commit -m "feat(day12-profile): LLM-экстракт профиля из ответа на интервью (best-effort JSON, паттерн _generate_title)"
```

---

### Task 5: State machine инициализации в ask_stream

**Files:**
- Modify: `studio/backend/agent.py` (константы текстов/маркеров, `_profile_init_turn`, ветка в `ask_stream` после авто-заголовка)
- Test: `studio/backend/tests/test_agent.py`

**Interfaces:**
- Consumes: Task 2 (store), Task 4 (`_extract_profile`).
- Produces: в `pending`-диалоге `ask_stream` НЕ вызывает LLM-стрим: ответ — детерминированный текст (приглашение / вопросы интервью / подтверждение / просьба повторить), сохраняется assistant-сообщение (без `model`), `done` с `usage: None, request_id: None`. В `active`/`declined` — обычный LLM-поток. `StudioAgent._profile_init_turn(dialogue_id: str, message: str, model: str) -> str`.

- [ ] **Step 1: Написать фейлинговые тесты**

```python
# ---------- state machine инициализации профиля (день 12) ----------

def _profile_handler(extract_content, calls, streams):
    """non-stream: 1-й — авто-заголовок (пустой), 2-й — экстракт; stream → SSE."""
    def handler(request):
        body = json.loads(request.content)
        if "stream" not in body:
            calls["n"] += 1
            if calls["n"] == 1:  # авто-заголовок
                return httpx.Response(200, json={"choices": [
                    {"message": {"content": "Тайтл"}}]})
            return httpx.Response(200, json={"choices": [
                {"message": {"content": extract_content}}]})
        streams.append(body)
        b = sse_body([delta_chunk("ок"), usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=b.encode("utf-8"))
    return handler


def test_pending_first_message_gets_invite_not_llm(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "объясни лямбды"))
    assert len(streams) == 0  # LLM-стрим НЕ вызывался
    done = events[-1]
    assert done["type"] == "done"
    assert done["usage"] is None and done["request_id"] is None
    assert "инициализировать профиль" in done["answer"]
    assert "вручную" in done["answer"] and "интервью" in done["answer"]
    assert "отказ" in done["answer"]
    # user + assistant сохранены; запрос в journal не ушёл
    msgs = agent.store.get_messages(d["id"])
    assert msgs[0] == {"role": "user", "content": "объясни лямбды"}
    assert msgs[1]["role"] == "assistant"
    assert agent.requests_list() == []


def test_pending_unrecognized_repeats_invite(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "привет"))
    events = list(agent.ask_stream(d["id"], "расскажи про акул"))
    assert len(streams) == 0
    assert "инициализировать профиль" in events[-1]["answer"]
    assert agent.store.profile_get(d["id"])["status"] == "pending"


def test_pending_manual_marker_points_to_tab(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "вручную"))
    assert len(streams) == 0
    assert agent.store.profile_get(d["id"])["status"] == "pending"
    assert agent.store.profile_get(d["id"])["interview"] is False


def test_pending_decline_marker_sets_declined_then_normal_flow(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "отказ"))
    assert agent.store.profile_get(d["id"])["status"] == "declined"
    assert "отказ" in events[-1]["answer"].lower() or "обычно" in events[-1]["answer"]
    # следующий ход — уже обычный LLM-поток, без блока профиля
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" not in streams[0]["messages"][0]["content"]


def test_pending_interview_flow_creates_active_profile(data_dir):
    extract = ('{"name": "Иван", "role": "backend", "tone": "кратко", '
               '"taboos": "мат"}')
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler(extract, calls, streams))
    d = agent.store.new_dialogue()
    # 1) выбор интервью
    e1 = list(agent.ask_stream(d["id"], "интервью"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    assert "имя" in e1[-1]["answer"].lower()
    assert "стоп-слова" in e1[-1]["answer"].lower() or "табу" in e1[-1]["answer"].lower()
    # 2) ответ на анкету → экстракт → active
    e2 = list(agent.ask_stream(d["id"], "Иван, backend-разработчик, кратко, не мат"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "active" and p["name"] == "Иван" and p["taboos"] == "мат"
    assert "сохранён" in e2[-1]["answer"].lower() or "профиль" in e2[-1]["answer"].lower()
    # 3) следующий ход — обычный LLM-поток С блоком профиля
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" in streams[0]["messages"][0]["content"]
    assert "Иван" in streams[0]["messages"][0]["content"]


def test_pending_interview_failed_extraction_repeats_questions(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("не разобрать", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "интервью"))
    events = list(agent.ask_stream(d["id"], "мусор без полей"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    assert "повтор" in events[-1]["answer"].lower()
    assert len(streams) == 0


def test_reset_back_to_pending_repeats_invite(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "")
    assert agent.store.profile_get(d["id"])["status"] == "active"
    agent.store.profile_action(d["id"], "reset")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 0
    assert "инициализировать профиль" in events[-1]["answer"]
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k "pending or reset_back"`
Expected: FAIL (в новых диалогах пока идёт обычный LLM-поток).

- [ ] **Step 3: Реализовать в `studio/backend/agent.py`**

Константы — после `DEFAULT_DIALOGUE_TITLE` (строка ~83):

```python
# Профиль пользователя (день 12): тексты служебного потока инициализации.
# Детерминированные — LLM в них не участвует (мелкие модели не должны
# управлять служебным потоком).
PROFILE_INVITE_TEXT = (
    "Перед началом работы нужно инициализировать профиль пользователя.\n"
    "Выберите один из вариантов:\n"
    "1) заполнить вручную — откройте вкладку «Профили» в панели «Контекст» "
    "справа и сохраните 4 поля (имя, роль и сфера, тон и стиль, "
    "стоп-слова/табу);\n"
    "2) провести интервью — напишите «интервью», я задам 4 вопроса одним "
    "сообщением;\n"
    "3) отказаться — напишите «отказ»; запросы будут выполняться как обычно, "
    "но без учёта ваших предпочтений.")

PROFILE_INTERVIEW_TEXT = (
    "Давайте интервью для профиля. Ответьте, пожалуйста, одним сообщением "
    "на 4 вопроса:\n"
    "1) Как вас зовут (имя пользователя);\n"
    "2) Ваша профессиональная роль и сфера;\n"
    "3) Какой тон и стиль общения вам подходит;\n"
    "4) Стоп-слова/табу — слова или темы, которых нужно избегать.")

PROFILE_MANUAL_TEXT = (
    "Хорошо, заполните профиль вручную: откройте панель «Контекст» справа, "
    "вкладка «Профили», заполните 4 поля и нажмите «Сохранить». После "
    "сохранения профиль будет применяться к каждому запросу автоматически.")

PROFILE_DECLINED_TEXT = (
    "Хорошо, профиль не заполняем. Запросы буду выполнять как обычно; "
    "подсказка о инициализации останется в интерфейсе — вернуться к ней "
    "можно в любой момент.")

PROFILE_INTERVIEW_MARKERS = ("интерв",)
PROFILE_MANUAL_MARKERS = ("вручную",)
PROFILE_DECLINE_MARKERS = ("отказ", "не надо", "не нужно", "не буду",
                           "не заполня", "не хоч")
```

Метод `_profile_init_turn` — рядом с `_detect_memory_conflict`:

```python
    def _profile_init_turn(self, dialogue_id: str, message: str,
                           model: str) -> str:
        """Ход служебного потока инициализации профиля (pending-диалог).

        Возвращает текст ответа; меняет статус профиля:
        - interview-флаг: ответ трактуется как анкета → LLM-экстракт;
          успех → active + подтверждение, сбой → просьба повторить;
        - маркеры в сообщении: интервью/вручную/отказ;
        - иначе → повторное приглашение.
        """
        p = self.store.profile_get(dialogue_id)
        if p["interview"]:
            extracted = self._extract_profile(model, message)
            if extracted:
                self.store.profile_set(dialogue_id, extracted["name"],
                                       extracted["role"], extracted["tone"],
                                       extracted["taboos"])
                return ("Профиль сохранён (имя: {}, роль и сфера: {}, "
                        "тон и стиль: {}, стоп-слова/табу: {}). Теперь "
                        "буду отвечать с учётом ваших предпочтений.".format(
                            extracted["name"] or "—",
                            extracted["role"] or "—",
                            extracted["tone"] or "—",
                            extracted["taboos"] or "—"))
            return ("Не смог разобрать ответ по всем вопросам анкеты. "
                    "Повторите, пожалуйста, одним сообщением: имя, роль и "
                    "сфера, тон и стиль общения, стоп-слова/табу.")
        low = message.lower()
        if any(m in low for m in PROFILE_INTERVIEW_MARKERS):
            self.store.profile_action(dialogue_id, "interview")
            return PROFILE_INTERVIEW_TEXT
        if any(m in low for m in PROFILE_MANUAL_MARKERS):
            return PROFILE_MANUAL_TEXT
        if any(m in low for m in PROFILE_DECLINE_MARKERS):
            self.store.profile_action(dialogue_id, "decline")
            return PROFILE_DECLINED_TEXT
        return PROFILE_INVITE_TEXT
```

Ветка в `ask_stream` — после блока авто-заголовка (после строки 223 `pass`), перед `messages = self.build_payload(...)`:

```python
        # День 12: pending-профиль — служебный ход без LLM-стрима
        # (приглашение/интервью/отказ); первый запрос не выполняется.
        profile = self.store.profile_get(dialogue_id)
        if profile["status"] == "pending":
            answer = self._profile_init_turn(dialogue_id, message,
                                             cfg["model"])
            self.store.append_message(dialogue_id, "assistant", answer)
            yield {"type": "done", "answer": answer,
                   "usage": None, "request_id": None}
            return
```

- [ ] **Step 4: Прогнать новые тесты**

Run: `cd studio/backend; python -m pytest tests/test_agent.py -q -k "pending or reset_back"`
Expected: PASS (7 тестов).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/agent.py studio/backend/tests/test_agent.py
git commit -m "feat(day12-profile): state machine инициализации в ask_stream (приглашение → интервью/вручную/отказ, без LLM-стрима)"
```

---

### Task 6: Коррекция существующих бэкенд-тестов под pending-профиль

**Files:**
- Modify: `studio/backend/tests/test_agent.py` (тесты ask_stream), `studio/backend/tests/test_api.py` (chat-тесты)
- Test: оба файла

**Контекст:** Теперь новые диалоги по умолчанию `pending` — любой `ask_stream`/`POST /api/chat` на новом диалоге идёт в служебный поток, а не в LLM. Все существующие тесты, которые ожидают LLM-ответ, должны сначала «привести диалог в готовый к работе» (decline или profile_set). Паттерн-хелпер:

```python
def ready(agent, d):
    """Диалог готов к обычным запросам (профиль declined)."""
    agent.store.profile_action(d["id"], "decline")
```

- [ ] **Step 1: Прогнать полный бэкенд-свит — собрать фейлы**

Run: `cd studio/backend; python -m pytest -q`
Expected: набор FAIL — тесты ask_stream в test_agent.py и chat-тесты в test_api.py (новые диалоги pending).

- [ ] **Step 2: Исправить test_agent.py**

В каждом тесте ask_stream, где создаётся новый диалог и ожидается LLM-ответ, добавить после `new_dialogue()`:

```python
    agent.store.profile_action(d["id"], "decline")
```

(или через хелпер `ready(agent, d)`). Тесты, где профиль нужен — оставить как есть (их в этом файле нет). Тесты build_payload/авто-заголовка, не вызывающие ask_stream, — не трогаем.

- [ ] **Step 3: Исправить test_api.py**

В каждом тесте, делающем `POST /api/chat` на новом диалоге, после получения dialogue_id добавить:

```python
client.post(f"/api/profile/action", json={"dialogue_id": dialogue_id,
                                          "action": "decline"})
```

(после реализации Task 7; пока — `agent.store.profile_action(...)`, если в фикстуре доступен агент). Тесты memory/wm/lt-эндпоинтов, не делающие chat, — не трогаем.

- [ ] **Step 4: Полный прогон**

Run: `cd studio/backend; python -m pytest -q`
Expected: PASS (все).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/tests
git commit -m "test(day12-profile): адаптировать существующие ask_stream/chat-тесты под pending-профиль по умолчанию"
```

---

### Task 7: REST-эндпоинты /api/profile и /api/profile/action

**Files:**
- Modify: `studio/backend/main.py`
- Test: `studio/backend/tests/test_api.py`

**Interfaces:**
- `POST /api/profile` — body `{"dialogue_id": str, "name": str, "role": str, "tone": str, "taboos": str}` (все 5 обязательны). 200 → `{"profile": {...}}`. 400 — не-str/лишние/отсутствующие поля; 404 — диалог не найден. RU-ошибки.
- `POST /api/profile/action` — body `{"dialogue_id": str, "action": "interview"|"decline"|"reset"}`. 200 → `{"profile": {...}}`. 400 — неизвестное action; 404 — диалог.

- [ ] **Step 1: Написать фейлинговые тесты**

В `studio/backend/tests/test_api.py` (фикстуры `client`, `dialogue_id` уже есть; агент — через фикстуру `agent_env`):

```python
# ---------- профиль: REST (день 12) ----------

def test_profile_set_endpoint(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "Иван",
        "role": "backend", "tone": "кратко", "taboos": "мат"})
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["status"] == "active" and p["name"] == "Иван"
    r2 = client.get(f"/api/dialogues/{dialogue_id}")
    assert r2.json()["profile"]["status"] == "active"


def test_profile_set_empty_keeps_pending(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "", "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 200
    assert r.json()["profile"]["status"] == "pending"


def test_profile_set_non_str_400(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": 5, "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 400
    assert "строки" in r.json()["detail"] or "Поля" in r.json()["detail"]


def test_profile_set_unknown_dialogue_404(client):
    r = client.post("/api/profile", json={
        "dialogue_id": "nope", "name": "a", "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 404


def test_profile_action_interview_decline_reset(client, dialogue_id):
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "interview"})
    assert r.status_code == 200
    assert r.json()["profile"]["interview"] is True
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "decline"})
    assert r.json()["profile"]["status"] == "declined"
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "reset"})
    assert r.json()["profile"]["status"] == "pending"
    assert r.json()["profile"]["name"] == ""


def test_profile_action_unknown_400(client, dialogue_id):
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "bogus"})
    assert r.status_code == 400


def test_profile_action_unknown_dialogue_404(client):
    r = client.post("/api/profile/action", json={
        "dialogue_id": "nope", "action": "decline"})
    assert r.status_code == 404
```

- [ ] **Step 2: Прогнать — фейлят (404 route)**

Run: `cd studio/backend; python -m pytest tests/test_api.py -q -k "profile_set or profile_action"`
Expected: FAIL (FastAPI 404 «Not Found», а не 400/200).

- [ ] **Step 3: Реализовать в `studio/backend/main.py`**

После `POST /api/chat` (строка ~110):

```python
    @app.post("/api/profile")
    def profile_set(body: dict):
        """Сохранить 4 поля профиля диалога (день 12)."""
        keys = ("dialogue_id", "name", "role", "tone", "taboos")
        for k in keys:
            if k not in body:
                raise HTTPException(400, f"Не указано поле «{k}»")
        for k in keys[1:]:
            if not isinstance(body[k], str):
                raise HTTPException(400,
                                    f"Поля профиля должны быть строками")
        try:
            p = mem.profile_set(body["dialogue_id"], body["name"],
                                body["role"], body["tone"], body["taboos"])
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"profile": p}

    @app.post("/api/profile/action")
    def profile_action(body: dict):
        """Действие с профилем: interview / decline / reset (день 12)."""
        if "dialogue_id" not in body or "action" not in body:
            raise HTTPException(400,
                                "Не указаны dialogue_id или action")
        try:
            p = mem.profile_action(body["dialogue_id"], body["action"])
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"profile": p}
```

Примечание: `profile_action` возвращает 404 и на неизвестный `action` (ValueError store → 404). Если нужно 400 на неизвестный action — проверить `body["action"]` против `PROFILE_ACTIONS` (импорт из memory) до вызова store:

```python
        from memory import PROFILE_ACTIONS
        if body["action"] not in PROFILE_ACTIONS:
            raise HTTPException(400,
                                f"Неизвестное действие: {body['action']}")
```

- [ ] **Step 4: Прогнать**

Run: `cd studio/backend; python -m pytest tests/test_api.py -q -k "profile_set or profile_action"`
Expected: PASS (7 тестов).

- [ ] **Step 5: Коммит**

```powershell
git add studio/backend/main.py studio/backend/tests/test_api.py
git commit -m "feat(day12-profile): REST /api/profile и /api/profile/action (400 на не-str/неизвестное, 404 на диалог)"
```

---

### Task 8: Профиль в выдаче диалогов + /api/rules

**Files:**
- Modify: `studio/backend/main.py` (`GET /api/rules`)
- Test: `studio/backend/tests/test_api.py`

**Контекст:** `GET /api/dialogues` и `GET /api/dialogues/{id}` уже возвращают `profile` (Task 2, list/get). Достаём `profile_block` (текст, который уйдёт в промпт) и `profile_status` в `/api/rules` для отладки/проверки.

- [ ] **Step 1: Написать фейлинговый тест**

```python
def test_rules_includes_profile(client, dialogue_id):
    # pending → блок пуст
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert r.json()["profile_block"] == ""
    assert r.json()["profile_status"] == "pending"
    # active → блок заполнен
    client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "Иван", "role": "",
        "tone": "", "taboos": ""})
    r2 = client.get("/api/rules", params={"dialogue_id": dialogue_id})
    assert r2.json()["profile_status"] == "active"
    assert "Иван" in r2.json()["profile_block"]
```

Примечание: `/api/rules` уже принимает `dialogue_id` (см. текущую сигнатуру). Если нет — добавить.

- [ ] **Step 2: Реализовать**

В `GET /api/rules` добавить в ответ:

```python
        profile = mem.profile_get(dialogue_id)
        ...
        "profile_block": agent.build_profile_block(dialogue_id),
        "profile_status": profile["status"],
```

- [ ] **Step 3: Прогнать**

Run: `cd studio/backend; python -m pytest tests/test_api.py -q -k rules`
Expected: PASS.

- [ ] **Step 4: Коммит**

```powershell
git add studio/backend/tests/test_api.py
git commit -m "feat(day12-profile): profile_block/profile_status в /api/rules"
```

---

### Task 9: Frontend — api.ts + state.tsx (типы и деривация профиля)

**Files:**
- Modify: `studio/frontend/src/api.ts`, `studio/frontend/src/state.tsx`
- Test: `studio/frontend/tests/api.test.ts` (или существующий), `studio/frontend/tests/state.test.tsx`

**Interfaces:**
- `UserProfile { status: "pending"|"active"|"declined", interview: boolean, name: string, role: string, tone: string, taboos: string }`
- `DialogueMeta.profile?: UserProfile`; `Dialogue.profile?: UserProfile`
- `apiPostProfile(dialogue_id, name, role, tone, taboos) -> Promise<{profile: UserProfile}>`
- `apiPostProfileAction(dialogue_id, action) -> Promise<{profile: UserProfile}>`
- Профиль берётся из `GET /api/dialogues` (list) — уже возвращается. Reducer держит `profiles: Record<dialogueId, UserProfile>` и `activeProfile: UserProfile | null`.

- [ ] **Step 1: Написать фейлинговые тесты**

В `studio/frontend/tests/` (именя как есть; паттерны `jsonResponse`, `normalizeUrl`, `API_FIXTURES`):

```ts
// api.test.ts
it('POST /api/profile', async () => {
  const resp = jsonResponse({ profile: { status: 'active', interview: false,
    name: 'Иван', role: '', tone: '', taboos: '' } });
  mockFetchOnce(resp);
  const p = await apiPostProfile('d1', 'Иван', '', '', '');
  expect(p.profile.name).toBe('Иван');
  assertLastCall('/api/profile', { dialogue_id: 'd1', name: 'Иван',
    role: '', tone: '', taboos: '' });
});

it('POST /api/profile/action', async () => {
  const resp = jsonResponse({ profile: { status: 'declined', interview: false,
    name: '', role: '', tone: '', taboos: '' } });
  mockFetchOnce(resp);
  const p = await apiPostProfileAction('d1', 'decline');
  expect(p.profile.status).toBe('declined');
});
```

```tsx
// state.test.tsx
it('derives profile from dialogues list', () => {
  // fixture dialogues с profile.active → activeProfile заполнен
});
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/frontend; npm test -- --run api state`
Expected: FAIL (нет `apiPostProfile`/`apiPostProfileAction`).

- [ ] **Step 3: Реализовать `api.ts`**

После `chatStream`/`apiPost`:

```ts
export interface UserProfile {
  status: 'pending' | 'active' | 'declined';
  interview: boolean;
  name: string;
  role: string;
  tone: string;
  taboos: string;
}

export async function apiPostProfile(
  dialogue_id: string, name: string, role: string,
  tone: string, taboos: string,
): Promise<{ profile: UserProfile }> {
  return apiPost('/api/profile', { dialogue_id, name, role, tone, taboos });
}

export async function apiPostProfileAction(
  dialogue_id: string, action: 'interview' | 'decline' | 'reset',
): Promise<{ profile: UserProfile }> {
  return apiPost('/api/profile/action', { dialogue_id, action });
}
```

Добавить `profile?: UserProfile` в `DialogueMeta`/`Dialogue` (там, где определены).

- [ ] **Step 4: Реализовать `state.tsx`**

- Добавить `profiles: Record<string, UserProfile>` в state; `activeProfile` — derived (профиль активного диалога).
- При загрузке `GET /api/dialogues` наполнить `profiles` из `profile` каждого элемента.
- Action `SET_PROFILE { id, profile }` — обновить запись + пересчитать `activeProfile`.
- Экспортировать `useStudio().activeProfile`, `useStudio().setProfile(id, profile)`.

- [ ] **Step 5: Прогнать**

Run: `cd studio/frontend; npm test -- --run api state`
Expected: PASS.

- [ ] **Step 6: Коммит**

```powershell
git add studio/frontend/src/api.ts studio/frontend/src/state.tsx studio/frontend/tests
git commit -m "feat(day12-profile): frontend api (apiPostProfile/Action) + state (profiles/activeProfile из dialogues)"
```

---

### Task 10: Frontend — ProfileTab (4-я вкладка «Профили»)

**Files:**
- Create: `studio/frontend/src/components/ProfileTab.tsx`
- Modify: `studio/frontend/src/components/ContextPanel.tsx` (4-й таб)
- Test: `studio/frontend/tests/profile-tab.test.tsx`

**Интерфейс вкладки (per-активный-диалог):**
- 4 текстовых поля (имя, роль/сфера, тон/стиль, стоп-слова) с текущими значениями `activeProfile`.
- Кнопки: «Сохранить» (POST /api/profile), «Заполнить заново» (POST action=reset), «Отказаться» (POST action=decline), «Провести интервью» (POST action=interview → подсказка написать «интервью» в чат).
- Индикатор статуса (pending/active/declined).

- [ ] **Step 1: Написать фейлинговый тест**

```tsx
// profile-tab.test.tsx
describe('ProfileTab', () => {
  it('renders 4 fields with current values', () => {
    renderTab({ profile: { status: 'active', interview: false,
      name: 'Иван', role: 'backend', tone: 'кратко', taboos: 'мат' } });
    expect(screen.getByDisplayValue('Иван')).toBeInTheDocument();
    expect(screen.getByDisplayValue('backend')).toBeInTheDocument();
  });

  it('save posts /api/profile', async () => {
    mockFetchOnce(jsonResponse({ profile: { status: 'active', interview: false,
      name: 'Мария', role: '', tone: '', taboos: '' } }));
    renderTab({ profile: pendingProfile() });
    await userEvent.type(screen.getByLabelText(/имя/i), 'Мария');
    await userEvent.click(screen.getByRole('button', { name: /сохранить/i }));
    expect(lastCallUrl()).toBe('/api/profile');
  });

  it('decline posts action=decline', async () => {
    mockFetchOnce(jsonResponse({ profile: { status: 'declined', interview: false,
      name: '', role: '', tone: '', taboos: '' } }));
    renderTab({ profile: pendingProfile() });
    await userEvent.click(screen.getByRole('button', { name: /отказаться/i }));
    expect(lastCallBody()).toEqual({ dialogue_id: 'd1', action: 'decline' });
  });

  it('interview posts action=interview and hints chat', async () => {
    mockFetchOnce(jsonResponse({ profile: { status: 'pending', interview: true,
      name: '', role: '', tone: '', taboos: '' } }));
    renderTab({ profile: pendingProfile() });
    await userEvent.click(screen.getByRole('button', { name: /интервью/i }));
    expect(screen.getByText(/напишите.*интервью/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/frontend; npm test -- --run profile-tab`
Expected: FAIL (нет компонента).

- [ ] **Step 3: Реализовать `ProfileTab.tsx`** (паттерн — `MemoryTab.tsx`)

```tsx
import { useEffect, useState } from 'react';
import { apiPostProfile, apiPostProfileAction, type UserProfile } from '../api';
import { useStudio } from '../state';

export default function ProfileTab() {
  const { activeDialogueId, activeProfile, setProfile } = useStudio();
  const p = activeProfile;
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [tone, setTone] = useState('');
  const [taboos, setTaboos] = useState('');
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState('');

  useEffect(() => {
    setName(p?.name ?? ''); setRole(p?.role ?? '');
    setTone(p?.tone ?? ''); setTaboos(p?.taboos ?? ''); setHint('');
  }, [activeDialogueId, p?.status]);

  if (!activeDialogueId || !p) return <div>Нет активного диалога</div>;

  const save = async () => {
    setBusy(true);
    const { profile } = await apiPostProfile(activeDialogueId, name, role, tone, taboos);
    setProfile(activeDialogueId, profile); setBusy(false);
  };
  const action = async (a: 'interview' | 'decline' | 'reset') => {
    setBusy(true);
    const { profile } = await apiPostProfileAction(activeDialogueId, a);
    setProfile(activeDialogueId, profile); setBusy(false);
    if (a === 'interview') setHint('Напишите «интервью» в чате, чтобы начать');
  };

  return (
    <div className="space-y-3">
      <StatusBadge status={p.status} />
      <Field label="Имя пользователя" value={name} onChange={setName} />
      <Field label="Роль и сфера" value={role} onChange={setRole} />
      <Field label="Тон и стиль общения" value={tone} onChange={setTone} />
      <Field label="Стоп-слова / табу" value={taboos} onChange={setTaboos} />
      <div className="flex flex-wrap gap-2">
        <button disabled={busy} onClick={save}>Сохранить</button>
        <button disabled={busy} onClick={() => action('interview')}>Провести интервью</button>
        <button disabled={busy} onClick={() => action('reset')}>Заполнить заново</button>
        <button disabled={busy} onClick={() => action('decline')}>Отказаться</button>
      </div>
      {hint && <p className="text-sm">{hint}</p>}
    </div>
  );
}
```

(`Field`/`StatusBadge` — мелкие локальные подкомпоненты в том же файле; стили — по паттерну MemoryTab.)

- [ ] **Step 4: Включить вкладку в `ContextPanel.tsx`**

В массив `TABS` добавить `{ id: 'profile', label: 'Профили' }`; в render — `{tab === 'profile' && <ProfileTab />}`.

- [ ] **Step 5: Прогнать**

Run: `cd studio/frontend; npm test -- --run profile-tab context-panel`
Expected: PASS.

- [ ] **Step 6: Коммит**

```powershell
git add studio/frontend/src/components/ProfileTab.tsx studio/frontend/src/components/ContextPanel.tsx studio/frontend/tests
git commit -m "feat(day12-profile): вкладка «Профили» (4 поля + сохранить/интервью/заново/отказаться)"
```

---

### Task 11: Frontend — бейдж инициализации в шапке чата

**Files:**
- Modify: `studio/frontend/src/components/ChatPanel.tsx`
- Test: `studio/frontend/tests/chat-panel.test.tsx`

**Поведение:** Если `activeProfile.status ∈ {pending, declined}` — в шапке чата бейдж («Профиль не заполнен» для pending, «Профиль отключён» для declined). Клик по бейджу → открыть вкладку «Профили» (установить `contextTab='profile'`).

- [ ] **Step 1: Написать фейлинговый тест**

```tsx
it('shows profile badge when pending, click opens profile tab', async () => {
  renderChat({ activeProfile: { status: 'pending', interview: false,
    name: '', role: '', tone: '', taboos: '' } });
  const badge = screen.getByRole('button', { name: /профиль не заполнен/i });
  expect(badge).toBeInTheDocument();
  await userEvent.click(badge);
  expect(store.contextTab).toBe('profile');
});

it('no badge when active', () => {
  renderChat({ activeProfile: { status: 'active', interview: false,
    name: 'Иван', role: '', tone: '', taboos: '' } });
  expect(screen.queryByRole('button', { name: /профиль/i })).toBeNull();
});

it('declined shows different badge text', () => {
  renderChat({ activeProfile: { status: 'declined', interview: false,
    name: '', role: '', tone: '', taboos: '' } });
  expect(screen.getByRole('button', { name: /профиль отключён/i }))
    .toBeInTheDocument();
});
```

- [ ] **Step 2: Прогнать — фейлят**

Run: `cd studio/frontend; npm test -- --run chat-panel`
Expected: FAIL (бейджа нет).

- [ ] **Step 3: Реализовать в `ChatPanel.tsx`**

В шапке, рядом с дропдауном модели:

```tsx
{activeProfile && activeProfile.status !== 'active' && (
  <button
    className={/* бейдж: pending=amber, declined=slate */}
    onClick={() => setContextTab('profile')}
    title="Открыть вкладку «Профили»"
  >
    {activeProfile.status === 'declined' ? 'Профиль отключён' : 'Профиль не заполнен'}
  </button>
)}
```

(`setContextTab` — из state; добавить в provider, если нет.)

- [ ] **Step 4: Прогнать**

Run: `cd studio/frontend; npm test -- --run chat-panel`
Expected: PASS (все chat-panel-тесты).

- [ ] **Step 5: Коммит**

```powershell
git add studio/frontend/src/components/ChatPanel.tsx studio/frontend/tests
git commit -m "feat(day12-profile): бейдж инициализации профиля в шапке чата (клик → вкладка «Профили»)"
```

---

### Task 12: e2e-блок «Профиль» в scripts/e2e_studio.py

**Files:**
- Modify: `scripts/e2e_studio.py`

**Контекст:** e2e поднимает prod-сервер на :8100 и гоняет HTTP-сценарий. Добавить блок: диалог → GET /api/dialogues/{id} (profile.pending) → POST /api/profile (active) → проверить, что GET /api/rules вернул profile_block с именем → POST /api/profile/action decline → проверка status declined. Ожидаемые значения — из реального LLM-ответа НЕ зависят (профиль-эндпоинты детерминированы).

- [ ] **Step 1: Добавить блок** (паттерн — существующие шаги e2e; helper `check(name, cond)` / `assert` как в файле):

```python
# --- День 12: профиль ---
did = create_dialogue()
prof = get(f"/api/dialogues/{did}")["profile"]
check("профиль по умолчанию pending", prof["status"] == "pending")

prof = post("/api/profile", {"dialogue_id": did, "name": "Иван",
    "role": "backend", "tone": "кратко", "taboos": ""})["profile"]
check("профиль active после set", prof["status"] == "active" and prof["name"] == "Иван")

rules = get(f"/api/rules?dialogue_id={did}")
check("profile_block в rules", "Иван" in rules["profile_block"])

prof = post("/api/profile/action", {"dialogue_id": did, "action": "decline"})["profile"]
check("профиль declined после action", prof["status"] == "declined")
```

(Адаптировать под реальные имена helper-функций в e2e-скрипте.)

- [ ] **Step 2: Прогнать e2e**

Run: `python scripts/e2e_studio.py`
Expected: все шаги PASS (включая новые 4).

- [ ] **Step 3: Коммит**

```powershell
git add scripts/e2e_studio.py
git commit -m "test(day12-profile): e2e-блок «Профиль» (pending→active→block в rules→declined)"
```

---

### Task 13: README + финальная верификация

**Files:**
- Modify: `README.md` (секция «День 12» + строка ветки в таблице)

- [ ] **Step 1: README**

Добавить секцию «День 12: Персонализация (профиль пользователя)» по паттерну секций дней 11/9: что делает (per-диалог, 4 поля, 3 способа инициализации, автоинъекция, бейдж, вкладка «Профили»), как проверить (разные профили → разные ответы), тесты. В таблице веток — строка `day12-user-profile`.

- [ ] **Step 2: Финальная верификация (всё)**

```powershell
cd studio/backend; python -m pytest -q
cd studio/frontend; npm test
python scripts/e2e_studio.py
```
Expected: все три PASS (e2e — PASS/SKIP по чату, если GPustack недоступен).

- [ ] **Step 3: Коммит**

```powershell
git add README.md
git commit -m "docs(day12-profile): секция «День 12» в README + строка ветки"
```

- [ ] **Step 4: Открытый вопрос для пользователя**

Только после зелёного свита: предложить закоммитить и запушить ветку `day12-user-profile` (пуш только по явному запросу).

---

## Self-Review (план против спеки)

| Требование спеки | Задача |
|---|---|
| 4 поля + статус per-диалог | Task 2 |
| Бэкворд (нет поля = pending) | Task 2 (test old_dialogue) |
| Инъекция active в system | Task 3 |
| Интервью (4 вопроса, LLM-экстракт) | Task 4, 5 |
| Вручную (вкладка) | Task 10 |
| Отказ + бейдж | Task 5 (текст), 11 (бейдж) |
| Первый запрос не выполняется в pending | Task 5 |
| REST /api/profile, /api/profile/action | Task 7 |
| Профиль в выдаче диалогов + /api/rules | Task 2 (list/get), 8 (rules) |
| Разные профили → разные ответы | Task 3 (coexists тест) + e2e Task 12 |
| Автоматический учёт профиля | Task 3 |

Покрытие полное. Точные номера строк — ориентировочные (сверить по факту при реализации).
