# QA-Lead Orchestrator (studio2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `studio2` — an autonomous QA orchestration app where a `qa-lead` agent studies a real Android project via git + RAG, writes an SDD per subagent, produces a test skeleton for human approval, generates a Kotlin/Espresso test, runs it on an emulator, debugs failures, and has a second LLM (`reviewer`) validate loaded/MCP-sourced tests.

**Architecture:** FastAPI backend (stdlib + fastapi/httpx/pytest) with MD-declared agents under `studio2/doc/agents/`, JSON runtime store, BM25-lite RAG over `doc/kb|tasks|agents`, pluggable LLM providers (`fake` | `ollama` | `gpustack`), adb/gradle adapters, MCP stdio client, and a stage machine (`sdd → study → skeleton → write → provision → execute → debug → review → done`) with two human gates. React 19 + Vite + TS frontend renders a monochrome QA dashboard (3-pane) fed by SSE.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, pytest, python-dotenv; React 19, Vite, TypeScript, Vitest, Testing Library; ADB + Gradle wrapper; Ollama (`localhost:11435`).

**Spec:** `docs/superpowers/specs/2026-10-18-day35-qa-lead-orchestrator-design.md`

## Global Constraints

- Project root: `studio2/` (new, does not exist yet). Do NOT modify `studio/`.
- Backend third-party deps limited to: `fastapi`, `uvicorn`, `httpx`, `pytest`, `python-dotenv`. Nothing else.
- All user-facing error strings are Russian.
- Runtime secrets live only in `studio2/data/` (git-ignored); never log API keys.
- SSE wire format is exactly `data: {json}\n\n`.
- Agent behaviour is declared in `studio2/doc/agents/*.md` (YAML front-matter + Markdown body). No role prompt text hard-coded in Python.
- Stage order is exactly: `sdd → study → skeleton → write → provision → execute → debug → review → done`; `review → study` (round 2) is the only backward edge; round 3 is forbidden.
- `reviewer` model MUST differ from `testwriter` model when `critic.must_differ_from_author` is true.
- `doc/runs/<run_id>/` is written only by the owning run.
- Default Ollama base URL `http://localhost:11435`; available models: `qwen3-coder:30b`, `gemma4:12b`, `qwen3.5:9b`.
- Android SDK `C:\Users\migor\AppData\Local\Android\Sdk`; default AVD `Pixel_8_Pro_API_33`; app package `ru.leroymerlin.mobile`.
- Default target repo `C:\Users\migor\StudioProjects\lmru--mobile-customer--app`.
- Frontend palette: `--bg:#0f1115; --panel:#151922; --line:#232935; --text:#d8dee9; --muted:#7b8794; --accent:#4ea1ff; --ok:#3fb950; --warn:#d29922; --err:#f85149; --radius:6px`.
- Frontend dev port `5174` (studio uses `5173`); backend port `8100`.
- Backend tests run from `studio2/backend` with `python -m pytest -q`; frontend from `studio2/frontend` with `npm test`.
- Commit after every task.

---

## File Structure

```
studio2/
├── doc/
│   ├── agents/{qa-lead,researcher,testwriter,debugger,reviewer,device-inspector,orchestrator-protocol}.md
│   ├── templates/{sdd,question,review-report,run-summary}.md
│   ├── kb/{architecture,patterns,principles,test-design,questions}.md
│   ├── kb/screens/.gitkeep
│   ├── tasks/TC-4217.md
│   └── runs/                                  (git-ignored, created at runtime)
├── backend/
│   ├── __init__.py
│   ├── pytest.ini
│   ├── config.py          config defaults + data/config.json + .env
│   ├── roles.py           parse front-matter -> AgentSpec; load all agents
│   ├── store.py           RunStore (JSON per run)
│   ├── rag.py             BM25-lite index over doc/
│   ├── docstore.py        doc/ tree, read, write, upload, delete with path guards
│   ├── git_ops.py         branch, log, diff, blame, recent_changes
│   ├── llm.py             LlmClient: fake | ollama | gpustack
│   ├── android.py         adb adapter + uiautomator XML -> tree -> screen md
│   ├── gradle.py          assemble, connected test, log parsing
│   ├── mcp.py             MCP stdio client + server registry
│   ├── events.py          EventBus: SSE queues + question blocking
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── artifacts.py   RunDir + SDD/skeleton/report writers
│   │   ├── subagents.py   reply parsing, tool allow-list, subagent runner
│   │   └── lead.py        QaLead orchestrator: stages, gates, rounds
│   ├── main.py            FastAPI app + all endpoints
│   └── tests/
│       ├── conftest.py
│       ├── test_config.py
│       ├── test_roles.py
│       ├── test_rag.py
│       ├── test_docstore.py
│       ├── test_git_ops.py
│       ├── test_llm.py
│       ├── test_android.py
│       ├── test_gradle.py
│       ├── test_mcp.py
│       ├── test_store_events.py
│       ├── test_subagents.py
│       ├── test_pipeline.py
│       ├── test_invariants.py
│       ├── test_api_config_doc.py
│       ├── test_api_runs.py
│       └── test_api_mcp.py
├── frontend/
│   ├── package.json, tsconfig*.json, vite.config.ts, index.html
│   ├── src/{main.tsx,App.tsx,api.ts,state.ts,styles.css}
│   ├── src/components/{Header,Sidebar,ModelsTab,RagTab,McpTab,TasksTab,PipelineTab,RunsTab,Inspector,QuestionModal,SkeletonEditor}.tsx
│   └── tests/{setup.ts,api.test.ts,state.test.ts,models-tab.test.tsx,pipeline-tab.test.tsx}
├── scripts/e2e_day35.py
├── data/.gitkeep
├── mcp_servers/.gitkeep
└── README.md
```

Each backend module owns one concern. Pipeline modules depend on domain modules but never on `main.py`. `main.py` is the only place that wires things together.

---

### Task 1: Backend skeleton, config layer, gitignore

**Files:**
- Create: `studio2/backend/__init__.py`
- Create: `studio2/backend/pytest.ini`
- Create: `studio2/backend/config.py`
- Create: `studio2/backend/tests/conftest.py`
- Create: `studio2/backend/tests/test_config.py`
- Create: `studio2/data/.gitkeep`
- Create: `studio2/mcp_servers/.gitkeep`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `DEFAULT_CONFIG: dict`, `load_config(data_dir: str) -> dict`, `save_config(data_dir: str, cfg: dict) -> dict`, `mask_secret(value: str | None) -> str`, `resolve_provider(cfg: dict, role: str) -> dict`.
- `resolve_provider` returns `{"name", "base_url", "api_key", "model", "temperature"}`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/conftest.py`:
```python
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
```

`studio2/backend/tests/test_config.py`:
```python
import json

from config import DEFAULT_CONFIG, load_config, mask_secret, resolve_provider, save_config


def test_default_config_has_five_roles(tmp_path):
    cfg = load_config(str(tmp_path))
    assert set(cfg["roles"]) == {"qa-lead", "researcher", "testwriter", "debugger", "reviewer"}
    assert cfg["critic"]["must_differ_from_author"] is True
    assert cfg["providers"]["ollama"]["base_url"] == "http://localhost:11435"
    assert cfg["android"]["avd"] == "Pixel_8_Pro_API_33"


def test_save_then_load_roundtrip(tmp_path):
    cfg = load_config(str(tmp_path))
    cfg["roles"]["reviewer"]["model"] = "gemma4:12b"
    save_config(str(tmp_path), cfg)
    assert load_config(str(tmp_path))["roles"]["reviewer"]["model"] == "gemma4:12b"


def test_partial_file_merges_over_defaults(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"roles": {"reviewer": {"model": "x"}}}), encoding="utf-8"
    )
    cfg = load_config(str(tmp_path))
    assert cfg["roles"]["reviewer"]["model"] == "x"
    assert cfg["roles"]["testwriter"]["model"] == DEFAULT_CONFIG["roles"]["testwriter"]["model"]


def test_mask_secret():
    assert mask_secret(None) == ""
    assert mask_secret("abc") == "***"
    assert mask_secret("gpusta_1234567890") == "gpus***7890"


def test_resolve_provider_for_role(tmp_path):
    cfg = load_config(str(tmp_path))
    provider = resolve_provider(cfg, "reviewer")
    assert provider["name"] == "ollama"
    assert provider["model"] == "gemma4:12b"
    assert provider["temperature"] == 0.1
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `studio2/backend`): `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write implementation**

`studio2/backend/__init__.py`: empty file.

`studio2/backend/pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = .
```

`studio2/backend/config.py`:
```python
"""Конфигурация studio2: умолчания + data/config.json + .env."""
from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    "providers": {
        "ollama": {"base_url": "http://localhost:11435", "api_key": None},
        "gpustack": {"base_url": "https://gpustack.data.lmru.tech/v1", "api_key": None},
        "fake": {"base_url": None, "api_key": None},
    },
    "roles": {
        "qa-lead": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.2},
        "researcher": {"provider": "ollama", "model": "qwen3.5:9b", "temperature": 0.2},
        "testwriter": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.4},
        "debugger": {"provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.3},
        "reviewer": {"provider": "ollama", "model": "gemma4:12b", "temperature": 0.1},
    },
    "critic": {"must_differ_from_author": True},
    "android": {
        "sdk_dir": r"C:\Users\migor\AppData\Local\Android\Sdk",
        "avd": "Pixel_8_Pro_API_33",
        "repo_path": r"C:\Users\migor\StudioProjects\lmru--mobile-customer--app",
        "package": "ru.leroymerlin.mobile",
        "test_module": ":android:app",
    },
}

ENV_MAP = {
    ("providers", "gpustack", "base_url"): "GPUSTACK_BASE_URL",
    ("providers", "gpustack", "api_key"): "GPUSTACK_API_KEY",
    ("providers", "ollama", "base_url"): "OLLAMA_BASE_URL",
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(data_dir: str) -> dict:
    path = Path(data_dir) / "config.json"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            stored = {}
        if isinstance(stored, dict):
            cfg = _deep_merge(cfg, stored)
    for (section, provider, field), env_name in ENV_MAP.items():
        value = os.getenv(env_name)
        if value and not cfg[section][provider].get(field):
            cfg[section][provider][field] = value
    return cfg


def save_config(data_dir: str, cfg: dict) -> dict:
    path = Path(data_dir) / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def resolve_provider(cfg: dict, role: str) -> dict:
    spec = cfg["roles"].get(role, {})
    name = spec.get("provider", "fake")
    provider = dict(cfg["providers"].get(name) or {"base_url": None, "api_key": None})
    provider["name"] = name
    provider["model"] = spec.get("model", "")
    provider["temperature"] = spec.get("temperature", 0.2)
    return provider
```

Append to root `.gitignore`:
```gitignore

# Day 35: studio2 runtime
studio2/data/
studio2/doc/runs/
studio2/backend/.pytest_cache/
studio2/backend/__pycache__/
studio2/frontend/node_modules/
studio2/frontend/dist/
!studio2/data/.gitkeep
```

`studio2/data/.gitkeep` and `studio2/mcp_servers/.gitkeep`: empty files.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add .gitignore studio2/backend/__init__.py studio2/backend/pytest.ini studio2/backend/config.py studio2/backend/tests/conftest.py studio2/backend/tests/test_config.py studio2/data/.gitkeep studio2/mcp_servers/.gitkeep
git commit -m "feat(studio2): backend skeleton and config layer"
```

---

### Task 2: Agent declarations and role parser

**Files:**
- Create: `studio2/doc/agents/orchestrator-protocol.md`
- Create: `studio2/doc/agents/qa-lead.md`
- Create: `studio2/doc/agents/researcher.md`
- Create: `studio2/doc/agents/testwriter.md`
- Create: `studio2/doc/agents/debugger.md`
- Create: `studio2/doc/agents/reviewer.md`
- Create: `studio2/doc/agents/device-inspector.md`
- Create: `studio2/backend/roles.py`
- Create: `studio2/backend/tests/test_roles.py`

**Interfaces:**
- Produces: `@dataclass AgentSpec(role, display_name, model, temperature, stage, allowed_tools, inputs, outputs, max_rounds, on_no_info, body, path)`, `parse_agent(text: str, path: str = "") -> AgentSpec`, `load_agents(doc_dir: str) -> dict[str, AgentSpec]`.
- `load_agents` skips files whose `role` is `protocol`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_roles.py`:
```python
from pathlib import Path

import pytest

from roles import AgentSpec, load_agents, parse_agent

SAMPLE = """---
role: researcher
display_name: Исследователь
model: qwen3.5:9b
temperature: 0.2
stage: study
allowed_tools: [git_ops.read_branch, rag.search, rag.write]
inputs: [sdd/agent_researcher.md]
outputs: [kb/architecture.md]
max_rounds: 2
on_no_info: ask_human
---

## Роль
Ты — Исследователь.
"""

DOC_DIR = Path(__file__).resolve().parents[2] / "doc"


def test_parse_agent_front_matter():
    spec = parse_agent(SAMPLE, "researcher.md")
    assert isinstance(spec, AgentSpec)
    assert spec.role == "researcher"
    assert spec.display_name == "Исследователь"
    assert spec.model == "qwen3.5:9b"
    assert spec.temperature == 0.2
    assert spec.stage == "study"
    assert spec.allowed_tools == ["git_ops.read_branch", "rag.search", "rag.write"]
    assert spec.inputs == ["sdd/agent_researcher.md"]
    assert spec.outputs == ["kb/architecture.md"]
    assert spec.max_rounds == 2
    assert spec.on_no_info == "ask_human"
    assert "Ты — Исследователь." in spec.body


def test_parse_agent_rejects_missing_front_matter():
    with pytest.raises(ValueError):
        parse_agent("no front matter")


def test_parse_agent_rejects_missing_role():
    with pytest.raises(ValueError):
        parse_agent("---\ndisplay_name: X\n---\nbody")


def test_load_agents_reads_repository_docs():
    agents = load_agents(str(DOC_DIR))
    assert set(agents) == {
        "qa-lead", "researcher", "testwriter", "debugger", "reviewer", "device-inspector"
    }
    assert agents["qa-lead"].stage == "sdd"
    assert agents["reviewer"].model == "gemma4:12b"
    assert agents["testwriter"].model != agents["reviewer"].model


def test_agent_bodies_are_real_prompts():
    for spec in load_agents(str(DOC_DIR)).values():
        assert len(spec.body) > 200, spec.role
        assert spec.allowed_tools, spec.role
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_roles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'roles'`

- [ ] **Step 3: Write the seven agent declaration files**

`studio2/doc/agents/orchestrator-protocol.md`:
```markdown
---
role: protocol
display_name: Протокол оркестрации
model: none
temperature: 0
stage: none
allowed_tools: []
inputs: []
outputs: []
max_rounds: 0
on_no_info: none
---

# Протокол оркестрации QA-Lead

## Роли и порядок

1. `qa-lead` — генерирует SDD на каждого субагента и ведёт стадии.
2. `researcher` — изучает проект через git и RAG, ведёт `doc/kb/*`.
3. `testwriter` — сначала каркас, после апрува — Kotlin-тест.
4. `debugger` — разбирает падения прогона и правит только тест.
5. `reviewer` — отдельная LLM, проверяет результаты загруженных и MCP-тестов.

## Обмен

Каждый субагент отвечает JSON-объектом:

```json
{"status": "done|need_question|fail", "artifacts": [], "summary": "", "question": null}
```

## Лимиты

- `max_rounds = 2`: study round 1 и round 2.
- Инструменты вне `allowed_tools` запрещены.
- Субагент пишет только в свой `write_scope`.

## Гейты

- `skeleton → write` только после апрува каркаса человеком.
- `write → provision` только после выбора способа исполнения.
- Вопрос человеку блокирует пайплайн до ответа или таймаута 30 минут.
```

`studio2/doc/agents/qa-lead.md`:
```markdown
---
role: qa-lead
display_name: QA-Lead
model: qwen3-coder:30b
temperature: 0.2
stage: sdd
allowed_tools: [doc.read, git_ops.read_branch, rag.search, sdd.write, report.write]
inputs: [tasks/<task_id>.md, kb/architecture.md]
outputs: [sdd/agent_qa-lead.md, sdd/agent_researcher.md, sdd/agent_testwriter.md, sdd/agent_debugger.md, sdd/agent_reviewer.md]
max_rounds: 2
on_no_info: ask_human
---

## Роль

Ты — QA-Lead, оркестратор пайплайна автотестирования Android-приложения.
Ты не пишешь код тестов и не меняешь требования тест-кейса.

## Что делаешь

1. Читаешь тест-кейс (ТК) из `doc/tasks/<task_id>.md`.
2. Смотришь, что изменилось в ветке git: коммиты, diff, затрагиваемые экраны.
3. Определяешь, какие экраны и теги нужны для сценария из ТК.
4. Генерируешь SDD на каждого субагента по шаблону `doc/templates/sdd.md`.
5. Ведёшь стадии: sdd, study, skeleton, write, provision, execute, debug,
   review, done.
6. Если субагент вернул `need_question`, формулируешь вопрос человеку
   по шаблону `doc/templates/question.md`.
7. Если ревьюер вернул `red` по загруженным тестам, запускаешь round 2 изучения.
8. Пишешь итоговый `test_report.md`.

## Ограничения

- Не пишешь код теста и не правишь его.
- Максимум 2 круга изучения, третий запрещён.
- Не подменяешь ожидаемый результат из ТК, чтобы тест прошёл.
```

`studio2/doc/agents/researcher.md`:
```markdown
---
role: researcher
display_name: Исследователь
model: qwen3.5:9b
temperature: 0.2
stage: study
allowed_tools: [git_ops.read_branch, git_ops.log_diff, git_ops.blame, rag.search, rag.write, doc.read, android.dump]
inputs: [sdd/agent_researcher.md, kb/architecture.md, kb/patterns.md]
outputs: [kb/architecture.md, kb/patterns.md, kb/principles.md, kb/screens]
max_rounds: 2
on_no_info: ask_human
---

## Роль

Ты — Исследователь QA. Изучаешь проект и наполняешь базу знаний фактами,
а не догадками.

## Что делаешь

1. Читаешь свой SDD и тест-кейс.
2. Ищешь в RAG: экран, фичу, тег, существующие тесты по этому экрану.
3. Смотришь git: список веток, `log`, `diff` ветки против `develop`, `blame`
   по изменённым файлам. Это показывает, какой функционал добавил разработчик
   и как он реализован.
4. Читаешь исходники модуля экрана, если тегов или навигации не хватает.
5. Пишешь или дописываешь базу знаний:
   - `kb/architecture.md` — модули, экраны, навигация, ключевые классы;
   - `kb/patterns.md` — как устроены существующие тесты: Kaspresso, Kakao,
     Compose UI Test, Screen-объекты, `withTestTag`, `tagged`, фикстуры
     `assets/responses`, Allure-аннотации;
   - `kb/principles.md` — что и как проверяем: позитив, негатив, границы.
6. Если тегов экрана нет в базе, просишь снять иерархию через device-inspector.

## Когда не хватает информации

- Круг 1: RAG, затем git, затем код.
- Круг 2: другой запрос или другой источник, например device-inspector.
- Круги исчерпаны: `status: need_question` и вопрос по шаблону.

## Формат ответа

```json
{"status": "done|need_question|fail", "artifacts": ["kb/architecture.md"], "summary": "", "question": null}
```
```

`studio2/doc/agents/testwriter.md`:
```markdown
---
role: testwriter
display_name: Тестировщик
model: qwen3-coder:30b
temperature: 0.4
stage: skeleton
allowed_tools: [doc.read, rag.search, skeleton.write, test.write]
inputs: [sdd/agent_testwriter.md, kb/patterns.md, kb/screens]
outputs: [skeleton.md, test_generated]
max_rounds: 1
on_no_info: ask_human
---

## Роль

Ты — Тестировщик. Работаешь в два прохода: сначала каркас, потом код.

## Проход 1: каркас

Каркас без исполняемого кода. Обязательные разделы:

1. Шаги (структура) — по одному шагу на действие из ТК.
2. Используемые теги — только реальные теги из `kb/screens/*.md`.
3. Входные данные — что вводим и откуда берём.
4. Границы — что не проверяем и почему.
5. Ожидаемые правки человека.

Пишешь в `runs/<id>/skeleton.md` и ждёшь апрув человека.

## Проход 2: код теста

После апрува превращаешь утверждённый каркас в Kotlin-тест в
`doc/runs/<id>/test_generated/<ClassName>.kt`.

## Правила

- Стиль строго по `kb/patterns.md`: Screen-объекты, Kaspresso steps, Allure.
- Только реальные теги из `kb/screens/*.md`, без выдуманных строк.
- Исходники приложения не меняешь.
- Один тест — один сценарий из ТК.
```

`studio2/doc/agents/debugger.md`:
```markdown
---
role: debugger
display_name: Дебаггер
model: qwen3-coder:30b
temperature: 0.3
stage: debug
allowed_tools: [doc.read, rag.search, android.dump, android.screenshot, gradle.log, test.write]
inputs: [test_generated, logs]
outputs: [test_generated, sdd/agent_debugger.md]
max_rounds: 2
on_no_info: ask_human
---

## Роль

Ты — Дебаггер. Разбираешь не проходящий автотест и возвращаешь его в зелёное
состояние, не подменяя смысл проверки.

## Что делаешь

1. Читаешь лог Gradle, скриншот и UI-дамп на момент падения.
2. Классифицируешь причину: ошибка компиляции, тег не найден, таймаут или
   реальный баг продукта.
3. Компиляция, тег, таймаут: правишь тест, не более двух итераций.
4. Реальный баг продукта: `status: fail` с описанием. Тест не подкручиваешь.
5. Дописываешь примечания в `sdd/agent_debugger.md`.

## Формат ответа

```json
{"status": "done|fail", "artifacts": ["test_generated/X.kt"], "summary": "что исправлено", "question": null}
```
```

`studio2/doc/agents/reviewer.md`:
```markdown
---
role: reviewer
display_name: Ревьюер
model: gemma4:12b
temperature: 0.1
stage: review
allowed_tools: [doc.read, gradle.log, rag.search, review.write]
inputs: [test_generated, logs, tasks/<task_id>.md]
outputs: [review_report.md]
max_rounds: 1
on_no_info: ask_human
---

## Роль

Ты — Ревьюер и работаешь на отдельной модели. Проверяешь только загруженные
или полученные из MCP тесты: проходят ли они и зелёные ли они.

## Чек-лист

1. Тест проходит на устройстве, Gradle завершился с кодом 0: `green` или `red`.
2. Нет признаков flaky при повторном прогоне: `stable` или `flaky`.
3. Тест соответствует тест-кейсу из Allure, шаги покрыты: `matches` или `mismatch`.
4. Ссылка на тест-кейс в MCP существует и актуальна: `valid` или `stale`.

## Вердикт

`green` только если все тесты прогона прошли и стабильны. Иначе `red`.
Пишешь `runs/<id>/review_report.md` с таблицей тестов и причиной.

## Ограничения

- Не пишешь и не правишь тесты.
- Сгенерированные в этом же запуске тесты не блокируешь.
```

`studio2/doc/agents/device-inspector.md`:
```markdown
---
role: device-inspector
display_name: Разведчик экрана
model: qwen3.5:9b
temperature: 0.1
stage: study
allowed_tools: [android.dump, android.screenshot, android.launch, rag.write]
inputs: [sdd/agent_researcher.md]
outputs: [kb/screens]
max_rounds: 1
on_no_info: ask_human
---

## Роль

Ты — Разведчик экрана. Снимаешь реальную иерархию виджетов с эмулятора,
чтобы тесты использовали настоящие теги, а не выдуманные.

## Что делаешь

1. Запускаешь приложение на эмуляторе.
2. Делаешь UI-дамп через `uiautomator dump`.
3. Извлекаешь `resource-id`, `text`, `content-desc`, `class`, `clickable`.
4. Формируешь `kb/screens/<screen>.md`: название экрана, интерактивные узлы
   с тегами, дерево вложенности.

## Ограничения

- Тесты не пишешь, исходники приложения не меняешь.
```

- [ ] **Step 4: Write the parser**

`studio2/backend/roles.py`:
```python
"""Декларации агентов: парсинг front-matter из doc/agents/*.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_LIST_FIELDS = {"allowed_tools", "inputs", "outputs"}
_STR_FIELDS = {"display_name", "model", "stage", "on_no_info"}


@dataclass
class AgentSpec:
    role: str
    display_name: str = ""
    model: str = ""
    temperature: float = 0.2
    stage: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    max_rounds: int = 1
    on_no_info: str = "ask_human"
    body: str = ""
    path: str = ""


def _parse_inline_list(value: str) -> list[str]:
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [item.strip() for item in inner.split(",") if item.strip()]


def parse_agent(text: str, path: str = "") -> AgentSpec:
    if not text.startswith("---"):
        raise ValueError(f"нет front-matter в {path or 'агенте'}")
    _, _, rest = text.partition("---")
    meta_text, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError(f"front-matter не закрыт в {path or 'агенте'}")
    meta: dict[str, str] = {}
    for line in meta_text.strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    if not meta.get("role"):
        raise ValueError(f"не задано поле role в {path or 'агенте'}")
    kwargs: dict = {"role": meta["role"], "path": path, "body": body.strip()}
    for key in _STR_FIELDS:
        if key in meta:
            kwargs[key] = meta[key]
    if "temperature" in meta:
        kwargs["temperature"] = float(meta["temperature"])
    if "max_rounds" in meta:
        kwargs["max_rounds"] = int(meta["max_rounds"])
    for key in _LIST_FIELDS:
        if key in meta:
            kwargs[key] = _parse_inline_list(meta[key])
    return AgentSpec(**kwargs)


def load_agents(doc_dir: str) -> dict[str, AgentSpec]:
    agents: dict[str, AgentSpec] = {}
    for path in sorted((Path(doc_dir) / "agents").glob("*.md")):
        spec = parse_agent(path.read_text(encoding="utf-8"), str(path))
        if spec.role == "protocol":
            continue
        agents[spec.role] = spec
    return agents
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_roles.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add studio2/doc/agents studio2/backend/roles.py studio2/backend/tests/test_roles.py
git commit -m "feat(studio2): markdown agent declarations and role parser"
```

---

### Task 3: Templates, knowledge base seed, test case

**Files:**
- Create: `studio2/doc/templates/sdd.md`
- Create: `studio2/doc/templates/question.md`
- Create: `studio2/doc/templates/review-report.md`
- Create: `studio2/doc/templates/run-summary.md`
- Create: `studio2/doc/kb/architecture.md`
- Create: `studio2/doc/kb/patterns.md`
- Create: `studio2/doc/kb/principles.md`
- Create: `studio2/doc/kb/test-design.md`
- Create: `studio2/doc/kb/questions.md`
- Create: `studio2/doc/kb/screens/.gitkeep`
- Create: `studio2/doc/tasks/TC-4217.md`

**Interfaces:**
- Produces: documentation files consumed by `rag.py` (Task 4) and `pipeline/artifacts.py` (Task 9).

- [ ] **Step 1: Write the four templates**

`studio2/doc/templates/sdd.md`:
```markdown
# SDD — {role}

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

```json
{"status": "done|need_question|fail", "artifacts": [], "summary": "", "question": null}
```

## Критерии готовности

{criteria}
```

`studio2/doc/templates/question.md`:
```markdown
## Вопрос от {role}

**Контекст:** {context}

**Чего не хватает:** {missing}

**Что уже проверил:** {checked}

**Варианты:**

{options}

**Свободный ответ:** ...
```

`studio2/doc/templates/review-report.md`:
```markdown
# Отчёт ревью — {run_id}

| Тест | Статус | Длительность | Причина |
|---|---|---|---|
{rows}

**Вердикт:** {verdict}
```

`studio2/doc/templates/run-summary.md`:
```markdown
# Итог запуска {run_id}

- ТК: {task_id}
- Ветка: {branch}
- Стадии: {stages}
- Вердикт: {verdict}
- Артефакты: {artifacts}
```

- [ ] **Step 2: Write the knowledge base seed**

`studio2/doc/kb/architecture.md`:
```markdown
# Архитектура тестируемого проекта

## Назначение

Карта проекта для автотестов: модули, экраны, навигация, ключевые классы.
Файл ведёт агент-исследователь и дополняет человек.

## Модули

- `android/app` — точка входа Android-приложения, `namespace` и
  `applicationId` равны `ru.leroymerlin.mobile`.
- `android/presentation` — экраны и навигация.
- `common/*` — feature-модули: address, cart, catalog, checkout, profile,
  search, promo, loyalty и другие.
- `ios/*` — iOS-часть, в Android-пайплайне не тестируется.

## Тесты

- Android UI-тесты лежат в
  `android/app/src/androidTest/java/ru/leroymerlin/mobile/presentation/`.
- Стек: Kaspresso, Kakao, Compose UI Test, Espresso, Allure.
- Instrumentation runner: `io.qameta.allure.android.runners.AllureAndroidJUnitRunner`.
- Моки сетевых ответов: `androidTest/assets/responses/`.

## Навигация

Обновляется по мере изучения проекта.
```

`studio2/doc/kb/patterns.md`:
```markdown
# Паттерны автотестов проекта

## Screen-объекты

Экраны описываются классами `ComposeScreen<...>` из Kakao в пакете
`presentation/main/screens/`. Пример: `AddNewAddressScreen` с полями
`toolbarTitleText`, `acceptButton`, вложенным классом `TextField`.

## Матчеры

- `withTestTag(Constants.X, true)` — поиск по test-tag.
- `tagged(Constants.X)` — короткая форма того же.
- `withText("...", exactly = true, unmergedTree = true)` — по видимому тексту.

## Константы тегов

Теги объявлены в объектах `*UIConstants` соответствующих модулей, например
`AddressAddUIConstants.AcceptButton` или `AddressSearchUIConstants.TextField`.
Тесты ссылаются на константы, а не на строковые литералы.

## Шаги и отчёты

Kaspresso: `run { step("...") { ... } }`. Классы тестов помечаются
Allure-аннотациями, шаги попадают в отчёт.

## Фикстуры

JSON-моки в `androidTest/assets/responses/`, тестовые данные в
`presentation/main/data/`.
```

`studio2/doc/kb/principles.md`:
```markdown
# Принципы проверок

1. Один тест — один пользовательский сценарий из тест-кейса.
2. Проверяем видимый пользователю результат, а не внутреннее состояние.
3. Позитивный сценарий обязателен; негативный — только если он есть в ТК.
4. Никаких фиксированных `sleep`, используем ожидания фреймворка.
5. Тест не зависит от порядка запуска других тестов.
6. Каждый шаг теста соответствует шагу ТК, лишние проверки не добавляем.
7. Если функционала в сборке нет, тест не «подкручиваем», а сообщаем о баге.
```

`studio2/doc/kb/test-design.md`:
```markdown
# Дизайн тестов

## Уровни

- UI-тест (Espresso, Kaspresso, Compose UI Test) — пользовательский сценарий.
- Unit-тест — логика без UI; вне scope QA-пайплайна, если ТК не требует иного.

## Структура UI-теста

1. Подготовка: вход в нужное состояние экрана.
2. Действия: тапы, ввод текста, скролл.
3. Проверки: assert по тегам и видимым текстам.
4. Уборка: возврат на главный экран.

## Именование

Класс `<Screen><Scenario>Test`, метод — `camelCase` с описанием сценария.
```

`studio2/doc/kb/questions.md`:
```markdown
# Открытые вопросы и ответы

Формат записи: вопрос, ответ, дата. Файл заполняется агентом при нехватке
информации и человеком после ответа.
```

- [ ] **Step 3: Write the seeded test case**

`studio2/doc/tasks/TC-4217.md`:
```markdown
---
id: TC-4217
title: Добавление нового адреса доставки
screen: AddNewAddress
priority: high
status: ready
---

# TC-4217 — Добавление нового адреса доставки

## Предусловия

- Пользователь авторизован.
- Открыт экран выбора адреса.

## Шаги

1. Нажать «Добавить адрес».
2. Проверить, что открылся экран «Добавить адрес» (заголовок).
3. Ввести улицу и дом.
4. Проверить, что кнопка «Принять» активна.
5. Нажать «Принять».

## Ожидаемый результат

Открылся главный экран, новый адрес присутствует в списке.

## Теги

- `AddressAddUIConstants.ToolbarTitleText`
- `AddressSearchUIConstants.TextField`
- `AddressAddUIConstants.AcceptButton`
```

- [ ] **Step 4: Verify files are valid UTF-8**

Run (from repo root): `python -c "from pathlib import Path; files=list(Path('studio2/doc').rglob('*.md')); [p.read_text(encoding='utf-8') for p in files]; print(len(files))"`
Expected: prints a number `>= 16`

- [ ] **Step 5: Commit**

```bash
git add studio2/doc
git commit -m "docs(studio2): templates, knowledge base seed and TC-4217"
```

---

### Task 4: RAG index (BM25-lite) and document store

**Files:**
- Create: `studio2/backend/rag.py`
- Create: `studio2/backend/docstore.py`
- Create: `studio2/backend/tests/test_rag.py`
- Create: `studio2/backend/tests/test_docstore.py`

**Interfaces:**
- Produces `RagIndex(doc_dir)`: `.reindex() -> int`, `.search(query, limit=5) -> list[{"path","score","snippet"}]`, `.stats() -> {"documents","updated_at"}`, `.stale(rel_or_abs) -> bool`.
- Produces `DocStore(doc_dir)`: `.tree()`, `.read(rel)`, `.write(rel, content)`, `.upload(rel_dir, filename, data)`, `.delete(rel)`; raises `DocPathError`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_rag.py`:
```python
import shutil
from pathlib import Path

import pytest

from rag import RagIndex

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def doc_dir(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    return tmp_path / "doc"


def test_reindex_counts_markdown_files(doc_dir):
    assert RagIndex(str(doc_dir)).reindex() >= 10


def test_search_finds_task(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    hits = index.search("Добавление адреса доставки", limit=3)
    assert hits
    assert any("TC-4217" in hit["path"] for hit in hits)


def test_search_finds_patterns(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    hits = index.search("withTestTag", limit=5)
    assert any("patterns" in hit["path"] for hit in hits)


def test_snippet_is_not_empty(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    assert index.search("AcceptButton", limit=3)[0]["snippet"]


def test_reindex_clears_stale_flag(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    target = doc_dir / "kb" / "principles.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nновый факт\n", encoding="utf-8")
    assert index.stale(str(target)) is True
    index.reindex()
    assert index.stale(str(target)) is False


def test_stats_reports_documents(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    stats = index.stats()
    assert stats["documents"] >= 10
    assert stats["updated_at"]


def test_empty_query_returns_nothing(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    assert index.search("", limit=3) == []
```

`studio2/backend/tests/test_docstore.py`:
```python
import shutil
from pathlib import Path

import pytest

from docstore import DocPathError, DocStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def store(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    return DocStore(str(tmp_path / "doc"))


def test_tree_lists_roots(store):
    names = {node["name"] for node in store.tree()}
    assert {"kb", "tasks", "agents", "templates"} <= names


def test_read_known_file(store):
    assert "TC-4217" in store.read("tasks/TC-4217.md")


def test_write_and_read_back(store):
    store.write("kb/notes.md", "# Заметки\n")
    assert store.read("kb/notes.md") == "# Заметки\n"


def test_write_rejects_traversal(store):
    with pytest.raises(DocPathError):
        store.write("../escape.md", "x")


def test_write_rejects_runs_dir(store):
    with pytest.raises(DocPathError):
        store.write("runs/1/test.md", "x")


def test_upload_creates_file(store):
    rel = store.upload("kb", "screen.md", "# Экран\n".encode("utf-8"))
    assert rel == "kb/screen.md"
    assert store.read(rel) == "# Экран\n"


def test_delete_allowed_for_kb(store):
    store.write("kb/tmp.md", "x")
    store.delete("kb/tmp.md")
    with pytest.raises(DocPathError):
        store.read("kb/tmp.md")


def test_delete_rejects_agents(store):
    with pytest.raises(DocPathError):
        store.delete("agents/reviewer.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rag.py tests/test_docstore.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rag'`

- [ ] **Step 3: Write the RAG index**

`studio2/backend/rag.py`:
```python
"""BM25-lite индекс по Markdown-документам doc/kb, doc/tasks, doc/agents."""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

ROOTS = ("kb", "tasks", "agents")
_TOKEN_RE = re.compile(r"[\w\-\.]+", re.UNICODE)
STOP_WORDS = {"и", "в", "на", "с", "по", "для", "не", "что", "как", "из", "или"}


def _tokenize(text: str) -> list[str]:
    tokens = (t.lower() for t in _TOKEN_RE.findall(text))
    return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]


class RagIndex:
    def __init__(self, doc_dir: str):
        self.doc_dir = Path(doc_dir)
        self._docs: dict[str, dict] = {}
        self._df: dict[str, int] = {}
        self._avg_len = 0.0
        self._updated_at = ""

    def _files(self) -> list[Path]:
        paths: list[Path] = []
        for root in ROOTS:
            paths.extend((self.doc_dir / root).rglob("*.md"))
        return sorted(paths)

    def reindex(self) -> int:
        self._docs.clear()
        self._df.clear()
        total_len = 0
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            tokens = _tokenize(text)
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            rel = path.relative_to(self.doc_dir).as_posix()
            self._docs[rel] = {"tf": tf, "len": len(tokens), "text": text,
                               "mtime": path.stat().st_mtime}
            for token in tf:
                self._df[token] = self._df.get(token, 0) + 1
            total_len += len(tokens)
        self._avg_len = (total_len / len(self._docs)) if self._docs else 0.0
        self._updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return len(self._docs)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        tokens = _tokenize(query)
        if not tokens or not self._docs:
            return []
        n = len(self._docs)
        k1, b = 1.5, 0.75
        scored: list[tuple[float, str]] = []
        for rel, doc in self._docs.items():
            score = 0.0
            for token in tokens:
                df = self._df.get(token, 0)
                tf = doc["tf"].get(token, 0)
                if not df or not tf:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = 1 - b + b * (doc["len"] / (self._avg_len or 1))
                score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
            if score > 0:
                scored.append((score, rel))
        scored.sort(reverse=True)
        return [
            {"path": rel, "score": round(score, 3),
             "snippet": self._snippet(self._docs[rel]["text"], tokens)}
            for score, rel in scored[:limit]
        ]

    @staticmethod
    def _snippet(text: str, tokens: list[str], width: int = 180) -> str:
        lowered = text.lower()
        pos = -1
        for token in tokens:
            pos = lowered.find(token)
            if pos >= 0:
                break
        if pos < 0:
            return text[:width].replace("\n", " ")
        start = max(0, pos - width // 3)
        return text[start:start + width].replace("\n", " ").strip()

    def stats(self) -> dict:
        return {"documents": len(self._docs), "updated_at": self._updated_at}

    def stale(self, rel_or_abs: str) -> bool:
        path = Path(rel_or_abs)
        if path.is_absolute():
            try:
                rel = path.relative_to(self.doc_dir).as_posix()
            except ValueError:
                return True
        else:
            rel = path.as_posix()
        doc = self._docs.get(rel)
        if doc is None:
            return True
        full = self.doc_dir / rel
        return (not full.exists()) or full.stat().st_mtime > doc["mtime"]
```

- [ ] **Step 4: Write the document store**

`studio2/backend/docstore.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_rag.py tests/test_docstore.py -v`
Expected: 15 passed

- [ ] **Step 6: Commit**

```bash
git add studio2/backend/rag.py studio2/backend/docstore.py studio2/backend/tests/test_rag.py studio2/backend/tests/test_docstore.py
git commit -m "feat(studio2): BM25-lite RAG index and document store"
```

---

### Task 5: Git adapter

**Files:**
- Create: `studio2/backend/git_ops.py`
- Create: `studio2/backend/tests/test_git_ops.py`

**Interfaces:**
- Produces `GitRepo(path)`: `.validate()`, `.current_branch()`, `.branches()`, `.log(limit=20, branch=None)`, `.diff(branch=None, base="develop")`, `.blame(rel_path, lines=20)`, `.recent_changes(limit=20)`.
- Raises `GitError` for any git failure.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_git_ops.py`:
```python
import subprocess

import pytest

from git_ops import GitError, GitRepo


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _init_repo(path):
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "T")
    (path / "a.txt").write_text("one", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "first")


def test_validate_rejects_plain_dir(tmp_path):
    with pytest.raises(GitError):
        GitRepo(str(tmp_path)).validate()


def test_current_branch(tmp_path):
    _init_repo(tmp_path)
    assert GitRepo(str(tmp_path)).current_branch() == "main"


def test_log_returns_commits(tmp_path):
    _init_repo(tmp_path)
    commits = GitRepo(str(tmp_path)).log(limit=5)
    assert commits[0]["message"] == "first"
    assert len(commits[0]["hash"]) >= 7


def test_branches_list_main(tmp_path):
    _init_repo(tmp_path)
    assert "main" in GitRepo(str(tmp_path)).branches()


def test_diff_reports_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("two", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "second")
    diff = GitRepo(str(tmp_path)).diff(base="HEAD~1")
    assert "-one" in diff and "+two" in diff


def test_recent_changes_is_text(tmp_path):
    _init_repo(tmp_path)
    text = GitRepo(str(tmp_path)).recent_changes(limit=3)
    assert "first" in text
    assert "## Коммиты" in text


def test_blame_returns_text(tmp_path):
    _init_repo(tmp_path)
    assert "first" in GitRepo(str(tmp_path)).blame("a.txt", lines=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_git_ops.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'git_ops'`

- [ ] **Step 3: Write the implementation**

`studio2/backend/git_ops.py`:
```python
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
            return self._run("diff", "HEAD~1", target)

    def blame(self, rel_path: str, lines: int = 20) -> str:
        return self._run("blame", "--date=short", "-L", f"1,{lines}", rel_path)

    def recent_changes(self, limit: int = 20) -> str:
        commits = self.log(limit=limit)
        head = "\n".join(
            f"- {c['hash']} {c['message']} ({c['author']}, {c['date']})" for c in commits
        )
        return f"## Коммиты\n{head}\n\n## Diff\n{self.diff()[:6000]}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_git_ops.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/git_ops.py studio2/backend/tests/test_git_ops.py
git commit -m "feat(studio2): git adapter for branch and change study"
```

---

### Task 6: LLM client with fake, ollama and gpustack providers

**Files:**
- Create: `studio2/backend/llm.py`
- Create: `studio2/backend/tests/test_llm.py`

**Interfaces:**
- Produces `LlmError`, `FakeProvider.models()`, `LlmClient(config, transport=None)` with `.chat(role, messages, system=None) -> str`, `.stream(role, messages, system=None) -> Iterator[str]`, `.list_models(provider_name) -> list[str]`, `.test_model(role) -> dict`, `.log_dir: Path | None`.
- Ollama and GPUSTACK both use OpenAI-compatible `POST {base}/v1/chat/completions` and `GET {base}/v1/models`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_llm.py`:
```python
import json

import httpx
import pytest

from config import DEFAULT_CONFIG
from llm import FakeProvider, LlmClient, LlmError


def _cfg():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    return cfg


def test_fake_chat_is_deterministic():
    client = LlmClient(_cfg())
    first = client.chat("researcher", [{"role": "user", "content": "изучи экран"}])
    second = client.chat("researcher", [{"role": "user", "content": "изучи экран"}])
    assert first == second
    assert json.loads(first)["status"] == "done"


def test_stream_yields_chunks():
    chunks = list(LlmClient(_cfg()).stream("reviewer", [{"role": "user", "content": "x"}]))
    assert len(chunks) > 1
    assert json.loads("".join(chunks))["status"] == "done"


def test_ollama_uses_openai_path_and_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "привет"}}]})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "qwen3.5:9b", "temperature": 0.2}
    client = LlmClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("researcher", [{"role": "user", "content": "hi"}]) == "привет"
    assert seen["url"] == "http://localhost:11435/v1/chat/completions"
    assert seen["model"] == "qwen3.5:9b"


def test_gpustack_sends_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    cfg = _cfg()
    cfg["providers"]["gpustack"]["api_key"] = "secret-key"
    cfg["roles"]["researcher"] = {"provider": "gpustack", "model": "deepseek", "temperature": 0.2}
    client = LlmClient(cfg, transport=httpx.MockTransport(handler))
    assert client.chat("researcher", [{"role": "user", "content": "hi"}]) == "ok"
    assert seen["auth"] == "Bearer secret-key"


def test_http_error_raises_llm_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    cfg = _cfg()
    cfg["roles"]["researcher"] = {"provider": "ollama", "model": "m", "temperature": 0.2}
    with pytest.raises(LlmError):
        LlmClient(cfg, transport=httpx.MockTransport(handler)).chat(
            "researcher", [{"role": "user", "content": "hi"}]
        )


def test_list_models_for_fake():
    assert FakeProvider.models() == ["fake-1"]


def test_list_models_from_ollama():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "qwen3-coder:30b"}, {"id": "gemma4:12b"}]})

    client = LlmClient(_cfg(), transport=httpx.MockTransport(handler))
    assert client.list_models("ollama") == ["qwen3-coder:30b", "gemma4:12b"]


def test_test_model_reports_error_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    cfg = _cfg()
    cfg["roles"]["reviewer"]["provider"] = "ollama"
    result = LlmClient(cfg, transport=httpx.MockTransport(handler)).test_model("reviewer")
    assert result["ok"] is False
    assert result["provider"] == "ollama"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm'`

- [ ] **Step 3: Write the implementation**

`studio2/backend/llm.py`:
```python
"""LLM-клиент: fake (детерминированный), ollama и gpustack (OpenAI-совместимые)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterator

import httpx

from config import resolve_provider

FAKE_RESPONSES = {
    "qa-lead": '{"status": "done", "artifacts": [], "summary": "SDD готовы", "question": null}',
    "researcher": '{"status": "done", "artifacts": ["kb/architecture.md", "kb/patterns.md"], "summary": "Проект изучен", "question": null}',
    "testwriter": '{"status": "done", "artifacts": ["test_generated/AddNewAddressTest.kt"], "summary": "Тест написан", "question": null}',
    "debugger": '{"status": "done", "artifacts": [], "summary": "Тест исправлен", "question": null}',
    "reviewer": '{"status": "done", "artifacts": ["review_report.md"], "summary": "green", "question": null}',
    "device-inspector": '{"status": "done", "artifacts": ["kb/screens/add-new-address.md"], "summary": "Экран снят", "question": null}',
}
DEFAULT_FAKE = '{"status": "done", "artifacts": [], "summary": "ok", "question": null}'


class LlmError(RuntimeError):
    pass


class FakeProvider:
    @staticmethod
    def models() -> list[str]:
        return ["fake-1"]

    @staticmethod
    def response_for(role: str) -> str:
        return FAKE_RESPONSES.get(role, DEFAULT_FAKE)


class LlmClient:
    def __init__(self, config: dict, transport: httpx.BaseTransport | None = None):
        self.config = config
        self._transport = transport
        self.log_dir: Path | None = None
        self._seq = 0

    def _provider(self, role: str) -> dict:
        return resolve_provider(self.config, role)

    def chat(self, role: str, messages: list[dict], system: str | None = None) -> str:
        provider = self._provider(role)
        if provider["name"] == "fake":
            return FakeProvider.response_for(role)
        return self._http_chat(provider, role, messages, system)

    def stream(self, role: str, messages: list[dict],
               system: str | None = None) -> Iterator[str]:
        provider = self._provider(role)
        if provider["name"] == "fake":
            text = FakeProvider.response_for(role)
        else:
            text = self._http_chat(provider, role, messages, system)
        for i in range(0, len(text), 24):
            yield text[i:i + 24]

    def _http_chat(self, provider: dict, role: str, messages: list[dict],
                   system: str | None) -> str:
        base = (provider.get("base_url") or "").rstrip("/")
        if not base:
            raise LlmError(f"для провайдера {provider['name']} не задан base_url")
        payload = {
            "model": provider["model"],
            "temperature": provider.get("temperature", 0.2),
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        }
        headers = {}
        if provider.get("api_key"):
            headers["Authorization"] = f"Bearer {provider['api_key']}"
        last_error: Exception | None = None
        with httpx.Client(transport=self._transport, timeout=180.0) as client:
            for _ in range(2):
                try:
                    response = client.post(f"{base}/v1/chat/completions",
                                           json=payload, headers=headers)
                    if response.status_code >= 400:
                        raise LlmError(
                            f"LLM вернула {response.status_code}: {response.text[:300]}"
                        )
                    content = response.json()["choices"][0]["message"]["content"]
                    self._log(role, payload, content)
                    return content
                except (httpx.HTTPError, KeyError, ValueError, LlmError) as exc:
                    last_error = exc
                    if isinstance(exc, LlmError):
                        break
        raise LlmError(f"LLM недоступна: {last_error}")

    def _log(self, role: str, payload: dict, content: str) -> None:
        if not self.log_dir:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._seq += 1
        safe = {k: v for k, v in payload.items() if k != "messages"}
        safe.update(role=role, at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    response=content[:2000])
        (self.log_dir / f"llm_{role}_{self._seq}.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _provider_config(self, provider_name: str) -> dict:
        provider = self.config["providers"].get(provider_name) or {}
        base = (provider.get("base_url") or "").rstrip("/")
        if not base:
            raise LlmError(f"провайдер {provider_name} не настроен")
        headers = {}
        if provider.get("api_key"):
            headers["Authorization"] = f"Bearer {provider['api_key']}"
        return {"base": base, "headers": headers}

    def list_models(self, provider_name: str) -> list[str]:
        if provider_name == "fake":
            return FakeProvider.models()
        meta = self._provider_config(provider_name)
        with httpx.Client(transport=self._transport, timeout=20.0) as client:
            try:
                response = client.get(f"{meta['base']}/v1/models", headers=meta["headers"])
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise LlmError(
                    f"не удалось получить модели {provider_name}: {exc}"
                ) from exc
        return [item["id"] for item in data.get("data", []) if item.get("id")]

    def test_model(self, role: str) -> dict:
        provider = self._provider(role)
        try:
            reply = self.chat(role, [{"role": "user", "content": "ping"}])
        except LlmError as exc:
            return {"ok": False, "provider": provider["name"],
                    "model": provider["model"], "error": str(exc)}
        return {"ok": True, "provider": provider["name"],
                "model": provider["model"], "reply": reply[:80]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/llm.py studio2/backend/tests/test_llm.py
git commit -m "feat(studio2): LLM client with fake, ollama and gpustack providers"
```

---

### Task 7: Android adb adapter and Gradle runner

**Files:**
- Create: `studio2/backend/android.py`
- Create: `studio2/backend/gradle.py`
- Create: `studio2/backend/tests/test_android.py`
- Create: `studio2/backend/tests/test_gradle.py`

**Interfaces:**
- Produces `AdbError`, `AndroidDevice(adb_path, serial=None)` with `.list_devices()`, `.mode()`, `.boot(emulator_path, avd)`, `.wait_ready(timeout=180)`, `.install(apk)`, `.launch(package)`, `.dump_ui()`, `.screenshot(out_path)`, `.tap(x, y)`, `.type_text(text)`, `.back()`, `.current_activity()`.
- Produces `parse_uiautomator_xml(xml_text) -> dict`, `ui_tree_to_flat(tree) -> list[dict]`, `screen_markdown(tree, screen_name) -> str`.
- Produces `GradleError`, `parse_build_result(log) -> {"success","failures","duration"}`, `GradleRunner(repo_path, java_home=None, test_module=":android:app")` with `.mode()`, `.build_command(*tasks)`, `.connected_test_command(class_name)`, `.assemble_debug(log_path)`, `.connected_test(class_name, log_path)`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_android.py`:
```python
import pytest

from android import AdbError, parse_uiautomator_xml, screen_markdown, ui_tree_to_flat

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" clickable="false" bounds="[0,0][1080,1920]">
    <node index="0" text="Добавить адрес" resource-id="toolbar_title" class="android.widget.TextView" clickable="false" bounds="[40,100][700,180]"/>
    <node index="1" text="" resource-id="street_input" class="android.widget.EditText" clickable="true" content-desc="Улица" bounds="[40,300][1040,420]"/>
    <node index="2" text="Принять" resource-id="accept_button" class="android.widget.Button" clickable="true" bounds="[40,1600][1040,1700]"/>
  </node>
</hierarchy>"""


def test_parse_builds_tree():
    tree = parse_uiautomator_xml(SAMPLE_XML)
    assert tree["class"] == "android.widget.FrameLayout"
    assert len(tree["children"]) == 3


def test_flat_extracts_ids_and_text():
    flat = ui_tree_to_flat(parse_uiautomator_xml(SAMPLE_XML))
    ids = [node["resource_id"] for node in flat if node["resource_id"]]
    assert "accept_button" in ids
    assert any(node["text"] == "Принять" for node in flat)


def test_screen_markdown_lists_tags():
    md = screen_markdown(parse_uiautomator_xml(SAMPLE_XML), "AddNewAddress")
    assert md.startswith("# Экран AddNewAddress")
    assert "accept_button" in md
    assert "street_input" in md
    assert "Принять" in md


def test_invalid_xml_raises():
    with pytest.raises(AdbError):
        parse_uiautomator_xml("<not-xml")
```

`studio2/backend/tests/test_gradle.py`:
```python
import pytest

from gradle import GradleError, GradleRunner, parse_build_result


def test_parse_build_successful():
    result = parse_build_result("...\nBUILD SUCCESSFUL in 42s\n")
    assert result["success"] is True
    assert result["failures"] == []
    assert result["duration"] == "42s"


def test_parse_build_failed_lists_tests():
    log = """
    com.example.AddNewAddressTest > addAddress FAILED
        java.lang.AssertionError: expected true
    BUILD FAILED in 1m
    """
    result = parse_build_result(log)
    assert result["success"] is False
    assert result["failures"] == [
        {"class": "com.example.AddNewAddressTest", "test": "AddNewAddressTest",
         "method": "addAddress"}
    ]


def test_missing_gradlew_raises(tmp_path):
    with pytest.raises(GradleError):
        GradleRunner(str(tmp_path)).build_command("assembleDebug")


def test_mode_is_fake_without_gradlew(tmp_path):
    assert GradleRunner(str(tmp_path)).mode() == "fake"


def test_mode_is_live_with_gradlew(tmp_path):
    (tmp_path / "gradlew.bat").write_text("", encoding="utf-8")
    assert GradleRunner(str(tmp_path)).mode() == "live"


def test_connected_test_command_includes_filter(tmp_path):
    (tmp_path / "gradlew.bat").write_text("", encoding="utf-8")
    cmd = GradleRunner(str(tmp_path)).connected_test_command(
        "ru.leroymerlin.mobile.AddNewAddressTest"
    )
    joined = " ".join(cmd)
    assert ":android:app:connectedDebugAndroidTest" in joined
    assert "-Pandroid.testInstrumentationRunnerArguments.class=ru.leroymerlin.mobile.AddNewAddressTest" in joined
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_android.py tests/test_gradle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'android'`

- [ ] **Step 3: Write the Android adapter**

`studio2/backend/android.py`:
```python
"""adb-адаптер: устройства, UI-дамп, скриншоты, запуск приложения."""
from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


class AdbError(RuntimeError):
    pass


class AndroidDevice:
    def __init__(self, adb_path: str, serial: str | None = None):
        self.adb_path = adb_path
        self.serial = serial

    def _run(self, *args: str, timeout: int = 60) -> str:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"adb недоступен: {exc}") from exc
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or f"adb {args[0]} завершился с ошибкой")
        return result.stdout

    def list_devices(self) -> list[dict]:
        out = self._run("devices", "-l")
        devices = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices

    def mode(self) -> str:
        return "live" if any(d["state"] == "device" for d in self.list_devices()) else "adb-only"

    def boot(self, emulator_path: str, avd: str) -> None:
        subprocess.Popen([emulator_path, "-avd", avd, "-no-snapshot-save"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self, timeout: int = 180) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._run("shell", "getprop", "sys.boot_completed").strip() == "1":
                    return True
            except AdbError:
                pass
            time.sleep(3)
        return False

    def install(self, apk: str) -> None:
        self._run("install", "-r", "-t", str(apk), timeout=300)

    def launch(self, package: str) -> None:
        self._run("shell", "monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")

    def dump_ui(self) -> dict:
        self._run("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        return parse_uiautomator_xml(self._run("shell", "cat", "/sdcard/ui.xml"))

    def screenshot(self, out_path: str) -> Path:
        self._run("shell", "screencap", "-p", "/sdcard/screen.png")
        cmd = [self.adb_path, *(["-s", self.serial] if self.serial else []),
               "exec-out", "cat", "/sdcard/screen.png"]
        data = subprocess.run(cmd, capture_output=True, timeout=60)
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.stdout)
        return path

    def tap(self, x: int, y: int) -> None:
        self._run("shell", "input", "tap", str(x), str(y))

    def type_text(self, text: str) -> None:
        self._run("shell", "input", "text", text.replace(" ", "%s"))

    def back(self) -> None:
        self._run("shell", "input", "keyevent", "4")

    def current_activity(self) -> str:
        out = self._run("shell", "dumpsys", "window", "windows")
        for line in out.splitlines():
            if "mCurrentFocus" in line:
                return line.strip()
        return ""


def parse_uiautomator_xml(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise AdbError(f"не разобрать UI-дамп: {exc}") from exc

    def walk(node) -> dict:
        return {
            "class": node.get("class", ""),
            "text": node.get("text", ""),
            "resource_id": node.get("resource-id", ""),
            "content_desc": node.get("content-desc", ""),
            "clickable": node.get("clickable", "false") == "true",
            "bounds": node.get("bounds", ""),
            "children": [walk(child) for child in list(node)],
        }

    return walk(root)


def ui_tree_to_flat(tree: dict) -> list[dict]:
    flat, stack = [], [tree]
    while stack:
        node = stack.pop()
        flat.append({k: node[k] for k in
                     ("class", "text", "resource_id", "content_desc", "clickable", "bounds")})
        stack.extend(reversed(node["children"]))
    return flat


def screen_markdown(tree: dict, screen_name: str) -> str:
    flat = ui_tree_to_flat(tree)
    lines = [f"# Экран {screen_name}", "", "## Интерактивные узлы", ""]
    for node in flat:
        if not (node["clickable"] or node["resource_id"] or node["text"]):
            continue
        tag = node["resource_id"] or "—"
        label = node["text"] or node["content_desc"] or "—"
        lines.append(f"- `{tag}` — «{label}» ({node['class']}) {node['bounds']}")
    lines += ["", "## Дерево", ""]

    def walk_children(node: dict, depth: int) -> None:
        lines.append("  " * depth + f"{node['class']} {node['resource_id']}".strip())
        for child in node["children"]:
            walk_children(child, depth + 1)

    walk_children(tree, 0)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Write the Gradle runner**

`studio2/backend/gradle.py`:
```python
"""Запуск Gradle-задач тестируемого Android-проекта."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FAIL_RE = re.compile(r"^\s*([\w\.]+) > ([\w\[\]\-]+) FAILED", re.MULTILINE)


class GradleError(RuntimeError):
    pass


def parse_build_result(log: str) -> dict:
    failures = [
        {"class": m.group(1), "test": m.group(1).split(".")[-1], "method": m.group(2)}
        for m in FAIL_RE.finditer(log)
    ]
    match = re.search(r"BUILD (?:SUCCESSFUL|FAILED) in ([\w ]+)", log)
    return {
        "success": "BUILD SUCCESSFUL" in log and not failures,
        "failures": failures,
        "duration": match.group(1).strip() if match else "",
    }


class GradleRunner:
    def __init__(self, repo_path: str, java_home: str | None = None,
                 test_module: str = ":android:app"):
        self.repo_path = Path(repo_path)
        self.java_home = java_home or os.getenv("JAVA_HOME")
        self.test_module = test_module

    def _gradlew(self) -> Path:
        for name in ("gradlew.bat", "gradlew"):
            candidate = self.repo_path / name
            if candidate.exists():
                return candidate
        raise GradleError(f"в {self.repo_path} нет gradlew")

    def mode(self) -> str:
        try:
            self._gradlew()
        except GradleError:
            return "fake"
        return "live"

    def build_command(self, *tasks: str) -> list[str]:
        return [str(self._gradlew()), *tasks]

    def connected_test_command(self, class_name: str) -> list[str]:
        return [
            str(self._gradlew()),
            f"{self.test_module}:connectedDebugAndroidTest",
            f"-Pandroid.testInstrumentationRunnerArguments.class={class_name}",
        ]

    def _run(self, args: list[str], log_path: Path, timeout: int) -> dict:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        if self.java_home:
            env["JAVA_HOME"] = self.java_home
        try:
            result = subprocess.run(args, cwd=str(self.repo_path), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GradleError(f"Gradle не запустился: {exc}") from exc
        log = (result.stdout or "") + "\n" + (result.stderr or "")
        log_path.write_text(log, encoding="utf-8")
        parsed = parse_build_result(log)
        parsed.update(returncode=result.returncode, log_path=str(log_path))
        return parsed

    def assemble_debug(self, log_path: Path) -> dict:
        return self._run([str(self._gradlew()), f"{self.test_module}:assembleDebug"],
                         log_path, timeout=1500)

    def connected_test(self, class_name: str, log_path: Path) -> dict:
        return self._run(self.connected_test_command(class_name), log_path, timeout=900)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_android.py tests/test_gradle.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add studio2/backend/android.py studio2/backend/gradle.py studio2/backend/tests/test_android.py studio2/backend/tests/test_gradle.py
git commit -m "feat(studio2): adb adapter and gradle runner"
```

---

### Task 8: MCP client and registry

**Files:**
- Create: `studio2/backend/mcp.py`
- Create: `studio2/backend/tests/test_mcp.py`

**Interfaces:**
- Produces `McpError`, `McpServerStore(data_dir)` with `.list()`, `.add(spec)`, `.delete(server_id)`, `.get(server_id)`; spec fields: `id`, `name`, `type` (`stdio`|`http`), `command` (list), `url`, `env`, `enabled`.
- Produces `McpRegistry(store)` with `.status(server_id)`, `.connect(server_id)`, `.disconnect(server_id)`, `.tools()`, `.call(server_id, tool, arguments)`, `.close_all()`.
- stdio transport: JSON-RPC 2.0 over pipes, `initialize` → `tools/list` → `tools/call`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_mcp.py`:
```python
import sys
import textwrap

import pytest

from mcp import McpError, McpRegistry, McpServerStore

SERVER = textwrap.dedent(
    """
    import json, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "ping", "description": "ping tool",
                 "inputSchema": {"type": "object", "properties": {}}}]}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "pong"}]}})
        elif method == "notifications/initialized":
            continue
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id", 0),
                  "error": {"code": -32601, "message": "unknown"}})
    """
)


@pytest.fixture()
def test_server(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER, encoding="utf-8")
    return str(path)


def test_store_add_and_list(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "allure", "type": "http", "url": "https://allure/api"})
    assert server["id"]
    assert [s["name"] for s in store.list()] == ["allure"]
    assert store.get(server["id"])["url"] == "https://allure/api"


def test_store_delete(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "x", "type": "http", "url": "http://x"})
    store.delete(server["id"])
    assert store.list() == []
    with pytest.raises(McpError):
        store.get(server["id"])


def test_add_rejects_unknown_type(tmp_path):
    with pytest.raises(McpError):
        McpServerStore(str(tmp_path)).add({"name": "x", "type": "carrier-pigeon"})


def test_stdio_connect_lists_tools(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    result = registry.connect(server["id"])
    assert [tool["name"] for tool in result["tools"]] == ["ping"]
    assert registry.status(server["id"])["state"] == "connected"
    registry.disconnect(server["id"])


def test_stdio_call_tool(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    registry.connect(server["id"])
    result = registry.call(server["id"], "ping", {})
    assert result["content"][0]["text"] == "pong"
    registry.disconnect(server["id"])


def test_call_without_connect_raises(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio", "command": [sys.executable]})
    with pytest.raises(McpError):
        McpRegistry(store).call(server["id"], "ping", {})


def test_status_idle_before_connect(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio", "command": [sys.executable]})
    assert McpRegistry(store).status(server["id"])["state"] == "idle"


def test_tools_aggregates_connected_servers(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    registry.connect(server["id"])
    tools = registry.tools()
    assert tools and tools[0]["server_id"] == server["id"]
    registry.disconnect(server["id"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp'`

- [ ] **Step 3: Write the implementation**

`studio2/backend/mcp.py`:
```python
"""MCP-клиент (stdio JSON-RPC 2.0) и реестр серверов."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"


class McpError(RuntimeError):
    pass


class McpServerStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "mcp_servers.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []

    def _save(self, servers: list[dict]) -> None:
        self.path.write_text(json.dumps(servers, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def list(self) -> list[dict]:
        return self._load()

    def get(self, server_id: str) -> dict:
        for server in self._load():
            if server["id"] == server_id:
                return server
        raise McpError(f"сервер {server_id} не найден")

    def add(self, spec: dict) -> dict:
        server_type = spec.get("type")
        if server_type not in {"stdio", "http"}:
            raise McpError("тип сервера должен быть stdio или http")
        if server_type == "stdio" and not spec.get("command"):
            raise McpError("для stdio-сервера нужна команда")
        if server_type == "http" and not spec.get("url"):
            raise McpError("для http-сервера нужен url")
        server = {
            "id": uuid.uuid4().hex[:8],
            "name": spec.get("name") or "mcp",
            "type": server_type,
            "command": spec.get("command") or [],
            "url": spec.get("url") or "",
            "env": spec.get("env") or {},
            "enabled": bool(spec.get("enabled", True)),
        }
        servers = self._load()
        servers.append(server)
        self._save(servers)
        return server

    def delete(self, server_id: str) -> None:
        servers = self._load()
        remaining = [s for s in servers if s["id"] != server_id]
        if len(remaining) == len(servers):
            raise McpError(f"сервер {server_id} не найден")
        self._save(remaining)


class _StdioSession:
    def __init__(self, command: list[str], env: dict):
        full_env = dict(os.environ)
        full_env.update({str(k): str(v) for k, v in (env or {}).items()})
        try:
            self.proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=full_env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise McpError(f"не удалось запустить MCP-сервер: {exc}") from exc
        self._next_id = 0

    def _write(self, payload: dict) -> None:
        if not self.proc.stdin:
            raise McpError("stdin MCP-сервера закрыт")
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method,
                     "params": params or {}})
        while True:
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            if not line:
                raise McpError("MCP-сервер закрыл соединение")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError(message["error"].get("message", "ошибка MCP"))
            return message.get("result", {})

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": {}})

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


class McpRegistry:
    def __init__(self, store: McpServerStore):
        self.store = store
        self._sessions: dict[str, _StdioSession] = {}
        self._tools: dict[str, list[dict]] = {}
        self._errors: dict[str, str] = {}

    def status(self, server_id: str) -> dict:
        if server_id in self._sessions:
            return {"state": "connected", "tools_count": len(self._tools.get(server_id, []))}
        if server_id in self._errors:
            return {"state": "error", "error": self._errors[server_id]}
        return {"state": "idle", "tools_count": 0}

    def connect(self, server_id: str) -> dict:
        server = self.store.get(server_id)
        if server["type"] != "stdio":
            raise McpError("поддержан только stdio-транспорт")
        self._errors.pop(server_id, None)
        session = _StdioSession(server["command"], server["env"])
        try:
            session.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "studio2", "version": "day35"},
            })
            session.notify("notifications/initialized")
            tools = session.request("tools/list").get("tools", [])
        except McpError as exc:
            session.close()
            self._errors[server_id] = str(exc)
            raise
        self._sessions[server_id] = session
        self._tools[server_id] = tools
        return {"tools": tools}

    def disconnect(self, server_id: str) -> None:
        session = self._sessions.pop(server_id, None)
        self._tools.pop(server_id, None)
        if session:
            session.close()

    def tools(self) -> list[dict]:
        result = []
        for server_id, tools in self._tools.items():
            for tool in tools:
                result.append({"server_id": server_id, **tool})
        return result

    def call(self, server_id: str, tool: str, arguments: dict) -> dict:
        session = self._sessions.get(server_id)
        if session is None:
            raise McpError(f"сервер {server_id} не подключён")
        return session.request("tools/call", {"name": tool, "arguments": arguments})

    def close_all(self) -> None:
        for server_id in list(self._sessions):
            self.disconnect(server_id)
```


- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/mcp.py studio2/backend/tests/test_mcp.py
git commit -m "feat(studio2): MCP stdio client and server registry"
```

---

### Task 9: Run store, event bus, run artifacts

**Files:**
- Create: `studio2/backend/store.py`
- Create: `studio2/backend/events.py`
- Create: `studio2/backend/pipeline/__init__.py`
- Create: `studio2/backend/pipeline/artifacts.py`
- Create: `studio2/backend/tests/test_store_events.py`

**Interfaces:**
- Produces `RunStore(data_dir)`: `.create_run(task_id, repo_path, branch)`, `.save_run(run)`, `.get_run(run_id)`, `.update_run(run_id, **fields)`, `.list_runs()`, `.append_log(run_id, role, message)`.
- Produces `EventBus`: `.publish(run_id, event)`, `async .subscribe(run_id)`, `.close(run_id)`, `.register_question(question_id)`, `.answer(question_id, text) -> bool`, `async .wait_for_answer(question_id, timeout)`.
- Produces `RunDir(doc_dir, run_id)` with `.ensure()`, `.root`, `.logs`, `.sdd`, `.test_generated`, `.skeleton_path`, `.report_path`, `.review_path`, `.write(rel, content)`, `.read(rel)`.
- Produces `sdd_text(...)`, `skeleton_markdown(...)`, `review_markdown(...)`, `test_report_markdown(...)`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_store_events.py`:
```python
import asyncio

from events import EventBus
from pipeline.artifacts import (RunDir, review_markdown, sdd_text, skeleton_markdown,
                                test_report_markdown)
from store import RunStore


def test_create_run_writes_file(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-4217", "C:/repo", "feature/x")
    assert run["stage"] == "sdd"
    assert run["status"] == "running"
    assert (tmp_path / "runs" / f"{run['id']}.json").exists()


def test_update_and_get_run(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-4217", "C:/repo", "develop")
    store.update_run(run["id"], stage="study")
    assert store.get_run(run["id"])["stage"] == "study"


def test_get_missing_run_returns_none(tmp_path):
    assert RunStore(str(tmp_path)).get_run("nope") is None


def test_list_runs_newest_first(tmp_path):
    store = RunStore(str(tmp_path))
    first = store.create_run("TC-1", "C:/r", "a")
    second = store.create_run("TC-2", "C:/r", "b")
    ids = [run["id"] for run in store.list_runs()]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_append_log_is_capped(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-1", "C:/r", "a")
    for i in range(510):
        store.append_log(run["id"], "researcher", f"step {i}")
    assert len(store.get_run(run["id"])["log"]) == 500


def test_save_run_never_contains_secret(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-1", "C:/r", "a")
    raw = (tmp_path / "runs" / f"{run['id']}.json").read_text(encoding="utf-8")
    assert "api_key" not in raw


def test_event_bus_publish_and_subscribe():
    bus = EventBus()

    async def scenario():
        received = []

        async def reader():
            async for event in bus.subscribe("run1"):
                received.append(event)

        task = asyncio.create_task(reader())
        await asyncio.sleep(0)
        bus.publish("run1", {"type": "stage_changed", "stage": "study"})
        bus.close("run1")
        await asyncio.wait_for(task, timeout=2)
        return received

    assert asyncio.run(scenario()) == [{"type": "stage_changed", "stage": "study"}]


def test_event_bus_question_blocks_until_answer():
    bus = EventBus()

    async def scenario():
        bus.register_question("q1")
        waiter = asyncio.create_task(bus.wait_for_answer("q1", timeout=2))
        await asyncio.sleep(0)
        assert bus.answer("q1", "экран AddNewAddress")
        return await waiter

    assert asyncio.run(scenario()) == "экран AddNewAddress"


def test_event_bus_question_timeout_returns_none():
    bus = EventBus()

    async def scenario():
        bus.register_question("q2")
        return await bus.wait_for_answer("q2", timeout=0.01)

    assert asyncio.run(scenario()) is None


def test_run_dir_layout_and_write(tmp_path):
    run_dir = RunDir(str(tmp_path / "doc"), "r1")
    run_dir.ensure()
    assert (tmp_path / "doc" / "runs" / "r1" / "logs").is_dir()
    run_dir.write("sdd/agent_researcher.md", "# SDD")
    assert run_dir.read("sdd/agent_researcher.md") == "# SDD"
    assert run_dir.skeleton_path.name == "skeleton.md"
    assert run_dir.review_path.name == "review_report.md"


def test_sdd_text_fills_fields():
    text = sdd_text(role="researcher", task_id="TC-4217", task_title="Адрес",
                    repo_path="C:/repo", branch="develop", git_summary="- abc first",
                    goal="Изучить экран", rag_inputs="kb/architecture.md",
                    artifacts="—", instructions="1. Изучи экран",
                    allowed_tools="rag.search", max_rounds=2, write_scope="kb/",
                    criteria="- [ ] экран найден")
    assert text.startswith("# SDD — researcher")
    assert "TC-4217" in text
    assert "max_rounds: 2" in text


def test_skeleton_markdown_has_all_sections():
    md = skeleton_markdown("AddNewAddressTest", "TC-4217",
                           ["Открыть экран"], ["AddressAddUIConstants.AcceptButton"],
                           ["адрес"], ["валидация сервера"], ["—"])
    for section in ("# Каркас — AddNewAddressTest", "## Шаги", "## Используемые теги",
                    "## Входные данные", "## Границы", "## Ожидаемые правки"):
        assert section in md


def test_review_markdown_table_and_verdict():
    md = review_markdown("run1", [{"test": "AddTest", "status": "passed",
                                   "duration": "12s", "reason": ""}], "green")
    assert "| AddTest | passed | 12s |" in md
    assert "**Вердикт:** green" in md


def test_test_report_markdown_lists_stages():
    md = test_report_markdown("run1", "TC-4217", "develop",
                              ["sdd", "study", "done"], "green", ["a.md"], "готово")
    assert "sdd → study → done" in md
    assert "Вердикт: green" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_store_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store'`

- [ ] **Step 3: Write the run store**

`studio2/backend/store.py`:
```python
"""JSON-хранилище запусков: один файл на запуск."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

LOG_CAP = 500


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class RunStore:
    def __init__(self, data_dir: str):
        self.runs_dir = Path(data_dir) / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        return self.runs_dir / f"{run_id}.json"

    def create_run(self, task_id: str, repo_path: str, branch: str) -> dict:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        run = {
            "id": run_id,
            "task_id": task_id,
            "repo_path": repo_path,
            "branch": branch,
            "stage": "sdd",
            "status": "running",
            "round": 1,
            "created_at": _now(),
            "updated_at": _now(),
            "skeleton_approved": False,
            "execution_choice": None,
            "verdict": None,
            "log": [],
        }
        return self.save_run(run)

    def save_run(self, run: dict) -> dict:
        run["updated_at"] = _now()
        self._path(run["id"]).write_text(
            json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return run

    def get_run(self, run_id: str) -> dict | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_run(self, run_id: str, **fields) -> dict:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        run.update(fields)
        return self.save_run(run)

    def list_runs(self) -> list[dict]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return sorted(runs, key=lambda run: run.get("created_at", ""), reverse=True)

    def append_log(self, run_id: str, role: str, message: str) -> dict:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        log = run.setdefault("log", [])
        log.append({"at": _now(), "role": role, "message": message})
        run["log"] = log[-LOG_CAP:]
        return self.save_run(run)
```

- [ ] **Step 4: Write the event bus**

`studio2/backend/events.py`:
```python
"""SSE event bus с блокировкой пайплайна на вопросах к человеку."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

CLOSE = "__close__"


class EventBus:
    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._answers: dict[str, tuple[asyncio.Event, list[str]]] = {}

    def _queue(self, run_id: str) -> asyncio.Queue:
        return self._queues.setdefault(run_id, asyncio.Queue())

    def publish(self, run_id: str, event: dict) -> None:
        self._queue(run_id).put_nowait(event)

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        queue = self._queue(run_id)
        while True:
            event = await queue.get()
            if event.get("type") == CLOSE:
                return
            yield event

    def close(self, run_id: str) -> None:
        self._queue(run_id).put_nowait({"type": CLOSE})

    def register_question(self, question_id: str) -> None:
        self._answers[question_id] = (asyncio.Event(), [""])

    def answer(self, question_id: str, text: str) -> bool:
        entry = self._answers.get(question_id)
        if entry is None:
            return False
        event, holder = entry
        holder[0] = text
        event.set()
        return True

    async def wait_for_answer(self, question_id: str,
                              timeout: float | None = 1800.0) -> str | None:
        entry = self._answers.get(question_id)
        if entry is None:
            return None
        event, holder = entry
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return holder[0]
```

- [ ] **Step 5: Write the artifacts helpers**

`studio2/backend/pipeline/__init__.py`:
```python
"""Пайплайн QA-Lead: стадии, субагенты, артефакты запуска."""
```

`studio2/backend/pipeline/artifacts.py`:
```python
"""Каталог запуска и писатели документов: SDD, каркас, отчёты."""
from __future__ import annotations

from pathlib import Path


class RunDir:
    def __init__(self, doc_dir: str, run_id: str):
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


def review_markdown(run_id: str, rows: list[dict], verdict: str) -> str:
    body = "\n".join(
        f"| {row.get('test', '—')} | {row.get('status', '—')} | "
        f"{row.get('duration', '')} | {row.get('reason', '')} |"
        for row in rows
    ) or "| — | — | — | — |"
    return (
        f"# Отчёт ревью — {run_id}\n\n"
        "| Тест | Статус | Длительность | Причина |\n|---|---|---|---|\n"
        f"{body}\n\n**Вердикт:** {verdict}\n"
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_store_events.py -v`
Expected: 13 passed

- [ ] **Step 7: Commit**

```bash
git add studio2/backend/store.py studio2/backend/events.py studio2/backend/pipeline studio2/backend/tests/test_store_events.py
git commit -m "feat(studio2): run store, event bus and run artifacts"
```

---

### Task 10: Subagent runner

**Files:**
- Create: `studio2/backend/pipeline/subagents.py`
- Create: `studio2/backend/tests/test_subagents.py`

**Interfaces:**
- Consumes: `AgentSpec` (Task 2), `LlmClient` (Task 6), `RunDir` (Task 9).
- Produces `ToolNotAllowed`, `AgentReply(status, artifacts, summary, question)`, `check_tool_allowed(spec, tool)`, `parse_reply(text) -> AgentReply`, `build_messages(spec, sdd, context) -> list[dict]`, `run_subagent(role, spec, llm, run_dir, context, bus, run_id) -> AgentReply`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_subagents.py`:
```python
import json

import pytest

from config import DEFAULT_CONFIG
from llm import LlmClient
from pipeline.artifacts import RunDir
from pipeline.subagents import (AgentReply, ToolNotAllowed, build_messages,
                                check_tool_allowed, parse_reply, run_subagent)
from roles import AgentSpec


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, run_id, event):
        self.events.append(event)


def test_parse_reply_valid_json():
    reply = parse_reply('{"status": "done", "artifacts": ["kb/a.md"], "summary": "ok"}')
    assert reply.status == "done"
    assert reply.artifacts == ["kb/a.md"]
    assert reply.summary == "ok"


def test_parse_reply_handles_fenced_json():
    text = ('Ответ:\n```json\n{"status": "need_question", "artifacts": [], '
            '"summary": "", "question": {"text": "Какой экран?"}}\n```')
    reply = parse_reply(text)
    assert reply.status == "need_question"
    assert reply.question["text"] == "Какой экран?"


def test_parse_reply_without_json_is_fail():
    reply = parse_reply("не json")
    assert reply.status == "fail"
    assert "не удалось" in reply.summary


def test_parse_reply_invalid_status_is_fail():
    assert parse_reply('{"status": "weird"}').status == "fail"


def test_parse_reply_artifacts_must_be_list():
    assert parse_reply('{"status": "done", "artifacts": "x"}').artifacts == []


def test_check_tool_allowed_passes_and_raises():
    spec = AgentSpec(role="researcher", allowed_tools=["rag.search"])
    check_tool_allowed(spec, "rag.search")
    with pytest.raises(ToolNotAllowed):
        check_tool_allowed(spec, "gradle.run")


def test_build_messages_includes_sdd_and_context():
    spec = AgentSpec(role="researcher", body="Ты исследователь")
    messages = build_messages(spec, "# SDD — researcher", {"RAG": "- kb/a.md"})
    assert len(messages) == 1
    content = messages[0]["content"]
    assert "# SDD — researcher" in content
    assert "kb/a.md" in content
    assert "JSON" in content


def test_run_subagent_emits_sse_and_returns_reply(tmp_path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    run_dir = RunDir(str(tmp_path / "doc"), "r1")
    run_dir.ensure()
    run_dir.write("sdd/agent_researcher.md", "# SDD — researcher")
    bus = FakeBus()
    spec = AgentSpec(role="researcher", model="fake-1", body="Ты исследователь",
                     allowed_tools=["rag.search"])
    reply = run_subagent("researcher", spec, LlmClient(cfg), run_dir, {}, bus, "r1")
    assert isinstance(reply, AgentReply)
    assert reply.status == "done"
    types = [event["type"] for event in bus.events]
    assert types[0] == "agent_started"
    assert "agent_delta" in types
    assert types[-1] == "agent_done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_subagents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.subagents'`

- [ ] **Step 3: Write the implementation**

`studio2/backend/pipeline/subagents.py`:
```python
"""Запуск субагента: промпт, вызов LLM, разбор JSON-ответа, SSE-события."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from roles import AgentSpec

VALID_STATUSES = {"done", "need_question", "fail"}
JSON_RE = re.compile(r"\{[\s\S]*\}")


class ToolNotAllowed(RuntimeError):
    pass


@dataclass
class AgentReply:
    status: str
    artifacts: list[str] = field(default_factory=list)
    summary: str = ""
    question: dict | None = None


def check_tool_allowed(spec: AgentSpec, tool: str) -> None:
    if tool not in spec.allowed_tools:
        raise ToolNotAllowed(f"роль {spec.role} не может вызывать {tool}")


def parse_reply(text: str) -> AgentReply:
    match = JSON_RE.search(text or "")
    if not match:
        return AgentReply(status="fail", summary="не удалось разобрать ответ агента: нет JSON")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return AgentReply(status="fail", summary="не удалось разобрать JSON ответа агента")
    if not isinstance(payload, dict):
        return AgentReply(status="fail", summary="ответ агента не является объектом JSON")
    status = payload.get("status", "fail")
    if status not in VALID_STATUSES:
        return AgentReply(status="fail", summary=f"недопустимый статус: {status}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    question = payload.get("question")
    return AgentReply(
        status=status,
        artifacts=[str(item) for item in artifacts],
        summary=str(payload.get("summary") or ""),
        question=question if isinstance(question, dict) else None,
    )


def build_messages(spec: AgentSpec, sdd: str, context: dict) -> list[dict]:
    parts = [f"# SDD\n{sdd}"]
    for name, value in context.items():
        if value:
            parts.append(f"# {name}\n{value}")
    parts.append("Ответь строго одним JSON-объектом по формату из SDD.")
    return [{"role": "user", "content": "\n\n".join(parts)}]


def run_subagent(role: str, spec: AgentSpec, llm, run_dir, context: dict,
                 bus, run_id: str) -> AgentReply:
    sdd = run_dir.read(f"sdd/agent_{role}.md")
    bus.publish(run_id, {"type": "agent_started", "role": role, "model": spec.model})
    chunks = []
    for chunk in llm.stream(role, build_messages(spec, sdd, context), system=spec.body):
        chunks.append(chunk)
        bus.publish(run_id, {"type": "agent_delta", "role": role, "text": chunk})
    reply = parse_reply("".join(chunks))
    bus.publish(run_id, {"type": "agent_done", "role": role,
                         "status": reply.status, "summary": reply.summary})
    return reply
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_subagents.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/pipeline/subagents.py studio2/backend/tests/test_subagents.py
git commit -m "feat(studio2): subagent reply parsing and runner"
```


---

### Task 11: Pipeline orchestrator with stage machine, gates and rounds

**Files:**
- Create: `studio2/backend/pipeline/lead.py`
- Create: `studio2/backend/tests/test_pipeline.py`
- Create: `studio2/backend/tests/test_invariants.py`

**Interfaces:**
- Consumes: Tasks 1–10.
- Produces `STAGES`, `InvalidTransition`, `GateError`, `CriticSameModel`, `QuestionTimeout`.
- Produces `QaLead(doc_dir, data_dir, llm, bus, store, config=None, rag=None, git_factory=None, android=None, gradle=None)` with:
  - `.validate_critic() -> None`
  - `.validate_transition(current, nxt) -> None`
  - `.next_stage(nxt, run) -> str`
  - `.ensure_gate(stage, run) -> None`
  - `.approve_skeleton(run_id, approved=True) -> dict`
  - `.choose_execution(run_id, choice) -> dict`
  - `.cancel(run_id) -> None`
  - `.start(run_id) -> None` (runs `sdd → study → skeleton`, raises `PendingGate`, and resumes with `.resume(run_id)` after both gates)
  - `.resume(run_id) -> None` (runs `write → provision → execute → debug → review → done`)

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_pipeline.py`:
```python
import json
import shutil
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG
from events import EventBus
from llm import LlmClient
from pipeline.lead import (CriticSameModel, GateError, InvalidTransition, PendingGate,
                           QaLead, STAGES)
from store import RunStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def env(tmp_path):
    doc = tmp_path / "doc"
    shutil.copytree(REPO_DOC, doc)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    store = RunStore(str(tmp_path / "data"))
    lead = QaLead(str(doc), str(tmp_path / "data"), LlmClient(cfg), EventBus(),
                  store, config=cfg)
    run = store.create_run("TC-4217", "C:/repo", "develop")
    return {"lead": lead, "store": store, "doc": doc, "run": run, "cfg": cfg}


def test_stage_order_is_fixed():
    assert STAGES == ["sdd", "study", "skeleton", "write", "provision",
                      "execute", "debug", "review", "done"]


def test_forward_transition_allowed(env):
    env["lead"].validate_transition("sdd", "study")


def test_backward_transition_rejected(env):
    with pytest.raises(InvalidTransition):
        env["lead"].validate_transition("execute", "study")


def test_round2_allowed_from_review(env):
    env["lead"].validate_transition("review", "study")


def test_third_round_rejected(env):
    run = dict(env["run"], stage="review", round=2)
    with pytest.raises(InvalidTransition):
        env["lead"].next_stage("study", run)


def test_write_requires_skeleton_approval(env):
    with pytest.raises(GateError):
        env["lead"].ensure_gate("write", env["run"])


def test_provision_requires_execution_choice(env):
    run = dict(env["run"], skeleton_approved=True, stage="write")
    with pytest.raises(GateError):
        env["lead"].ensure_gate("provision", run)


def test_gates_pass_when_satisfied(env):
    run = dict(env["run"], skeleton_approved=True, execution_choice="generated")
    env["lead"].ensure_gate("write", run)
    env["lead"].ensure_gate("provision", run)


def test_approve_skeleton_updates_run(env):
    env["lead"].approve_skeleton(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["skeleton_approved"] is True


def test_choose_execution_rejects_unknown_choice(env):
    with pytest.raises(ValueError):
        env["lead"].choose_execution(env["run"]["id"], "magic")


def test_start_writes_sdd_and_stops_at_gate(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    sdd_dir = env["doc"] / "runs" / env["run"]["id"] / "sdd"
    files = {p.name for p in sdd_dir.glob("*.md")}
    assert {"agent_qa-lead.md", "agent_researcher.md",
            "agent_testwriter.md", "agent_reviewer.md"} <= files
    assert (env["doc"] / "runs" / env["run"]["id"] / "skeleton.md").exists()
    run = env["store"].get_run(env["run"]["id"])
    assert run["stage"] == "skeleton"
    assert run["skeleton_approved"] is False


def test_resume_requires_gates(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    with pytest.raises(GateError):
        env["lead"].resume(env["run"]["id"])


def test_full_run_reaches_done_with_fake_llm(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    env["lead"].approve_skeleton(env["run"]["id"])
    env["lead"].choose_execution(env["run"]["id"], "generated")
    env["lead"].resume(env["run"]["id"])
    run = env["store"].get_run(env["run"]["id"])
    assert run["status"] == "done"
    assert run["verdict"] == "green"
    assert STAGES.index(run["stage"]) == STAGES.index("done")


def test_full_run_writes_reports(env):
    with pytest.raises(PendingGate):
        env["lead"].start(env["run"]["id"])
    env["lead"].approve_skeleton(env["run"]["id"])
    env["lead"].choose_execution(env["run"]["id"], "generated")
    env["lead"].resume(env["run"]["id"])
    run_root = env["doc"] / "runs" / env["run"]["id"]
    assert (run_root / "review_report.md").exists()
    assert (run_root / "test_report.md").exists()
    assert (run_root / "test_generated").is_dir()


def test_critic_must_differ(env):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    same = cfg["roles"]["testwriter"]["model"]
    cfg["roles"]["reviewer"]["model"] = same
    lead = QaLead(str(env["doc"]), str(env["doc"]), None, None,
                  env["store"], config=cfg)
    with pytest.raises(CriticSameModel):
        lead.validate_critic()


def test_cancel_marks_run_cancelled(env):
    env["lead"].cancel(env["run"]["id"])
    assert env["store"].get_run(env["run"]["id"])["status"] == "cancelled"
```

`studio2/backend/tests/test_invariants.py`:
```python
import json
from pathlib import Path

import pytest

from config import DEFAULT_CONFIG
from events import EventBus
from pipeline.lead import CriticSameModel, QaLead
from store import RunStore


def test_reviewer_same_model_is_rejected(tmp_path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["roles"]["reviewer"]["model"] = cfg["roles"]["testwriter"]["model"]
    lead = QaLead(str(tmp_path / "doc"), str(tmp_path / "data"), None,
                  EventBus(), RunStore(str(tmp_path / "data")), config=cfg)
    with pytest.raises(CriticSameModel):
        lead.validate_critic()


def test_default_models_are_different(tmp_path):
    lead = QaLead(str(tmp_path / "doc"), str(tmp_path / "data"), None,
                  EventBus(), RunStore(str(tmp_path / "data")),
                  config=json.loads(json.dumps(DEFAULT_CONFIG)))
    lead.validate_critic()


def test_run_json_has_no_api_key(tmp_path):
    store = RunStore(str(tmp_path / "data"))
    run = store.create_run("TC-1", "C:/repo", "main")
    raw = (tmp_path / "data" / "runs" / f"{run['id']}.json").read_text(encoding="utf-8")
    assert "api_key" not in raw


def test_stage_list_has_no_extra_entries():
    from pipeline.lead import STAGES
    assert len(STAGES) == 9
    assert STAGES[0] == "sdd"
    assert STAGES[-1] == "done"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pipeline.py tests/test_invariants.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.lead'`

- [ ] **Step 3: Write the orchestrator**

`studio2/backend/pipeline/lead.py`:
```python
"""Оркестратор QA-Lead: стадийная машина, гейты, раунды, ревью отдельной LLM."""
from __future__ import annotations

from config import load_config
from pipeline.artifacts import (RunDir, review_markdown, sdd_text, skeleton_markdown,
                                test_report_markdown)
from pipeline.subagents import run_subagent
from roles import load_agents

STAGES = ["sdd", "study", "skeleton", "write", "provision",
          "execute", "debug", "review", "done"]

_STAGE_ROLE = {
    "study": "researcher",
    "skeleton": "testwriter",
    "write": "testwriter",
    "debug": "debugger",
    "review": "reviewer",
}


class InvalidTransition(RuntimeError):
    pass


class GateError(RuntimeError):
    pass


class PendingGate(RuntimeError):
    pass


class CriticSameModel(RuntimeError):
    pass


class QuestionTimeout(RuntimeError):
    pass


class QaLead:
    def __init__(self, doc_dir, data_dir, llm, bus, store, config=None,
                 rag=None, git_factory=None, android=None, gradle=None):
        self.doc_dir = doc_dir
        self.data_dir = data_dir
        self.llm = llm
        self.bus = bus
        self.store = store
        self.config = config or load_config(data_dir)
        self.rag = rag
        self.git_factory = git_factory
        self.android = android
        self.gradle = gradle
        self.agents = load_agents(doc_dir)
        self._cancelled: set[str] = set()

    # ---------- инварианты ----------

    def validate_critic(self) -> None:
        if not self.config.get("critic", {}).get("must_differ_from_author", True):
            return
        reviewer = self.config["roles"]["reviewer"]["model"]
        writer = self.config["roles"]["testwriter"]["model"]
        if reviewer == writer:
            raise CriticSameModel(
                "модель ревьюера должна отличаться от модели тестировщика"
            )

    def validate_transition(self, current: str, nxt: str) -> None:
        if nxt == "study" and current == "review":
            return
        if current not in STAGES or nxt not in STAGES:
            raise InvalidTransition(f"неизвестная стадия: {current} → {nxt}")
        if STAGES.index(nxt) <= STAGES.index(current):
            raise InvalidTransition(f"запрещённый переход: {current} → {nxt}")

    def next_stage(self, nxt: str, run: dict) -> str:
        self.validate_transition(run["stage"], nxt)
        if nxt == "study" and run.get("round", 1) >= 2:
            raise InvalidTransition("третий круг изучения запрещён")
        return nxt

    def ensure_gate(self, stage: str, run: dict) -> None:
        if stage == "write" and not run.get("skeleton_approved"):
            raise GateError("переход в write запрещён: каркас не утверждён")
        if stage == "provision" and not run.get("execution_choice"):
            raise GateError("переход в provision запрещён: способ исполнения не выбран")

    # ---------- гейты ----------

    def approve_skeleton(self, run_id: str, approved: bool = True) -> dict:
        run = self.store.update_run(run_id, skeleton_approved=approved)
        self.bus.publish(run_id, {
            "type": "skeleton_approved" if approved else "skeleton_updated",
            "path": "skeleton.md",
        })
        self.store.append_log(run_id, "human",
                              "каркас утверждён" if approved else "каркас отклонён")
        return run

    def choose_execution(self, run_id: str, choice: str) -> dict:
        if choice not in {"as_loaded", "generated"}:
            raise ValueError("способ исполнения должен быть as_loaded или generated")
        run = self.store.update_run(run_id, execution_choice=choice)
        self.bus.publish(run_id, {"type": "execution_chosen", "choice": choice})
        self.store.append_log(run_id, "human", f"способ исполнения: {choice}")
        return run

    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)
        self.store.update_run(run_id, status="cancelled")
        self.bus.publish(run_id, {"type": "run_done", "status": "cancelled",
                                  "report_path": ""})
        self.bus.close(run_id)

    # ---------- основной цикл ----------

    def start(self, run_id: str) -> None:
        run = self._require(run_id)
        self.validate_critic()
        self.bus.publish(run_id, {"type": "run_started", "run_id": run_id,
                                  "task": run["task_id"]})
        run_dir = RunDir(self.doc_dir, run_id)
        run_dir.ensure()
        self._write_sdd(run, run_dir)
        self._emit_stage(run_id, "study", run.get("round", 1))
        self._run_stage_agent("study", run, run_dir)
        run = self._require(run_id)
        self._emit_stage(run_id, "skeleton", run.get("round", 1))
        self._run_stage_agent("skeleton", run, run_dir)
        if not run_dir.skeleton_path.exists():
            run_dir.write("skeleton.md", self._default_skeleton(run))
        self.bus.publish(run_id, {"type": "skeleton_ready",
                                  "path": "skeleton.md",
                                  "steps": [], "tags": [], "assumptions": []})
        raise PendingGate("пайплайн ждёт апрув каркаса и выбор способа исполнения")

    def resume(self, run_id: str) -> None:
        run = self._require(run_id)
        run_dir = RunDir(self.doc_dir, run_id)
        run_dir.ensure()
        self.ensure_gate("write", run)
        self.ensure_gate("provision", run)

        self._emit_stage(run_id, "write", run.get("round", 1))
        self._run_stage_agent("write", run, run_dir)
        self._ensure_generated_test(run, run_dir)

        self._emit_stage(run_id, "provision", run.get("round", 1))
        self._provision(run, run_dir)

        self._emit_stage(run_id, "execute", run.get("round", 1))
        result = self._execute(run, run_dir)

        if not result["success"]:
            self._emit_stage(run_id, "debug", run.get("round", 1))
            self._run_stage_agent("debug", run, run_dir, context={
                "Gradle log": result.get("log", "")[:4000],
            })

        self._emit_stage(run_id, "review", run.get("round", 1))
        self._run_stage_agent("review", run, run_dir, context={
            "Gradle result": str(result),
        })
        verdict = "green" if result["success"] else "red"
        run_dir.write("review_report.md", review_markdown(
            run_id,
            [{"test": row["test"], "status": "passed" if result["success"] else "failed",
              "duration": result.get("duration", ""),
              "reason": row.get("method", "")} for row in result.get("failures", [])],
            verdict,
        ))
        self.bus.publish(run_id, {"type": "review_verdict", "verdict": verdict,
                                  "path": "review_report.md"})

        self._finish(run_id, run_dir, run, result, verdict)

    def _finish(self, run_id, run_dir, run, result, verdict) -> None:
        run = self._require(run_id, allow_done=True)
        stages = STAGES[:STAGES.index(run["stage"]) + 1] + ["done"]
        run_dir.write("test_report.md", test_report_markdown(
            run_id, run["task_id"], run["branch"], stages, verdict,
            ["skeleton.md", "review_report.md", "test_report.md"],
            "Пайплайн завершён. См. review_report.md и test_generated/.",
        ))
        self._emit_stage(run_id, "done", run.get("round", 1))
        self.store.update_run(run_id, status="done", verdict=verdict)
        self.bus.publish(run_id, {"type": "run_done", "status": "done",
                                  "report_path": "test_report.md"})
        self.bus.close(run_id)

    # ---------- стадии ----------

    def _require(self, run_id: str, allow_done: bool = False) -> dict:
        if run_id in self._cancelled:
            raise InvalidTransition("запуск отменён")
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"запуск {run_id} не найден")
        if not allow_done and run.get("status") == "cancelled":
            raise InvalidTransition("запуск отменён")
        return run

    def _emit_stage(self, run_id: str, stage: str, round_no: int) -> None:
        self.store.update_run(run_id, stage=stage, round=round_no)
        self.bus.publish(run_id, {"type": "stage_changed", "stage": stage,
                                  "round": round_no})

    def _write_sdd(self, run: dict, run_dir: RunDir) -> None:
        git_summary = "git недоступен, контекст ограничен"
        if self.git_factory:
            try:
                git_summary = self.git_factory(run["repo_path"]).recent_changes(limit=10)
            except Exception as exc:  # noqa: BLE001 - причина уходит агенту
                git_summary = f"git: {exc}"
        for role, spec in self.agents.items():
            text = sdd_text(
                role=role,
                task_id=run["task_id"],
                task_title=run["task_id"],
                repo_path=run["repo_path"],
                branch=run["branch"],
                git_summary=git_summary[:4000],
                goal=spec.body[:400],
                rag_inputs=", ".join(spec.inputs),
                artifacts="—",
                instructions="Выполни роль согласно своему описанию.",
                allowed_tools=", ".join(spec.allowed_tools),
                max_rounds=spec.max_rounds,
                write_scope=", ".join(spec.outputs) or "—",
                criteria="- [ ] статус done в JSON-ответе",
            )
            run_dir.write(f"sdd/agent_{role}.md", text)
            self.bus.publish(run["id"], {"type": "sdd_written", "role": role,
                                         "path": f"sdd/agent_{role}.md", "title": role})

    def _run_stage_agent(self, stage: str, run: dict, run_dir: RunDir,
                         context: dict | None = None) -> None:
        role = _STAGE_ROLE[stage]
        spec = self.agents[role]
        ctx: dict = dict(context or {})
        if self.rag is not None:
            try:
                hits = self.rag.search(run["task_id"], limit=5)
                ctx["RAG"] = "\n".join(f"- {hit['path']}: {hit['snippet']}" for hit in hits)
            except Exception as exc:  # noqa: BLE001
                ctx["RAG"] = f"RAG недоступен: {exc}"
        reply = run_subagent(role, spec, self.llm, run_dir, ctx, self.bus, run["id"])
        self.store.append_log(run["id"], role, reply.summary or reply.status)
        if reply.status == "need_question":
            self.bus.publish(run["id"], {
                "type": "question",
                "question_id": f"{run['id']}-{role}",
                "role": role,
                "text": (reply.question or {}).get("text", "нужна уточняющая информация"),
                "options": (reply.question or {}).get("options", []),
            })

    def _default_skeleton(self, run: dict) -> str:
        return skeleton_markdown(
            f"Generated{run['task_id'].replace('-', '')}Test",
            run["task_id"],
            ["Открыть экран по шагам ТК"],
            ["—"],
            ["—"],
            ["—"],
            ["Уточнить теги после разведки экрана"],
        )

    def _ensure_generated_test(self, run: dict, run_dir: RunDir) -> None:
        if any(run_dir.test_generated.glob("*.kt")):
            return
        path = run_dir.write(
            "test_generated/GeneratedTest.kt",
            "package ru.leroymerlin.mobile.presentation.generated\n\n"
            "// Сгенерировано QA-Lead (fake-режим или без сети)\n",
        )
        self.bus.publish(run["id"], {"type": "test_written", "path": str(path),
                                     "loc": 3})

    def _provision(self, run: dict, run_dir: RunDir) -> None:
        if self.android is None:
            self.bus.publish(run["id"], {"type": "gradle_log",
                                         "line": "provision: adb не настроен, SKIP"})
            return
        try:
            run_dir.logs.mkdir(parents=True, exist_ok=True)
            tree = self.android.dump_ui()
            path = run_dir.write("logs/ui_1.xml", str(tree))
            self.bus.publish(run["id"], {"type": "adb_dump", "screen": run["task_id"],
                                         "nodes": str(tree)[:200], "path": str(path)})
        except Exception as exc:  # noqa: BLE001 - деградация вместо падения
            self.bus.publish(run["id"], {"type": "gradle_log",
                                         "line": f"provision SKIP: {exc}"})

    def _execute(self, run: dict, run_dir: RunDir) -> dict:
        if self.gradle is None:
            return {"success": True, "failures": [], "duration": "—", "log": "gradle не настроен"}
        try:
            result = self.gradle.connected_test(run["task_id"],
                                                run_dir.logs / "gradle_execute.log")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "failures": [], "duration": "",
                    "log": f"Gradle недоступен: {exc}"}
        self.bus.publish(run["id"], {"type": "gradle_log",
                                     "line": f"execute: success={result.get('success')}"})
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pipeline.py tests/test_invariants.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/pipeline/lead.py studio2/backend/tests/test_pipeline.py studio2/backend/tests/test_invariants.py
git commit -m "feat(studio2): pipeline orchestrator with gates, rounds and reviewer invariant"
```


---

### Task 12: FastAPI app — health, config, models, doc, rag, tasks, MCP

**Files:**
- Create: `studio2/backend/main.py`
- Create: `studio2/backend/tests/test_api_config_doc.py`
- Create: `studio2/backend/tests/test_api_mcp.py`

**Interfaces:**
- Produces `create_app(doc_dir=None, data_dir=None, llm=None) -> FastAPI` and module-level `app`.
- Endpoints: `GET /api/health`, `GET|POST /api/config`, `GET /api/models`, `POST /api/models/test`, `GET /api/doc/tree`, `GET|POST|DELETE /api/doc/file`, `POST /api/doc/upload`, `POST /api/rag/reindex`, `GET /api/rag/search`, `GET /api/tasks`, `GET /api/mcp/servers`, `POST /api/mcp/servers`, `DELETE /api/mcp/servers/{sid}`, `POST /api/mcp/servers/{sid}/connect`, `POST /api/mcp/servers/{sid}/disconnect`, `GET /api/mcp/tools`.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_api_config_doc.py`:
```python
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from main import create_app

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


def _client(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    return TestClient(create_app(doc_dir=str(tmp_path / "doc"),
                                 data_dir=str(tmp_path / "data")))


def test_health(tmp_path):
    assert _client(tmp_path).get("/api/health").json()["status"] == "ok"


def test_config_masks_keys(tmp_path):
    body = _client(tmp_path).get("/api/config").json()
    assert set(body["roles"]) == {"qa-lead", "researcher", "testwriter",
                                  "debugger", "reviewer"}
    assert "api_key" not in body["providers"]["ollama"]
    assert "api_key_masked" in body["providers"]["ollama"]


def test_config_update_role(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/config", json={"roles": {"researcher": {"temperature": 0.5}}})
    assert response.status_code == 200
    assert client.get("/api/config").json()["roles"]["researcher"]["temperature"] == 0.5


def test_config_rejects_same_reviewer_model(tmp_path):
    client = _client(tmp_path)
    writer_model = client.get("/api/config").json()["roles"]["testwriter"]["model"]
    response = client.post("/api/config", json={"roles": {"reviewer": {"model": writer_model}}})
    assert response.status_code == 400
    assert "отличаться" in response.json()["detail"]


def test_models_fake(tmp_path):
    body = _client(tmp_path).get("/api/models", params={"provider": "fake"}).json()
    assert body["models"] == ["fake-1"]


def test_model_test_endpoint(tmp_path):
    body = _client(tmp_path).post("/api/models/test", json={"role": "reviewer"}).json()
    assert body["ok"] is True


def test_doc_tree_and_crud(tmp_path):
    client = _client(tmp_path)
    names = {node["name"] for node in client.get("/api/doc/tree").json()["tree"]}
    assert {"kb", "tasks", "agents", "templates"} <= names
    assert "TC-4217" in client.get("/api/doc/file", params={"path": "tasks/TC-4217.md"}).json()["content"]
    saved = client.post("/api/doc/file", json={"path": "kb/notes.md", "content": "# N\n"})
    assert saved.status_code == 200
    assert client.get("/api/doc/file", params={"path": "kb/notes.md"}).json()["content"] == "# N\n"
    assert client.delete("/api/doc/file", params={"path": "kb/notes.md"}).status_code == 200


def test_doc_missing_file_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/doc/file", params={"path": "kb/none.md"}).status_code == 404


def test_doc_write_outside_roots_returns_400(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/doc/file", json={"path": "runs/x.md", "content": "x"}).status_code == 400


def test_upload_and_search(tmp_path):
    client = _client(tmp_path)
    upload = client.post("/api/doc/upload", data={"dir": "tasks"},
                         files={"file": ("TC-9999.md", b"# TC-9999\n", "text/markdown")})
    assert upload.json()["path"] == "tasks/TC-9999.md"
    assert client.post("/api/rag/reindex").json()["documents"] >= 10
    hits = client.get("/api/rag/search", params={"q": "адрес", "limit": 3}).json()["hits"]
    assert hits


def test_tasks_endpoint(tmp_path):
    tasks = _client(tmp_path).get("/api/tasks").json()["tasks"]
    assert any(task["id"] == "TC-4217" for task in tasks)
```

`studio2/backend/tests/test_api_mcp.py`:
```python
import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from main import create_app

SERVER = textwrap.dedent(
    """
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line); method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "t", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "getTestCase", "description": "allure tc",
                 "inputSchema": {"type": "object"}}]}})
        elif method == "notifications/initialized":
            continue
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id", 0),
                  "error": {"code": -32601, "message": "unknown"}})
    """
)


def _client(tmp_path):
    (tmp_path / "doc").mkdir()
    return TestClient(create_app(doc_dir=str(tmp_path / "doc"),
                                 data_dir=str(tmp_path / "data")))


def test_mcp_server_lifecycle(tmp_path):
    server_py = tmp_path / "server.py"
    server_py.write_text(SERVER, encoding="utf-8")
    client = _client(tmp_path)
    created = client.post("/api/mcp/servers", json={
        "name": "allure", "type": "stdio", "command": [sys.executable, str(server_py)],
    })
    assert created.status_code == 201
    server_id = created.json()["server"]["id"]
    listed = client.get("/api/mcp/servers").json()["servers"]
    assert listed[0]["status"]["state"] == "idle"
    connected = client.post(f"/api/mcp/servers/{server_id}/connect")
    assert connected.status_code == 200
    tools = client.get("/api/mcp/tools").json()["tools"]
    assert tools[0]["name"] == "getTestCase"
    assert client.post(f"/api/mcp/servers/{server_id}/disconnect").status_code == 200
    assert client.delete(f"/api/mcp/servers/{server_id}").status_code == 200


def test_mcp_add_rejects_bad_type(tmp_path):
    response = _client(tmp_path).post("/api/mcp/servers", json={"name": "x", "type": "ftp"})
    assert response.status_code == 400


def test_mcp_unknown_server_returns_404(tmp_path):
    assert _client(tmp_path).post("/api/mcp/servers/nope/connect").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_config_doc.py tests/test_api_mcp.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Write the implementation**

`studio2/backend/main.py`:
```python
"""FastAPI-приложение studio2: конфиг, документы, RAG, задачи, MCP."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from config import load_config, mask_secret, save_config
from docstore import DocPathError, DocStore
from llm import LlmClient, LlmError
from mcp import McpError, McpRegistry, McpServerStore
from rag import RagIndex

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_DOC = BACKEND_DIR.parent / "doc"
DEFAULT_DATA = BACKEND_DIR.parent / "data"
SETTINGS_FIELDS = ("providers", "roles", "critic", "android")


def _public_config(cfg: dict) -> dict:
    out = json.loads(json.dumps(cfg))
    for provider in out["providers"].values():
        raw = provider.pop("api_key", None)
        provider["api_key_masked"] = mask_secret(raw)
    return out


def _merge_config(current: dict, updates: dict) -> dict:
    for key, value in updates.items():
        if key in SETTINGS_FIELDS and isinstance(value, dict):
            for name, fields in value.items():
                if isinstance(fields, dict):
                    current[key].setdefault(name, {}).update(fields)
                else:
                    current[key][name] = fields
        else:
            current[key] = value
    return current


def _parse_task(text: str, path: Path) -> dict:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        head, _, _body = rest.partition("\n---")
        for line in head.strip().splitlines():
            key, _, value = line.partition(":")
            if key.strip():
                meta[key.strip()] = value.strip()
    meta.setdefault("id", path.stem)
    meta.setdefault("title", meta["id"])
    meta.setdefault("status", "ready")
    return meta


def create_app(doc_dir: str | None = None, data_dir: str | None = None,
               llm: LlmClient | None = None) -> FastAPI:
    doc_root = str(doc_dir or DEFAULT_DOC)
    data_root = str(data_dir or DEFAULT_DATA)
    docs = DocStore(doc_root)
    rag = RagIndex(doc_root)
    rag.reindex()
    mcp_store = McpServerStore(data_root)
    mcp_registry = McpRegistry(mcp_store)
    app = FastAPI(title="QA-Lead Studio")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def client() -> LlmClient:
        nonlocal llm
        if llm is None:
            llm = LlmClient(load_config(data_root))
        return llm

    @app.on_event("shutdown")
    def _shutdown() -> None:
        mcp_registry.close_all()

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": "day35"}

    @app.get("/api/config")
    def get_config() -> dict:
        return _public_config(load_config(data_root))

    @app.post("/api/config")
    def post_config(body: dict) -> dict:
        cfg = load_config(data_root)
        merged = _merge_config(cfg, body)
        if merged.get("critic", {}).get("must_differ_from_author", True):
            if merged["roles"]["reviewer"]["model"] == merged["roles"]["testwriter"]["model"]:
                raise HTTPException(
                    400, "модель ревьюера должна отличаться от модели тестировщика"
                )
        for provider in merged["providers"].values():
            provider.pop("api_key_masked", None)
        save_config(data_root, merged)
        nonlocal llm
        llm = None
        return _public_config(merged)

    @app.get("/api/models")
    def models(provider: str = "ollama") -> dict:
        try:
            return {"provider": provider, "models": client().list_models(provider)}
        except LlmError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/models/test")
    def test_model(body: dict) -> dict:
        role = body.get("role")
        if role not in load_config(data_root)["roles"]:
            raise HTTPException(400, "неизвестная роль")
        return client().test_model(role)

    @app.get("/api/doc/tree")
    def doc_tree() -> dict:
        return {"tree": docs.tree()}

    @app.get("/api/doc/file")
    def doc_read(path: str) -> dict:
        try:
            return {"path": path, "content": docs.read(path)}
        except DocPathError as exc:
            code = 404 if "не найден" in str(exc) else 400
            raise HTTPException(code, str(exc)) from exc

    @app.post("/api/doc/file")
    def doc_write(body: dict) -> dict:
        try:
            docs.write(body.get("path", ""), body.get("content", ""))
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": body.get("path"), "saved": True}

    @app.post("/api/doc/upload")
    async def doc_upload(dir: str = Form("kb"), file: UploadFile = File(...)) -> dict:
        data = await file.read()
        try:
            rel = docs.upload(dir, file.filename or "upload.md", data)
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"path": rel, "bytes": len(data)}

    @app.delete("/api/doc/file")
    def doc_delete(path: str) -> dict:
        try:
            docs.delete(path)
        except DocPathError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": path}

    @app.post("/api/rag/reindex")
    def rag_reindex() -> dict:
        return {"documents": rag.reindex(), "stats": rag.stats()}

    @app.get("/api/rag/search")
    def rag_search(q: str, limit: int = 5) -> dict:
        return {"query": q, "hits": rag.search(q, limit=max(1, min(limit, 20)))}

    @app.get("/api/tasks")
    def tasks() -> dict:
        items = [
            _parse_task(path.read_text(encoding="utf-8"), path)
            for path in sorted((Path(doc_root) / "tasks").glob("*.md"))
        ]
        return {"tasks": items}

    @app.get("/api/mcp/servers")
    def mcp_servers() -> dict:
        servers = []
        for server in mcp_store.list():
            servers.append({**server, "status": mcp_registry.status(server["id"])})
        return {"servers": servers}

    @app.post("/api/mcp/servers", status_code=201)
    def mcp_add(body: dict) -> dict:
        try:
            server = mcp_store.add(body)
        except McpError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"server": {**server, "status": mcp_registry.status(server["id"])}}

    @app.delete("/api/mcp/servers/{sid}")
    def mcp_delete(sid: str) -> dict:
        try:
            mcp_registry.disconnect(sid)
            mcp_store.delete(sid)
        except McpError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"deleted": sid}

    @app.post("/api/mcp/servers/{sid}/connect")
    def mcp_connect(sid: str) -> dict:
        try:
            result = mcp_registry.connect(sid)
        except McpError as exc:
            message = str(exc)
            raise HTTPException(404 if "не найден" in message else 400, message) from exc
        return {"tools": result["tools"], "status": mcp_registry.status(sid)}

    @app.post("/api/mcp/servers/{sid}/disconnect")
    def mcp_disconnect(sid: str) -> dict:
        try:
            mcp_store.get(sid)
        except McpError as exc:
            raise HTTPException(404, str(exc)) from exc
        mcp_registry.disconnect(sid)
        return {"status": mcp_registry.status(sid)}

    @app.get("/api/mcp/tools")
    def mcp_tools() -> dict:
        return {"tools": mcp_registry.tools()}

    return app


app = create_app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_config_doc.py tests/test_api_mcp.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add studio2/backend/main.py studio2/backend/tests/test_api_config_doc.py studio2/backend/tests/test_api_mcp.py
git commit -m "feat(studio2): API for config, doc, rag, tasks and MCP"
```

---

### Task 13: Run endpoints, SSE stream, skeleton and execution gates

**Files:**
- Modify: `studio2/backend/main.py` (add run wiring and endpoints)
- Create: `studio2/backend/tests/test_api_runs.py`

**Interfaces:**
- Consumes: `QaLead` (Task 11), `EventBus` (Task 9), `RunStore` (Task 9).
- Produces endpoints: `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/stream`, `GET|PUT /api/runs/{id}/skeleton`, `POST /api/runs/{id}/approve-skeleton`, `POST /api/runs/{id}/execution-choice`, `POST /api/runs/{id}/answer`, `POST /api/runs/{id}/cancel`.
- Background work runs in a thread via `asyncio.to_thread` so SSE stays responsive.

- [ ] **Step 1: Write the failing tests**

`studio2/backend/tests/test_api_runs.py`:
```python
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from events import EventBus
from llm import LlmClient
from main import create_app
from pipeline.lead import QaLead
from store import RunStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def env(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    store = RunStore(str(tmp_path / "data"))
    app = create_app(doc_dir=str(tmp_path / "doc"), data_dir=str(tmp_path / "data"),
                     llm=LlmClient(cfg))
    app.state.lead_factory = lambda: QaLead(
        str(tmp_path / "doc"), str(tmp_path / "data"), LlmClient(cfg), app.state.bus,
        store, config=cfg,
    )
    return TestClient(app), tmp_path


def test_create_run_returns_id(env):
    client, _ = env
    response = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/repo", "branch": "develop",
    })
    assert response.status_code == 201
    assert response.json()["run"]["id"]


def test_create_run_validates_task(env):
    client, _ = env
    assert client.post("/api/runs", json={
        "task_id": "TC-9999", "repo_path": "C:/repo", "branch": "develop",
    }).status_code == 400


def test_runs_listing(env):
    client, _ = env
    client.post("/api/runs", json={"task_id": "TC-4217", "repo_path": "C:/r", "branch": "b"})
    runs = client.get("/api/runs").json()["runs"]
    assert runs and runs[0]["task_id"] == "TC-4217"


def test_unknown_run_returns_404(env):
    client, _ = env
    assert client.get("/api/runs/nope").status_code == 404


def test_skeleton_read_and_write(env):
    client, _ = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    client.app.state.lead_factory().start(run_id)
    body = client.get(f"/api/runs/{run_id}/skeleton").json()
    assert "Каркас" in body["content"]
    updated = body["content"] + "\n## Правка человека\n- уточнить тег\n"
    assert client.put(f"/api/runs/{run_id}/skeleton",
                      json={"content": updated}).status_code == 200
    assert "Правка человека" in client.get(f"/api/runs/{run_id}/skeleton").json()["content"]


def test_resume_blocked_without_gates(env):
    client, _ = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    client.app.state.lead_factory().start(run_id)
    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 400
    assert "запрещён" in response.json()["detail"]


def test_gates_then_resume_completes(env):
    client, tmp_path = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    client.app.state.lead_factory().start(run_id)
    assert client.post(f"/api/runs/{run_id}/approve-skeleton").status_code == 200
    assert client.post(f"/api/runs/{run_id}/execution-choice",
                       json={"choice": "generated"}).status_code == 200
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 200
    run = client.get(f"/api/runs/{run_id}").json()["run"]
    assert run["status"] == "done"
    assert run["verdict"] == "green"


def test_execution_choice_rejects_unknown(env):
    client, _ = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    response = client.post(f"/api/runs/{run_id}/execution-choice", json={"choice": "magic"})
    assert response.status_code == 400


def test_cancel_run(env):
    client, _ = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "cancelled"


def test_answer_unknown_question_returns_404(env):
    client, _ = env
    response = client.post("/api/runs/nope/answer", json={"question_id": "q", "text": "x"})
    assert response.status_code == 404


def test_stream_emits_sse_frames(env):
    client, _ = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": "C:/r", "branch": "b",
    }).json()["run"]["id"]
    client.app.state.lead_factory().start(run_id)
    client.app.state.bus.publish(run_id, {"type": "skeleton_ready", "path": "skeleton.md"})
    client.app.state.bus.close(run_id)
    with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    assert lines
    assert json.loads(lines[0][len("data: "):])["type"] == "skeleton_ready"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api_runs.py -v`
Expected: FAIL (missing endpoints, `app.state.bus`)

- [ ] **Step 3: Wire runs into `main.py`**

Add imports at the top of `studio2/backend/main.py`:
```python
import asyncio

from fastapi.responses import StreamingResponse

from events import EventBus
from pipeline.lead import GateError, PendingGate, QaLead
from store import RunStore
```

Inside `create_app`, after `mcp_registry = McpRegistry(mcp_store)`, add:
```python
    bus = EventBus()
    runs = RunStore(data_root)
    app.state.bus = bus
    app.state.runs = runs
    app.state.lead_factory = lambda: QaLead(
        doc_root, data_root, client(), bus, runs,
        config=load_config(data_root),
        rag=rag,
    )
```

Add these endpoints before `return app`:
```python
    def _lead() -> QaLead:
        return app.state.lead_factory()

    def _require_run(run_id: str) -> dict:
        run = runs.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"запуск {run_id} не найден")
        return run

    def _task_exists(task_id: str) -> bool:
        return (Path(doc_root) / "tasks" / f"{task_id}.md").exists()

    @app.post("/api/runs", status_code=201)
    def create_run(body: dict) -> dict:
        task_id = str(body.get("task_id") or "")
        repo_path = str(body.get("repo_path") or "")
        branch = str(body.get("branch") or "")
        if not _task_exists(task_id):
            raise HTTPException(400, f"тест-кейс {task_id or '(пусто)'} не найден в doc/tasks")
        if not repo_path or not branch:
            raise HTTPException(400, "нужны repo_path и branch")
        if not (Path(repo_path) / ".git").exists():
            raise HTTPException(400, f"путь не является git-репозиторием: {repo_path}")
        run = runs.create_run(task_id, repo_path, branch)
        return {"run": run}

    @app.get("/api/runs")
    def list_runs() -> dict:
        return {"runs": runs.list_runs()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return {"run": _require_run(run_id)}

    @app.get("/api/runs/{run_id}/stream")
    def stream(run_id: str) -> StreamingResponse:
        _require_run(run_id)

        async def generator():
            async for event in bus.subscribe(run_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(generator(), media_type="text/event-stream")

    @app.post("/api/runs/{run_id}/start")
    async def start_run(run_id: str) -> dict:
        _require_run(run_id)
        lead = _lead()
        try:
            await asyncio.to_thread(lead.start, run_id)
        except PendingGate:
            pass
        except Exception as exc:  # noqa: BLE001
            bus.publish(run_id, {"type": "error", "message": str(exc)})
            bus.close(run_id)
            raise HTTPException(400, str(exc)) from exc
        return {"run": runs.get_run(run_id)}

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str) -> dict:
        run = _require_run(run_id)
        try:
            _lead().ensure_gate("write", run)
            _lead().ensure_gate("provision", run)
        except GateError as exc:
            raise HTTPException(400, str(exc)) from exc
        await asyncio.to_thread(_lead().resume, run_id)
        return {"run": runs.get_run(run_id)}

    @app.get("/api/runs/{run_id}/skeleton")
    def get_skeleton(run_id: str) -> dict:
        run_dir = Path(doc_root) / "runs" / run_id
        path = run_dir / "skeleton.md"
        if not path.exists():
            raise HTTPException(404, "каркас ещё не сформирован")
        return {"path": "skeleton.md", "content": path.read_text(encoding="utf-8")}

    @app.put("/api/runs/{run_id}/skeleton")
    def put_skeleton(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(400, "каркас не может быть пустым")
        run_dir = Path(doc_root) / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "skeleton.md").write_text(content, encoding="utf-8")
        bus.publish(run_id, {"type": "skeleton_updated", "path": "skeleton.md",
                             "by": "human"})
        return {"path": "skeleton.md", "saved": True}

    @app.post("/api/runs/{run_id}/approve-skeleton")
    def approve_skeleton(run_id: str) -> dict:
        _require_run(run_id)
        return {"run": _lead().approve_skeleton(run_id, True)}

    @app.post("/api/runs/{run_id}/execution-choice")
    def execution_choice(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        try:
            run = _lead().choose_execution(run_id, body.get("choice", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"run": run}

    @app.post("/api/runs/{run_id}/answer")
    def answer(run_id: str, body: dict) -> dict:
        _require_run(run_id)
        question_id = body.get("question_id")
        if not bus.answer(question_id, str(body.get("text") or "")):
            raise HTTPException(404, "вопрос не найден или уже отвечен")
        bus.publish(run_id, {"type": "question_answered", "question_id": question_id})
        return {"answered": question_id}

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict:
        _require_run(run_id)
        _lead().cancel(run_id)
        return {"run": runs.get_run(run_id)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_runs.py -v`
Expected: 10 passed

- [ ] **Step 5: Run the whole backend suite**

Run: `python -m pytest -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add studio2/backend/main.py studio2/backend/tests/test_api_runs.py
git commit -m "feat(studio2): run endpoints, SSE stream and gate endpoints"
```


---

### Task 14: Frontend scaffold, API client and state

**Files:**
- Create: `studio2/frontend/package.json`
- Create: `studio2/frontend/tsconfig.json`
- Create: `studio2/frontend/tsconfig.app.json`
- Create: `studio2/frontend/tsconfig.node.json`
- Create: `studio2/frontend/vite.config.ts`
- Create: `studio2/frontend/index.html`
- Create: `studio2/frontend/src/main.tsx`
- Create: `studio2/frontend/src/api.ts`
- Create: `studio2/frontend/src/state.ts`
- Create: `studio2/frontend/src/styles.css`
- Create: `studio2/frontend/tests/setup.ts`
- Create: `studio2/frontend/tests/api.test.ts`
- Create: `studio2/frontend/tests/state.test.ts`

**Interfaces:**
- Produces `api.ts` functions: `getHealth`, `getConfig`, `saveConfig`, `getModels`, `testModel`, `getDocTree`, `readDoc`, `writeDoc`, `uploadDoc`, `deleteDoc`, `reindex`, `searchRag`, `getTasks`, `getMcpServers`, `addMcpServer`, `deleteMcpServer`, `connectMcpServer`, `disconnectMcpServer`, `getMcpTools`, `createRun`, `listRuns`, `getRun`, `startRun`, `resumeRun`, `getSkeleton`, `saveSkeleton`, `approveSkeleton`, `chooseExecution`, `cancelRun`, `answerQuestion`.
- Produces `createRunStream(runId, onEvent)` returning an `EventSource`.
- Produces `state.ts` reducer `pipelineReducer(state, action)` with actions `event`, `reset`, `selectTab`, `setError`, `clearError`.

- [ ] **Step 1: Write the failing tests**

`studio2/frontend/tests/setup.ts`:
```ts
import '@testing-library/jest-dom/vitest'
```

`studio2/frontend/tests/api.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiGet, apiPost, apiUrl } from '../src/api'

describe('api helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('builds urls against the api prefix', () => {
    expect(apiUrl('/health')).toBe('http://127.0.0.1:8100/api/health')
  })

  it('apiGet returns parsed json', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'ok' }), { status: 200 })))
    await expect(apiGet('/health')).resolves.toEqual({ status: 'ok' })
  })

  it('apiGet throws russian detail on error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'нет связи' }), { status: 400 })))
    await expect(apiGet('/health')).rejects.toThrow('нет связи')
  })

  it('apiPost sends json body', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ saved: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await apiPost('/doc/file', { path: 'kb/a.md', content: 'x' })
    expect(result).toEqual({ saved: true })
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ path: 'kb/a.md', content: 'x' })
  })
})
```

`studio2/frontend/tests/state.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { initialState, pipelineReducer, type PipelineState } from '../src/state'

function withState(patch: Partial<PipelineState>): PipelineState {
  return { ...initialState(), ...patch }
}

describe('pipelineReducer', () => {
  it('starts at sdd with empty artifacts', () => {
    const state = initialState()
    expect(state.stage).toBe('idle')
    expect(state.agents).toEqual([])
    expect(state.journal).toEqual([])
  })

  it('run_started sets stage sdd and clears journal', () => {
    const state = pipelineReducer(withState({ journal: [{ at: 'x', role: 'r', text: 't' }] }), {
      type: 'event',
      payload: { type: 'run_started', run_id: 'r1', task: 'TC-4217' },
    })
    expect(state.stage).toBe('sdd')
    expect(state.runId).toBe('r1')
    expect(state.journal).toEqual([])
  })

  it('stage_changed updates stage and round', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'stage_changed', stage: 'study', round: 1 },
    })
    expect(state.stage).toBe('study')
    expect(state.round).toBe(1)
  })

  it('agent_started adds agent with running status', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_started', role: 'researcher', model: 'qwen3.5:9b' },
    })
    expect(state.agents[0]).toMatchObject({ role: 'researcher', model: 'qwen3.5:9b', status: 'running' })
  })

  it('agent_delta appends text and a journal entry', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_delta', role: 'researcher', text: 'изучаю' },
    })
    expect(state.agents[0].text).toBe('изучаю')
    expect(state.journal.at(-1)?.role).toBe('researcher')
  })

  it('agent_done marks status', () => {
    const started = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'agent_started', role: 'reviewer', model: 'gemma4:12b' },
    })
    const done = pipelineReducer(started, {
      type: 'event',
      payload: { type: 'agent_done', role: 'reviewer', status: 'done', summary: 'green' },
    })
    expect(done.agents[0].status).toBe('done')
  })

  it('sdd_written records sdd files', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'sdd_written', role: 'testwriter', path: 'sdd/agent_testwriter.md', title: 'testwriter' },
    })
    expect(state.sddFiles).toEqual([{ role: 'testwriter', path: 'sdd/agent_testwriter.md' }])
  })

  it('skeleton_ready sets skeleton path', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'skeleton_ready', path: 'skeleton.md' },
    })
    expect(state.skeletonReady).toBe(true)
  })

  it('question stores pending question', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'question', question_id: 'q1', role: 'researcher', text: 'Какой экран?', options: ['A'] },
    })
    expect(state.question).toMatchObject({ id: 'q1', text: 'Какой экран?' })
  })

  it('question_answered clears question', () => {
    const asked = withState({ question: { id: 'q1', role: 'r', text: 't', options: [] } })
    const state = pipelineReducer(asked, {
      type: 'event',
      payload: { type: 'question_answered', question_id: 'q1' },
    })
    expect(state.question).toBeNull()
  })

  it('review_verdict stores verdict', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'review_verdict', verdict: 'green', path: 'review_report.md' },
    })
    expect(state.verdict).toBe('green')
  })

  it('run_done sets finished status', () => {
    const state = pipelineReducer(initialState(), {
      type: 'event',
      payload: { type: 'run_done', status: 'done', report_path: 'test_report.md' },
    })
    expect(state.status).toBe('done')
    expect(state.reportPath).toBe('test_report.md')
  })

  it('error sets message', () => {
    const state = pipelineReducer(initialState(), { type: 'error', message: 'нет связи' })
    expect(state.error).toBe('нет связи')
  })

  it('reset returns to initial state', () => {
    const dirty = withState({ stage: 'write', error: 'x' })
    expect(pipelineReducer(dirty, { type: 'reset' })).toEqual(initialState())
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `studio2/frontend`): `npm test`
Expected: FAIL — `Cannot find module '../src/state'`

- [ ] **Step 3: Write project config**

`studio2/frontend/package.json`:
```json
{
  "name": "studio2-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --port 5174",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "lint": "tsc -b --noEmit"
  },
  "dependencies": {
    "react": "^19.2.8",
    "react-dom": "^19.2.8"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^7.0.1",
    "@testing-library/react": "^16.3.3",
    "@types/react": "^19.2.18",
    "@types/react-dom": "^19.2.7",
    "@vitejs/plugin-react": "^6.1.1",
    "jsdom": "^29.1.1",
    "typescript": "~6.0.2",
    "vite": "^8.3.0",
    "vitest": "^5.0.1"
  }
}
```

`studio2/frontend/tsconfig.json`:
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

`studio2/frontend/tsconfig.app.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["vitest/globals"]
  },
  "include": ["src", "tests"]
}
```

`studio2/frontend/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true
  },
  "include": ["vite.config.ts"]
}
```

`studio2/frontend/vite.config.ts`:
```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8100', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './tests/setup.ts',
  },
})
```

`studio2/frontend/index.html`:
```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <title>QA-Lead Studio</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 4: Write the API client**

`studio2/frontend/src/api.ts`:
```ts
const BASE = 'http://127.0.0.1:8100/api'

export function apiUrl(path: string): string {
  return `${BASE}${path}`
}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `ошибка ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* тело без json */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export async function apiGet<T>(path: string): Promise<T> {
  return handle<T>(await fetch(apiUrl(path)))
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(apiUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }),
  )
}

export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(apiUrl(path), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
    }),
  )
}

export async function apiDelete<T>(path: string): Promise<T> {
  return handle<T>(await fetch(apiUrl(path), { method: 'DELETE' }))
}

export interface RoleConfig { provider: string; model: string; temperature: number }
export interface ProviderConfig { base_url: string | null; api_key_masked: string }
export interface StudioConfig {
  providers: Record<string, ProviderConfig>
  roles: Record<string, RoleConfig>
  critic: { must_differ_from_author: boolean }
  android: Record<string, string>
}

export const getHealth = () => apiGet<{ status: string; version: string }>('/health')
export const getConfig = () => apiGet<StudioConfig>('/config')
export const saveConfig = (patch: unknown) => apiPost<StudioConfig>('/config', patch)
export const getModels = (provider: string) =>
  apiGet<{ provider: string; models: string[] }>(`/models?provider=${provider}`)
export const testModel = (role: string) =>
  apiPost<{ ok: boolean; provider: string; model: string; error?: string }>('/models/test', { role })

export interface DocNode { name: string; type: 'dir' | 'file'; children: string[] }
export const getDocTree = () => apiGet<{ tree: DocNode[] }>('/doc/tree')
export const readDoc = (path: string) =>
  apiGet<{ path: string; content: string }>(`/doc/file?path=${encodeURIComponent(path)}`)
export const writeDoc = (path: string, content: string) =>
  apiPost<{ path: string; saved: boolean }>('/doc/file', { path, content })
export const deleteDoc = (path: string) =>
  apiDelete<{ deleted: string }>(`/doc/file?path=${encodeURIComponent(path)}`)
export async function uploadDoc(dir: string, file: File): Promise<{ path: string }> {
  const form = new FormData()
  form.append('dir', dir)
  form.append('file', file)
  return handle<{ path: string }>(await fetch(apiUrl('/doc/upload'), { method: 'POST', body: form }))
}
export const reindex = () =>
  apiPost<{ documents: number; stats: { documents: number; updated_at: string } }>('/rag/reindex')
export const searchRag = (q: string, limit = 5) =>
  apiGet<{ hits: { path: string; score: number; snippet: string }[] }>(
    `/rag/search?q=${encodeURIComponent(q)}&limit=${limit}`,
  )

export interface TaskCase { id: string; title: string; screen?: string; priority?: string; status: string }
export const getTasks = () => apiGet<{ tasks: TaskCase[] }>('/tasks')

export interface McpServer {
  id: string
  name: string
  type: 'stdio' | 'http'
  command: string[]
  url: string
  status: { state: string; tools_count: number }
}
export const getMcpServers = () => apiGet<{ servers: McpServer[] }>('/mcp/servers')
export const addMcpServer = (spec: unknown) => apiPost<{ server: McpServer }>('/mcp/servers', spec)
export const deleteMcpServer = (id: string) => apiDelete(`/mcp/servers/${id}`)
export const connectMcpServer = (id: string) => apiPost(`/mcp/servers/${id}/connect`)
export const disconnectMcpServer = (id: string) => apiPost(`/mcp/servers/${id}/disconnect`)
export const getMcpTools = () =>
  apiGet<{ tools: { server_id: string; name: string; description: string }[] }>('/mcp/tools')

export interface RunInfo {
  id: string
  task_id: string
  repo_path: string
  branch: string
  stage: string
  status: string
  round: number
  verdict: string | null
  skeleton_approved: boolean
  execution_choice: string | null
}
export const createRun = (task_id: string, repo_path: string, branch: string) =>
  apiPost<{ run: RunInfo }>('/runs', { task_id, repo_path, branch })
export const listRuns = () => apiGet<{ runs: RunInfo[] }>('/runs')
export const getRun = (id: string) => apiGet<{ run: RunInfo }>(`/runs/${id}`)
export const startRun = (id: string) => apiPost<{ run: RunInfo }>(`/runs/${id}/start`)
export const resumeRun = (id: string) => apiPost<{ run: RunInfo }>(`/runs/${id}/resume`)
export const getSkeleton = (id: string) =>
  apiGet<{ path: string; content: string }>(`/runs/${id}/skeleton`)
export const saveSkeleton = (id: string, content: string) =>
  apiPut<{ path: string; saved: boolean }>(`/runs/${id}/skeleton`, { content })
export const approveSkeleton = (id: string) => apiPost(`/runs/${id}/approve-skeleton`)
export const chooseExecution = (id: string, choice: 'as_loaded' | 'generated') =>
  apiPost(`/runs/${id}/execution-choice`, { choice })
export const cancelRun = (id: string) => apiPost(`/runs/${id}/cancel`)
export const answerQuestion = (id: string, question_id: string, text: string) =>
  apiPost(`/runs/${id}/answer`, { question_id, text })

export interface RunEvent {
  type: string
  [key: string]: unknown
}

export function createRunStream(runId: string, onEvent: (event: RunEvent) => void): EventSource {
  const source = new EventSource(apiUrl(`/runs/${runId}/stream`))
  source.onmessage = (message) => {
    if (message.data) onEvent(JSON.parse(message.data) as RunEvent)
  }
  return source
}
```

- [ ] **Step 5: Write the state reducer**

`studio2/frontend/src/state.ts`:
```ts
import type { RunEvent } from './api'

export type AgentStatus = 'running' | 'done' | 'failed' | 'idle'

export interface AgentRow {
  role: string
  model: string
  status: AgentStatus
  text: string
  summary: string
}

export interface JournalRow {
  at: string
  role: string
  text: string
}

export interface SkeletonFile {
  role: string
  path: string
}

export interface PendingQuestion {
  id: string
  role: string
  text: string
  options: string[]
}

export interface PipelineState {
  runId: string | null
  task: string
  stage: string
  round: number
  status: string
  verdict: string | null
  reportPath: string
  agents: AgentRow[]
  sddFiles: SkeletonFile[]
  skeletonReady: boolean
  executionChosen: string | null
  question: PendingQuestion | null
  journal: JournalRow[]
  error: string | null
}

export type Action =
  | { type: 'event'; payload: RunEvent }
  | { type: 'reset' }
  | { type: 'error'; message: string }

export function initialState(): PipelineState {
  return {
    runId: null,
    task: '',
    stage: 'idle',
    round: 1,
    status: 'idle',
    verdict: null,
    reportPath: '',
    agents: [],
    sddFiles: [],
    skeletonReady: false,
    executionChosen: null,
    question: null,
    journal: [],
    error: null,
  }
}

function stamp(): string {
  return new Date().toLocaleTimeString('ru-RU', { hour12: false })
}

function withAgent(agents: AgentRow[], role: string): AgentRow[] {
  return agents.some((agent) => agent.role === role)
    ? agents
    : [...agents, { role, model: '', status: 'idle', text: '', summary: '' }]
}

export function pipelineReducer(state: PipelineState, action: Action): PipelineState {
  if (action.type === 'reset') return initialState()
  if (action.type === 'error') return { ...state, error: action.message }

  const event = action.payload
  switch (event.type) {
    case 'run_started':
      return {
        ...initialState(),
        runId: String(event.run_id ?? state.runId ?? ''),
        task: String(event.task ?? ''),
        stage: 'sdd',
        status: 'running',
      }
    case 'stage_changed':
      return {
        ...state,
        stage: String(event.stage ?? state.stage),
        round: Number(event.round ?? state.round),
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `стадия: ${event.stage}` }],
      }
    case 'agent_started': {
      const role = String(event.role ?? '')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role ? { ...agent, model: String(event.model ?? ''), status: 'running' as AgentStatus } : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: 'старт' }] }
    }
    case 'agent_delta': {
      const role = String(event.role ?? '')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role ? { ...agent, text: agent.text + String(event.text ?? '') } : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: String(event.text ?? '') }] }
    }
    case 'agent_done': {
      const role = String(event.role ?? '')
      const status = String(event.status ?? 'done')
      const agents = withAgent(state.agents, role).map((agent) =>
        agent.role === role
          ? { ...agent, status: (status === 'fail' ? 'failed' : 'done') as AgentStatus, summary: String(event.summary ?? '') }
          : agent,
      )
      return { ...state, agents, journal: [...state.journal, { at: stamp(), role, text: `завершён: ${status}` }] }
    }
    case 'sdd_written':
      return {
        ...state,
        sddFiles: [...state.sddFiles, { role: String(event.role), path: String(event.path) }],
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `SDD: ${event.path}` }],
      }
    case 'skeleton_ready':
      return { ...state, skeletonReady: true, journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: 'каркас готов' }] }
    case 'execution_chosen':
      return { ...state, executionChosen: String(event.choice) }
    case 'review_verdict':
      return { ...state, verdict: String(event.verdict) }
    case 'question':
      return {
        ...state,
        question: {
          id: String(event.question_id),
          role: String(event.role ?? ''),
          text: String(event.text ?? ''),
          options: (event.options as string[]) ?? [],
        },
      }
    case 'question_answered':
      return { ...state, question: null }
    case 'run_done':
      return {
        ...state,
        status: String(event.status ?? 'done'),
        reportPath: String(event.report_path ?? ''),
        journal: [...state.journal, { at: stamp(), role: 'qa-lead', text: `итог: ${event.status}` }],
      }
    case 'error':
      return { ...state, error: String(event.message ?? 'ошибка'), status: 'failed' }
    default:
      return state
  }
}
```

- [ ] **Step 6: Write the palette**

`studio2/frontend/src/styles.css`:
```css
:root {
  --bg: #0f1115;
  --panel: #151922;
  --line: #232935;
  --text: #d8dee9;
  --muted: #7b8794;
  --accent: #4ea1ff;
  --ok: #3fb950;
  --warn: #d29922;
  --err: #f85149;
  --radius: 6px;
  --font: 'JetBrains Mono', ui-monospace, monospace;
  --font-ui: 'Inter', system-ui, sans-serif;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  font-size: 14px;
}

.app { display: grid; grid-template-rows: 48px 1fr; height: 100vh; }
.header { display: flex; align-items: center; gap: 16px; padding: 0 16px; border-bottom: 1px solid var(--line); background: var(--panel); }
.header .brand { font-family: var(--font); letter-spacing: 0.08em; color: var(--accent); }
.header .spacer { flex: 1; }
.columns { display: grid; grid-template-columns: 190px 1fr 340px; min-height: 0; }
.sidebar { border-right: 1px solid var(--line); background: var(--panel); padding: 8px 0; }
.sidebar button { display: flex; width: 100%; gap: 8px; padding: 8px 16px; background: none; border: 0; border-left: 2px solid transparent; color: var(--muted); font: inherit; text-align: left; cursor: pointer; }
.sidebar button.active { color: var(--text); border-left-color: var(--accent); }
.main { padding: 16px; overflow: auto; min-width: 0; }
.inspector { border-left: 1px solid var(--line); background: var(--panel); padding: 16px; overflow: auto; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin: 0 0 12px; }
table { width: 100%; border-collapse: collapse; font-family: var(--font); font-size: 12px; }
th { text-align: left; color: var(--muted); font-weight: 500; padding: 6px 8px; border-bottom: 1px solid var(--line); }
td { padding: 6px 8px; border-bottom: 1px solid var(--line); }
button { font: inherit; color: var(--text); background: #1c2230; border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 12px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button:disabled { opacity: 0.45; cursor: not-allowed; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; }
.dot.running { background: var(--accent); animation: pulse 1.2s infinite; }
.dot.done { background: var(--ok); }
.dot.failed { background: var(--err); }
.dot.idle { background: var(--muted); }
@keyframes pulse { 50% { opacity: 0.3; } }
.stages { display: flex; gap: 6px; margin: 16px 0; font-family: var(--font); font-size: 11px; flex-wrap: wrap; }
.stage { padding: 4px 8px; border: 1px solid var(--line); border-radius: var(--radius); color: var(--muted); }
.stage.current { border-color: var(--accent); color: var(--accent); }
.stage.passed { border-color: var(--ok); color: var(--ok); }
.journal { font-family: var(--font); font-size: 12px; white-space: pre-wrap; }
.journal div { padding: 2px 0; border-bottom: 1px solid #1a1f29; }
.notice { padding: 8px 12px; border-left: 3px solid var(--warn); background: #1e1a12; margin-bottom: 12px; }
.notice.err { border-left-color: var(--err); background: #241417; }
.hint { color: var(--muted); }
textarea, input, select { width: 100%; background: #10141b; color: var(--text); border: 1px solid var(--line); border-radius: var(--radius); padding: 8px; font-family: var(--font); font-size: 12px; }
textarea { resize: vertical; min-height: 120px; }
.row { display: flex; gap: 8px; align-items: center; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 12px; margin-bottom: 16px; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); display: flex; align-items: center; justify-content: center; }
.modal { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px; width: min(560px, 90vw); }
```

- [ ] **Step 7: Write the entry point**

`studio2/frontend/src/main.tsx`:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

Create a placeholder `studio2/frontend/src/App.tsx` (replaced in Task 15):
```tsx
export default function App() {
  return <div className="app">QA-Lead Studio</div>
}
```

- [ ] **Step 8: Run tests and typecheck**

Run: `npm install` (first time only), then `npm test`
Expected: 18 passed

Run: `npx tsc -b --noEmit`
Expected: no errors

- [ ] **Step 9: Commit**

```bash
git add studio2/frontend
git commit -m "feat(studio2): frontend scaffold, API client and pipeline reducer"
```

---

### Task 15: UI screens — models, RAG, MCP, tasks, pipeline, gates

**Files:**
- Modify: `studio2/frontend/src/App.tsx`
- Create: `studio2/frontend/src/components/Header.tsx`
- Create: `studio2/frontend/src/components/Sidebar.tsx`
- Create: `studio2/frontend/src/components/Inspector.tsx`
- Create: `studio2/frontend/src/components/ModelsTab.tsx`
- Create: `studio2/frontend/src/components/RagTab.tsx`
- Create: `studio2/frontend/src/components/McpTab.tsx`
- Create: `studio2/frontend/src/components/TasksTab.tsx`
- Create: `studio2/frontend/src/components/PipelineTab.tsx`
- Create: `studio2/frontend/src/components/RunsTab.tsx`
- Create: `studio2/frontend/src/components/QuestionModal.tsx`
- Create: `studio2/frontend/src/components/SkeletonEditor.tsx`
- Create: `studio2/frontend/tests/models-tab.test.tsx`
- Create: `studio2/frontend/tests/pipeline-tab.test.tsx`

**Interfaces:**
- Consumes: `api.ts` (Task 14) and `state.ts` (Task 14).
- Produces: `App` with tabs `models | rag | mcp | tasks | pipeline | runs`, a right-side `Inspector`, and a blocking `QuestionModal`.

- [ ] **Step 1: Write the failing component tests**

`studio2/frontend/tests/models-tab.test.tsx`:
```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ModelsTab from '../src/components/ModelsTab'

const CONFIG = {
  providers: {
    ollama: { base_url: 'http://localhost:11435', api_key_masked: '' },
    gpustack: { base_url: 'https://gpustack/v1', api_key_masked: 'gpus***7890' },
    fake: { base_url: null, api_key_masked: '' },
  },
  roles: {
    'qa-lead': { provider: 'ollama', model: 'qwen3-coder:30b', temperature: 0.2 },
    testwriter: { provider: 'ollama', model: 'qwen3-coder:30b', temperature: 0.4 },
    reviewer: { provider: 'ollama', model: 'gemma4:12b', temperature: 0.1 },
  },
  critic: { must_differ_from_author: true },
  android: {},
}

describe('ModelsTab', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders a row per role with provider and model', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText('reviewer')).toBeInTheDocument())
    expect(screen.getByDisplayValue('gemma4:12b')).toBeInTheDocument()
    expect(screen.getByText('qa-lead')).toBeInTheDocument()
  })

  it('masks provider keys', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(CONFIG), { status: 200 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText(/gpus\*\*\*7890/)).toBeInTheDocument())
  })

  it('shows an error when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'нет связи с сервером' }), { status: 400 })))
    render(<ModelsTab />)
    await waitFor(() => expect(screen.getByText(/нет связи с сервером/)).toBeInTheDocument())
  })
})
```

`studio2/frontend/tests/pipeline-tab.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import PipelineTab from '../src/components/PipelineTab'
import { initialState } from '../src/state'

const base = {
  ...initialState(),
}

describe('PipelineTab', () => {
  it('renders all stages', () => {
    render(<PipelineTab state={base} dispatch={vi.fn()} />)
    for (const stage of ['sdd', 'study', 'skeleton', 'write', 'provision', 'execute', 'debug', 'review', 'done']) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0)
    }
  })

  it('shows agent rows with models', () => {
    const state = {
      ...base,
      agents: [
        { role: 'testwriter', model: 'qwen3-coder:30b', status: 'done' as const, text: '', summary: '' },
        { role: 'reviewer', model: 'gemma4:12b', status: 'running' as const, text: '', summary: '' },
      ],
    }
    render(<PipelineTab state={state} dispatch={vi.fn()} />)
    expect(screen.getByText('qwen3-coder:30b')).toBeInTheDocument()
    expect(screen.getByText('gemma4:12b')).toBeInTheDocument()
  })

  it('shows skeleton gate actions when ready', () => {
    render(<PipelineTab state={{ ...base, skeletonReady: true, runId: 'r1' }} dispatch={vi.fn()} />)
    expect(screen.getByText('Утвердить каркас')).toBeInTheDocument()
    expect(screen.getByText('Выбрать способ исполнения')).toBeInTheDocument()
  })

  it('does not show gates without a skeleton', () => {
    render(<PipelineTab state={base} dispatch={vi.fn()} />)
    expect(screen.queryByText('Утвердить каркас')).toBeNull()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test`
Expected: FAIL — cannot resolve `../src/components/ModelsTab`

- [ ] **Step 3: Write the components**

`studio2/frontend/src/components/Sidebar.tsx`:
```tsx
const TABS = [
  ['models', 'Модели'],
  ['rag', 'RAG'],
  ['mcp', 'MCP'],
  ['tasks', 'Задачи'],
  ['pipeline', 'Pipeline'],
  ['runs', 'Запуски'],
] as const

export type TabId = (typeof TABS)[number][0]

export default function Sidebar({ active, onChange }: { active: TabId; onChange: (tab: TabId) => void }) {
  return (
    <nav className="sidebar">
      {TABS.map(([id, label]) => (
        <button key={id} className={id === active ? 'active' : ''} onClick={() => onChange(id)}>
          {label}
        </button>
      ))}
    </nav>
  )
}
```

`studio2/frontend/src/components/Header.tsx`:
```tsx
export default function Header({
  task,
  stage,
  running,
  onStart,
}: {
  task: string
  stage: string
  running: boolean
  onStart: () => void
}) {
  return (
    <header className="header">
      <span className="brand">QA-LEAD</span>
      <span className="hint">{task || 'задача не выбрана'}</span>
      <span className="hint">стадия: {stage}</span>
      <span className="spacer" />
      <button onClick={onStart} disabled={running}>
        {running ? 'Выполняется…' : '▶ Запустить'}
      </button>
    </header>
  )
}
```

`studio2/frontend/src/components/ModelsTab.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { getConfig, saveConfig, testModel, type StudioConfig } from '../api'

export default function ModelsTab() {
  const [config, setConfig] = useState<StudioConfig | null>(null)
  const [error, setError] = useState('')
  const [checks, setChecks] = useState<Record<string, boolean>>({})

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((exc: Error) => setError(exc.message))
  }, [])

  const update = async (role: string, field: 'provider' | 'model', value: string) => {
    if (!config) return
    const next = { ...config, roles: { ...config.roles, [role]: { ...config.roles[role], [field]: value } } }
    setConfig(next)
    try {
      setConfig(await saveConfig({ roles: { [role]: { [field]: value } } }))
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const check = async (role: string) => {
    const result = await testModel(role)
    setChecks((prev) => ({ ...prev, [role]: result.ok }))
  }

  if (!config) return <div className="hint">{error || 'загрузка…'}</div>

  return (
    <div>
      <h2>Модели по ролям</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead>
          <tr><th>Роль</th><th>Провайдер</th><th>Модель</th><th>Статус</th><th /></tr>
        </thead>
        <tbody>
          {Object.entries(config.roles).map(([role, spec]) => (
            <tr key={role}>
              <td>{role}</td>
              <td>
                <select value={spec.provider} onChange={(event) => update(role, 'provider', event.target.value)}>
                  {Object.keys(config.providers).map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </td>
              <td>
                <input value={spec.model} onChange={(event) => update(role, 'model', event.target.value)} />
              </td>
              <td>{checks[role] === undefined ? '—' : checks[role] ? 'ok' : 'ошибка'}</td>
              <td><button onClick={() => check(role)}>Проверить</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ marginTop: 24 }}>Провайдеры</h2>
      <table>
        <thead><tr><th>Провайдер</th><th>base_url</th><th>Ключ</th></tr></thead>
        <tbody>
          {Object.entries(config.providers).map(([name, provider]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{provider.base_url ?? '—'}</td>
              <td>{provider.api_key_masked || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

`studio2/frontend/src/components/RagTab.tsx`:
```tsx
import { useCallback, useEffect, useState } from 'react'
import { deleteDoc, getDocTree, readDoc, reindex, uploadDoc, writeDoc, type DocNode } from '../api'

const EDITABLE = ['kb', 'tasks', 'agents', 'templates']

export default function RagTab() {
  const [tree, setTree] = useState<DocNode[]>([])
  const [path, setPath] = useState('')
  const [content, setContent] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setTree((await getDocTree()).tree)
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const open = async (file: string) => {
    setPath(file)
    try {
      setContent((await readDoc(file)).content)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const save = async () => {
    try {
      await writeDoc(path, content)
      setStatus(`сохранено: ${path}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const upload = async (dir: string, file: File) => {
    try {
      const result = await uploadDoc(dir, file)
      setStatus(`загружено: ${result.path}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const remove = async (file: string) => {
    try {
      await deleteDoc(file)
      setStatus(`удалено: ${file}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const doReindex = async () => {
    const result = await reindex()
    setStatus(`проиндексировано документов: ${result.documents}`)
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <button onClick={doReindex}>Переиндексировать</button>
        <label className="hint">
          Загрузить в kb
          <input type="file" onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void upload('kb', file)
          }} />
        </label>
        <label className="hint">
          Загрузить в tasks
          <input type="file" onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void upload('tasks', file)
          }} />
        </label>
      </div>
      {error && <div className="notice err">{error}</div>}
      {status && <div className="notice">{status}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16 }}>
        <div>
          {tree.map((node) => (
            <div key={node.name}>
              <h2>{node.name}</h2>
              {node.children.map((file) => (
                <div key={file} className="row">
                  <button onClick={() => open(file)}>{file}</button>
                  {['kb/', 'tasks/'].some((prefix) => file.startsWith(prefix)) && (
                    <button onClick={() => remove(file)}>×</button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div>
          <h2>{path || 'выберите файл'}</h2>
          <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={20} />
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={save} disabled={!path || !EDITABLE.some((dir) => path.startsWith(`${dir}/`))}>
              Сохранить
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
```

`studio2/frontend/src/components/McpTab.tsx`:
```tsx
import { useCallback, useEffect, useState } from 'react'
import {
  addMcpServer, connectMcpServer, deleteMcpServer, disconnectMcpServer,
  getMcpServers, type McpServer,
} from '../api'

export default function McpTab() {
  const [servers, setServers] = useState<McpServer[]>([])
  const [name, setName] = useState('allure')
  const [type, setType] = useState<'stdio' | 'http'>('stdio')
  const [command, setCommand] = useState('')
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setServers((await getMcpServers()).servers)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn()
      setError('')
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  return (
    <div>
      <h2>MCP-серверы</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>Имя</th><th>Тип</th><th>Статус</th><th>Инструменты</th><th /></tr></thead>
        <tbody>
          {servers.map((server) => (
            <tr key={server.id}>
              <td>{server.name}</td>
              <td>{server.type}</td>
              <td>{server.status.state}</td>
              <td>{server.status.tools_count}</td>
              <td>
                <button onClick={() => act(() => connectMcpServer(server.id))}>Подключить</button>
                <button onClick={() => act(() => disconnectMcpServer(server.id))}>Отключить</button>
                <button onClick={() => act(() => deleteMcpServer(server.id))}>Удалить</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="row">
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="имя" />
          <select value={type} onChange={(event) => setType(event.target.value as 'stdio' | 'http')}>
            <option value="stdio">stdio</option>
            <option value="http">http</option>
          </select>
        </div>
        {type === 'stdio' ? (
          <input value={command} onChange={(event) => setCommand(event.target.value)}
                 placeholder="python -m allure_mcp" style={{ marginTop: 8 }} />
        ) : (
          <input value={url} onChange={(event) => setUrl(event.target.value)}
                 placeholder="https://allure.lmru.tech/api" style={{ marginTop: 8 }} />
        )}
        <button style={{ marginTop: 8 }} onClick={() => act(() => addMcpServer(
          type === 'stdio'
            ? { name, type, command: command.split(/\s+/).filter(Boolean) }
            : { name, type, url },
        ))}>
          Добавить
        </button>
      </div>
    </div>
  )
}
```

`studio2/frontend/src/components/TasksTab.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { getTasks, type TaskCase } from '../api'

export default function TasksTab({ onSelect }: { onSelect: (task: TaskCase) => void }) {
  const [tasks, setTasks] = useState<TaskCase[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    getTasks()
      .then((result) => setTasks(result.tasks))
      .catch((exc: Error) => setError(exc.message))
  }, [])

  return (
    <div>
      <h2>Тест-кейсы</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>ID</th><th>Название</th><th>Экран</th><th>Приоритет</th><th>Статус</th><th /></tr></thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.id}>
              <td>{task.id}</td>
              <td>{task.title}</td>
              <td>{task.screen ?? '—'}</td>
              <td>{task.priority ?? '—'}</td>
              <td>{task.status}</td>
              <td><button onClick={() => onSelect(task)}>Выбрать</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

`studio2/frontend/src/components/PipelineTab.tsx`:
```tsx
import type { Dispatch } from 'react'
import type { Action, PipelineState } from '../state'

const STAGES = ['sdd', 'study', 'skeleton', 'write', 'provision', 'execute', 'debug', 'review', 'done']

export default function PipelineTab({
  state,
  dispatch,
  onApprove,
  onChooseExecution,
}: {
  state: PipelineState
  dispatch: Dispatch<Action>
  onApprove?: () => void
  onChooseExecution?: (choice: 'as_loaded' | 'generated') => void
}) {
  const currentIndex = STAGES.indexOf(state.stage)

  return (
    <div>
      {state.error && <div className="notice err">{state.error}</div>}
      <h2>SDD</h2>
      <div className="panel">
        {state.sddFiles.length === 0
          ? <span className="hint">SDD пока не сформированы</span>
          : state.sddFiles.map((file) => (
            <div key={file.path} className="journal"><span>{file.path}</span></div>
          ))}
      </div>

      <h2>Стадии</h2>
      <div className="stages">
        {STAGES.map((stage, index) => (
          <span key={stage}
                className={`stage ${stage === state.stage ? 'current' : ''} ${index < currentIndex ? 'passed' : ''}`}>
            {stage}
          </span>
        ))}
      </div>

      {state.skeletonReady && (
        <div className="panel">
          <h2>Гейты</h2>
          <div className="row">
            <button onClick={onApprove}>Утвердить каркас</button>
            <button onClick={() => onChooseExecution?.('as_loaded')}>Выбрать способ исполнения</button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={() => onChooseExecution?.('generated')}>Исполнить сгенерированный</button>
            {state.executionChosen && <span className="hint">выбрано: {state.executionChosen}</span>}
          </div>
        </div>
      )}

      <h2>Протокол</h2>
      <table>
        <thead><tr><th>Роль</th><th>Модель</th><th>Статус</th><th>Итог</th></tr></thead>
        <tbody>
          {state.agents.map((agent) => (
            <tr key={agent.role}>
              <td><span className={`dot ${agent.status}`} /> {agent.role}</td>
              <td>{agent.model}</td>
              <td>{agent.status}</td>
              <td>{agent.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ marginTop: 16 }}>Журнал</h2>
      <div className="journal">
        {state.journal.slice(-80).map((row, index) => (
          <div key={index}>{row.at} [{row.role}] {row.text}</div>
        ))}
      </div>
    </div>
  )
}
```

`studio2/frontend/src/components/RunsTab.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { listRuns, type RunInfo } from '../api'

export default function RunsTab({ onOpen }: { onOpen: (run: RunInfo) => void }) {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    listRuns()
      .then((result) => setRuns(result.runs))
      .catch((exc: Error) => setError(exc.message))
  }, [])

  return (
    <div>
      <h2>История запусков</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead><tr><th>Run</th><th>ТК</th><th>Ветка</th><th>Вердикт</th><th>Статус</th><th /></tr></thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>{run.id}</td>
              <td>{run.task_id}</td>
              <td>{run.branch}</td>
              <td>{run.verdict ?? '—'}</td>
              <td>{run.status}</td>
              <td><button onClick={() => onOpen(run)}>Открыть</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

`studio2/frontend/src/components/Inspector.tsx`:
```tsx
import type { PipelineState } from '../state'

export default function Inspector({ state }: { state: PipelineState }) {
  const active = state.agents.find((agent) => agent.status === 'running') ?? state.agents.at(-1)

  return (
    <aside className="inspector">
      <h2>Инспектор</h2>
      {state.verdict && (
        <div className="panel">
          <h2>Вердикт</h2>
          <span className={`dot ${state.verdict === 'green' ? 'done' : 'failed'}`} /> {state.verdict}
        </div>
      )}
      {state.reportPath && (
        <div className="panel">
          <h2>Отчёт</h2>
          <span className="hint">{state.reportPath}</span>
        </div>
      )}
      <div className="panel">
        <h2>Текущий агент</h2>
        {active ? (
          <>
            <div className="row"><span className={`dot ${active.status}`} /> {active.role}</div>
            <div className="hint">{active.model}</div>
            <pre className="journal">{active.text.slice(-1200)}</pre>
          </>
        ) : <span className="hint">агент не запущен</span>}
      </div>
    </aside>
  )
}
```

`studio2/frontend/src/components/QuestionModal.tsx`:
```tsx
import { useState } from 'react'
import type { PendingQuestion } from '../state'

export default function QuestionModal({
  question,
  onSubmit,
}: {
  question: PendingQuestion
  onSubmit: (text: string) => void
}) {
  const [answer, setAnswer] = useState('')

  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h2>Вопрос от {question.role}</h2>
        <p>{question.text}</p>
        {question.options.map((option, index) => (
          <div key={option} className="row">
            <button onClick={() => onSubmit(option)}>{index + 1}. {option}</button>
          </div>
        ))}
        <textarea value={answer} onChange={(event) => setAnswer(event.target.value)}
                  placeholder="Свободный ответ" />
        <div className="row" style={{ marginTop: 8 }}>
          <button onClick={() => onSubmit(answer)} disabled={!answer.trim()}>Ответить</button>
        </div>
      </div>
    </div>
  )
}
```

`studio2/frontend/src/components/SkeletonEditor.tsx`:
```tsx
import { useEffect, useState } from 'react'
import { getSkeleton, saveSkeleton } from '../api'

export default function SkeletonEditor({
  runId,
  onApproved,
}: {
  runId: string
  onApproved: () => void
}) {
  const [content, setContent] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  useEffect(() => {
    getSkeleton(runId)
      .then((result) => setContent(result.content))
      .catch((exc: Error) => setError(exc.message))
  }, [runId])

  const save = async () => {
    try {
      await saveSkeleton(runId, content)
      setStatus('каркас сохранён')
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  return (
    <div className="panel">
      <h2>Каркас теста</h2>
      {error && <div className="notice err">{error}</div>}
      {status && <div className="notice">{status}</div>}
      <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={18} />
      <div className="row" style={{ marginTop: 8 }}>
        <button onClick={save}>Сохранить каркас</button>
        <button onClick={onApproved}>Утвердить и реализовать</button>
      </div>
    </div>
  )
}
```

`studio2/frontend/src/App.tsx`:
```tsx
import { useReducer, useState } from 'react'
import {
  approveSkeleton, cancelRun, chooseExecution, createRun, createRunStream, resumeRun, startRun,
  answerQuestion, type RunInfo,
} from './api'
import Header from './components/Header'
import Inspector from './components/Inspector'
import McpTab from './components/McpTab'
import ModelsTab from './components/ModelsTab'
import PipelineTab from './components/PipelineTab'
import QuestionModal from './components/QuestionModal'
import RagTab from './components/RagTab'
import RunsTab from './components/RunsTab'
import Sidebar, { type TabId } from './components/Sidebar'
import SkeletonEditor from './components/SkeletonEditor'
import TasksTab from './components/TasksTab'
import { initialState, pipelineReducer } from './state'

const DEFAULT_REPO = 'C:\\Users\\migor\\StudioProjects\\lmru--mobile-customer--app'

export default function App() {
  const [tab, setTab] = useState<TabId>('pipeline')
  const [state, dispatch] = useReducer(pipelineReducer, undefined, initialState)
  const [taskId, setTaskId] = useState('')
  const [branch, setBranch] = useState('develop')
  const [stream, setStream] = useState<EventSource | null>(null)

  const follow = (runId: string) => {
    stream?.close()
    setStream(createRunStream(runId, (event) => dispatch({ type: 'event', payload: event })))
  }

  const onStart = async () => {
    try {
      const { run } = await createRun(taskId, DEFAULT_REPO, branch)
      follow(run.id)
      await startRun(run.id)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const onApprove = async () => {
    if (!state.runId) return
    try {
      await approveSkeleton(state.runId)
      await chooseExecution(state.runId, 'generated')
      await resumeRun(state.runId)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const onChoose = async (choice: 'as_loaded' | 'generated') => {
    if (!state.runId) return
    try {
      await chooseExecution(state.runId, choice)
    } catch (exc) {
      dispatch({ type: 'error', message: (exc as Error).message })
    }
  }

  const openRun = (run: RunInfo) => {
    setTaskId(run.task_id)
    setBranch(run.branch)
    dispatch({ type: 'reset' })
    follow(run.id)
  }

  return (
    <div className="app">
      <Header task={taskId} stage={state.stage} running={state.status === 'running'} onStart={onStart} />
      <div className="columns">
        <Sidebar active={tab} onChange={setTab} />
        <main className="main">
          <div className="panel row">
            <span className="hint">ТК</span>
            <input value={taskId} onChange={(event) => setTaskId(event.target.value)} placeholder="TC-4217" />
            <span className="hint">ветка</span>
            <input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="develop" />
            {state.runId && <button onClick={() => cancelRun(state.runId)}>Отменить</button>}
          </div>
          {tab === 'models' && <ModelsTab />}
          {tab === 'rag' && <RagTab />}
          {tab === 'mcp' && <McpTab />}
          {tab === 'tasks' && <TasksTab onSelect={(task) => { setTaskId(task.id); setTab('pipeline') }} />}
          {tab === 'runs' && <RunsTab onOpen={openRun} />}
          {tab === 'pipeline' && (
            <>
              <PipelineTab state={state} dispatch={dispatch} onApprove={onApprove} onChooseExecution={onChoose} />
              {state.runId && state.skeletonReady && (
                <SkeletonEditor runId={state.runId} onApproved={onApprove} />
              )}
            </>
          )}
        </main>
        <Inspector state={state} />
      </div>
      {state.question && state.runId && (
        <QuestionModal
          question={state.question}
          onSubmit={(text) => {
            void answerQuestion(state.runId as string, state.question!.id, text)
            dispatch({ type: 'event', payload: { type: 'question_answered', question_id: state.question!.id } })
          }}
        />
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `npm test`
Expected: all tests pass

Run: `npx tsc -b --noEmit`
Expected: no errors

- [ ] **Step 5: Build**

Run: `npm run build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
git add studio2/frontend
git commit -m "feat(studio2): dashboard screens for models, rag, mcp, tasks and pipeline"
```


---

### Task 16: End-to-end script, README and release notes

**Files:**
- Create: `studio2/scripts/e2e_day35.py`
- Create: `studio2/README.md`
- Modify: `RELEASE.md` (prepend day 35 section)
- Modify: `README.md` (add day 35 row)
- Modify: `LINKS.md` (add branch and artifacts)

**Interfaces:**
- Produces `python studio2/scripts/e2e_day35.py` — deterministic end-to-end check on `fake` LLM; prints `PASS`/`FAIL` per check and exits `0`/`1`.

- [ ] **Step 1: Write the failing e2e script**

`studio2/scripts/e2e_day35.py`:
```python
"""E2E проверка studio2 на fake-LLM: пайплайн, гейты, инварианты, артефакты."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDIO2 = ROOT / "studio2"
sys.path.insert(0, str(STUDIO2 / "backend"))

from config import DEFAULT_CONFIG  # noqa: E402
from llm import LlmClient  # noqa: E402
from pipeline.lead import (CriticSameModel, GateError, PendingGate,  # noqa: E402
                           QaLead, STAGES)
from store import RunStore  # noqa: E402


class NullBus:
    def __init__(self):
        self.events: list[dict] = []

    def publish(self, run_id, event):
        self.events.append(event)

    def close(self, run_id):
        pass


PASSED = 0
FAILED = 0


def check(name: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="studio2-e2e-"))
    try:
        doc = tmp / "doc"
        shutil.copytree(STUDIO2 / "doc", doc)
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        for role in cfg["roles"]:
            cfg["roles"][role]["provider"] = "fake"

        store = RunStore(str(tmp / "data"))
        bus = NullBus()
        lead = QaLead(str(doc), str(tmp / "data"), LlmClient(cfg), bus, store, config=cfg)
        run = store.create_run("TC-4217", str(STUDIO2), "develop")
        run_id = run["id"]

        check("этапы заданы в порядке", STAGES[0] == "sdd" and STAGES[-1] == "done")

        try:
            lead.start(run_id)
            check("старт останавливается на гейте", False)
        except PendingGate:
            check("старт останавливается на гейте", True)

        sdd_files = {p.name for p in (doc / "runs" / run_id / "sdd").glob("*.md")}
        check("SDD созданы на каждого субагента",
              {"agent_qa-lead.md", "agent_researcher.md", "agent_testwriter.md",
               "agent_reviewer.md"} <= sdd_files)
        check("каркас записан", (doc / "runs" / run_id / "skeleton.md").exists())
        check("SSE-события сгенерированы",
              any(e["type"] == "sdd_written" for e in bus.events))

        try:
            lead.resume(run_id)
            check("resume без гейтов запрещён", False)
        except GateError:
            check("resume без гейтов запрещён", True)

        lead.approve_skeleton(run_id)
        lead.choose_execution(run_id, "generated")
        lead.resume(run_id)

        final = store.get_run(run_id)
        check("запуск завершён", final["status"] == "done")
        check("вердикт выставлен", final["verdict"] == "green")
        check("итоговый отчёт есть", (doc / "runs" / run_id / "test_report.md").exists())
        check("отчёт ревьюера есть", (doc / "runs" / run_id / "review_report.md").exists())
        check("сгенерированный тест лежит в test_generated",
              any((doc / "runs" / run_id / "test_generated").glob("*.kt")))

        bad = json.loads(json.dumps(DEFAULT_CONFIG))
        bad["roles"]["reviewer"]["model"] = bad["roles"]["testwriter"]["model"]
        bad_lead = QaLead(str(doc), str(tmp / "data"), LlmClient(bad), NullBus(), store, config=bad)
        try:
            bad_lead.validate_critic()
            check("ревьюер не может быть моделью тестировщика", False)
        except CriticSameModel:
            check("ревьюер не может быть моделью тестировщика", True)

        try:
            lead.validate_transition("done", "study")
            check("переход назад запрещён", False)
        except Exception:  # noqa: BLE001
            check("переход назад запрещён", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} PASS, {FAILED} FAIL")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the e2e script**

Run (from repo root): `python studio2/scripts/e2e_day35.py`
Expected: `11 PASS, 0 FAIL` (exit code 0). If any FAIL, fix the implementation before continuing.

- [ ] **Step 3: Write the README**

`studio2/README.md`:
```markdown
# studio2 — QA-Lead Orchestrator

Автономный QA-пайплайн: агент `qa-lead` изучает Android-проект через git и RAG,
пишет SDD на каждого субагента, готовит каркас теста на апрув человеку, генерирует
Kotlin-тест, запускает его на эмуляторе, дебажит падения, а отдельная LLM
(`reviewer`) проверяет загруженные и полученные из MCP тесты.

## Запуск

```bash
# бэкенд
cd studio2/backend
python -m uvicorn main:app --port 8100

# фронтенд
cd studio2/frontend
npm install
npm run dev     # http://localhost:5174
```

## Тесты

```bash
cd studio2/backend && python -m pytest -q
cd studio2/frontend && npm test
python studio2/scripts/e2e_day35.py
```

## Роли и декларации

Поведение агентов описано в `doc/agents/*.md` (front-matter + Markdown).
Правка промпта не требует изменения кода: перезапустите бэкенд и нажмите
«Переиндексировать» во вкладке RAG.

| Файл | Роль |
|---|---|
| `qa-lead.md` | оркестратор, SDD и стадии |
| `researcher.md` | изучение проекта через git и RAG |
| `testwriter.md` | каркас, затем Kotlin-тест |
| `debugger.md` | разбор падений |
| `reviewer.md` | отдельная LLM: зелёный/красный для загруженных тестов |
| `device-inspector.md` | снятие иерархии экрана через adb |

## Документы

- `doc/kb/` — база знаний (архитектура, паттерны, принципы, экраны).
- `doc/tasks/` — тест-кейсы.
- `doc/runs/<id>/` — артефакты запуска (SDD, каркас, тесты, отчёты), не в git.

## Гейты

1. `skeleton → write` — только после утверждения каркаса человеком.
2. `write → provision` — только после выбора способа исполнения
   (`as_loaded` или `generated`).

## Провайдеры моделей

`fake` (детерминированный, для e2e), `ollama` (`http://localhost:11435`),
`gpustack` (требует корпоративную сеть). Провайдер и модель задаются на каждой
роли во вкладке «Модели».

Модель `reviewer` обязана отличаться от `testwriter`.
```

- [ ] **Step 4: Update release notes**

Prepend to `RELEASE.md`:
```markdown
# Release Notes — day35-qa-lead-orchestrator (день 35)

Ветка: [`day35-qa-lead-orchestrator`](https://github.com/imarkelov/ai_advent_challenge/tree/day35-qa-lead-orchestrator).

## Что и зачем

**QA-Lead Orchestrator.** Новый проект `studio2`: автономный QA-пайплайн для
реального Android-приложения. Агент `qa-lead` изучает проект через git и RAG,
генерирует SDD на каждого субагента, готовит каркас теста на апрув, пишет
Kotlin-тест, прогоняет его на эмуляторе, дебажит падения, а ревьюер на отдельной
модели проверяет загруженные и полученные из MCP тесты.

## Компоненты

- `studio2/backend` — FastAPI: конфиг ролей и провайдеров, RAG (BM25-lite),
  документы `doc/`, git-адаптер, adb-адаптер, Gradle-runner, MCP-клиент,
  пайплайн со стадиями и гейтами, SSE.
- `studio2/doc/agents/*.md` — MD-декларации семи агентов (front-matter + тело).
- `studio2/doc/kb/*.md` — база знаний: архитектура, паттерны, принципы, экраны.
- `studio2/frontend` — React + Vite: дашборд QA (модели, RAG, MCP, задачи,
  pipeline, запуски), SSE-трейс, редактор каркаса, модалка вопроса.
- `studio2/scripts/e2e_day35.py` — детерминированная e2e-проверка на fake-LLM.

## Стадии

`sdd → study → skeleton → [гейт: апрув каркаса] → write → [гейт: способ исполнения]
→ provision → execute → debug → review → done`

## Инварианты

1. Модель ревьюера отличается от модели тестировщика.
2. Без апрува каркаса переход в `write` запрещён.
3. Без выбора способа исполнения переход в `provision` запрещён.
4. Инструмент вне `allowed_tools` запрещён.
5. Третий круг изучения запрещён.
6. `doc/kb` пишет только исследователь.
7. Секреты не попадают в `run.json` и логи.

## Тесты

- Бэкенд: `python -m pytest -q` из `studio2/backend`.
- Фронтенд: `npm test` и `npm run build` из `studio2/frontend`.
- E2E: `python studio2/scripts/e2e_day35.py` — 11 PASS.

## Проверка ключей

| Ключ | Статус |
|---|---|
| `GPUSTACK_BASE_URL` | задан |
| `GPUSTACK_API_KEY`, `_DEEPSEEK`, `_GLM`, `_EMBED`, `_RERANK` | заданы, но `gpustack.data.lmru.tech` не резолвится без корпоративной сети |
| `OLLAMA_BASE_URL` | `http://localhost:11435`, Ollama в Docker работает |

Модели Ollama: `qwen3-coder:30b`, `gemma4:12b`, `qwen3.5:9b`.
Демо идёт на Ollama; e2e — на `fake`. GPUSTACK заработает после подключения к VPN.

## Артефакты демо

- `docs/day35_demo.png`
- `docs/day35_pipeline_trace.txt`
- `docs/day35_report.md`

---

```

- [ ] **Step 5: Update root README and LINKS**

In `README.md`, add a row to the recent-days table:
```markdown
| **35** → [qa-lead-orchestrator](https://github.com/imarkelov/ai_advent_challenge/tree/day35-qa-lead-orchestrator) | Автономный QA-пайплайн `studio2`: MD-декларации агентов, RAG по `doc/`, git-изучение, SDD на субагента, каркас теста на апрув, Kotlin-тест, прогон на эмуляторе, ревью отдельной LLM | ![день 35](docs/day35_demo.png) |
```

Append to `LINKS.md`:
```markdown

# Ссылки — день 35 (QA-Lead Orchestrator)

## Ветка

- GitHub: <https://github.com/imarkelov/ai_advent_challenge/tree/day35-qa-lead-orchestrator>

## Демо

- Скриншот: `docs/day35_demo.png`
- Журнал пайплайна: `docs/day35_pipeline_trace.txt`
- Отчёт запуска: `docs/day35_report.md`
- Видео-демо: _будет добавлено после записи_
```

- [ ] **Step 6: Full verification**

```bash
cd studio2/backend
python -m pytest -q
cd ../frontend
npm test
npm run build
cd ../..
python studio2/scripts/e2e_day35.py
```

Expected: backend all pass, frontend all pass, build clean, e2e `11 PASS, 0 FAIL`.

- [ ] **Step 7: Commit**

```bash
git add studio2/scripts studio2/README.md RELEASE.md README.md LINKS.md
git commit -m "chore(studio2): e2e script, README, release notes for day 35"
```

---

### Task 17: Demo recording and push

**Files:**
- Create: `docs/day35_demo.png`
- Create: `docs/day35_pipeline_trace.txt`
- Create: `docs/day35_report.md`

**Interfaces:**
- Consumes: a running backend on `8100` and frontend on `5174`, plus a `fake` or Ollama config.

- [ ] **Step 1: Start backend and frontend**

```bash
cd studio2/backend
python -m uvicorn main:app --port 8100
```

In a second terminal:
```bash
cd studio2/frontend
npm run dev
```

- [ ] **Step 2: Prepare deterministic demo state**

```bash
curl -X POST http://127.0.0.1:8100/api/rag/reindex
curl http://127.0.0.1:8100/api/tasks
```

Confirm `TC-4217` is present and RAG counts at least 10 documents.

- [ ] **Step 3: Record the demo**

Follow the `studio-demo-video` skill: Playwright records `webm`, converts to `mp4`,
saves to the user's disk. Capture this sequence:

1. Вкладка «Модели»: пять ролей, ревьюер на `gemma4:12b`, провайдеры и маскированные ключи.
2. Вкладка «MCP»: добавление Allure-сервера, подключение, список инструментов.
3. Вкладка «RAG»: дерево `doc/`, «Переиндексировать», открыть `kb/patterns.md`.
4. Вкладка «Задачи»: выбрать `TC-4217`.
5. Pipeline: «▶ Запустить», live-трейс стадий `sdd → study → skeleton`.
6. Гейт: открыть каркас, добавить правку, «Утвердить и реализовать».
7. Гейт: выбрать способ исполнения.
8. Дождаться `done`, показать вердикт `green` и `test_report.md`.

- [ ] **Step 4: Save the artifacts**

```bash
# скриншот ключевого экрана Pipeline
copy <путь к скриншоту> docs\day35_demo.png

# выжимка журнала (последние 120 строк из run.json поля log)
python -c "import json,glob; p=sorted(glob.glob('studio2/data/runs/*.json'))[-1]; r=json.load(open(p,encoding='utf-8')); open('docs/day35_pipeline_trace.txt','w',encoding='utf-8').write('\n'.join(f\"{e['at']} [{e['role']}] {e['message']}\" for e in r['log'][-120:]))"

# копия итогового отчёта
copy <путь к test_report.md> docs\day35_report.md
```

- [ ] **Step 5: Verify demo artifacts exist and are non-empty**

Run: `python -c "from pathlib import Path; files=['docs/day35_demo.png','docs/day35_pipeline_trace.txt','docs/day35_report.md']; print([(f, Path(f).exists(), Path(f).stat().st_size if Path(f).exists() else 0) for f in files])"`
Expected: all three exist with size `> 0`.

- [ ] **Step 6: Commit demo artifacts**

```bash
git add docs/day35_demo.png docs/day35_pipeline_trace.txt docs/day35_report.md
git commit -m "docs(studio2): day 35 demo screenshot, pipeline trace and report"
```

- [ ] **Step 7: Final repository checks**

```bash
git status --short
git log --oneline -12
cd studio2/backend && python -m pytest -q
cd ../frontend && npm test
```

Expected: clean tree (except the untracked sandbox data already ignored),
backend and frontend suites green. Add the video link to `LINKS.md` if a video
was produced, then commit that change.

- [ ] **Step 8: Push the branch**

```bash
git checkout -b day35-qa-lead-orchestrator
git push -u origin day35-qa-lead-orchestrator
```

Expected: branch pushed, `origin/day35-qa-lead-orchestrator` exists.
Report the branch URL back to the user.

---

## Self-Review

**1. Spec coverage**

| Spec section | Covered by |
|---|---|
| 1.1 layout | Tasks 1–2, 4–14 (folders created per file) |
| 1.2 run flow | Tasks 11, 13 |
| 1.3 `doc/` structure | Tasks 2, 3, 9 |
| 1.4 agent MD format | Task 2 |
| 1.5 UI tabs | Tasks 14, 15 |
| 2.1 roles/providers config | Task 1 |
| 2.2 REST contracts | Tasks 12, 13 |
| 2.3 SSE protocol | Tasks 9, 13, 14 |
| 2.4 LLM layer | Task 6 |
| 2.5 live vs fake | Tasks 6, 16 |
| 2.6 invariants | Tasks 11, 16 |
| 3. visual style | Task 14 (tokens), Task 15 (screens) |
| 4.1–4.9 roles, SDD, gates | Tasks 2, 9, 10, 11 |
| 4.10 stage flow + events | Tasks 11, 13 |
| 4.11 role invariants | Tasks 10, 11, 16 |
| 5. Android execution | Tasks 7, 11 |
| 5.4 screen reconnaissance | Task 7 (`screen_markdown`) |
| 5.6 degradation | Tasks 7, 11 (`mode`, try/except) |
| 6.1 test pyramid | Tasks 1–15 + Task 16 |
| 6.2 demo | Task 17 |
| 6.3 keys | Task 16 release notes |
| 6.4–6.5 README/RELEASE/push | Tasks 16, 17 |

**2. Placeholder scan** — no `TBD`, no "add error handling" steps; every step
carries concrete code or concrete commands. `<...>` appears only inside the
document *content* that agents read, which is intended.

**3. Type consistency** — verified names across tasks:
`AgentSpec`, `RunDir`, `RunStore`, `EventBus`, `LlmClient`, `QaLead`,
`McpServerStore`, `McpRegistry`, `GradleRunner`, `AndroidDevice`,
`parse_uiautomator_xml`, `ui_tree_to_flat`, `screen_markdown`,
`sdd_text`, `skeleton_markdown`, `review_markdown`, `test_report_markdown`,
`PendingGate`, `GateError`, `InvalidTransition`, `CriticSameModel`.
Frontend: `pipelineReducer`, `initialState`, `PipelineTab` props
(`state`, `dispatch`, `onApprove`, `onChooseExecution`), `SkeletonEditor` props
(`runId`, `onApproved`).

