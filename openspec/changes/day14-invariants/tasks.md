# Tasks: day14-invariants

## 1. Подготовка

- [ ] 1.1 Ветка `day14-invariants` от `day13-task-state-machine` — проверить:
  `git branch --show-current` = `day14-invariants`, `git log -1` = последний
  коммит day13-task-state-machine.

## 2. Бэкенд: хранилище инвариантов (memory.py)

- [ ] 2.1 Методы `MemoryStore`: `invariants_items()` ({} если нет/битый),
  `invariants_set(key, value)` (непустые str; атомарно под `Lock`),
  `invariants_remove(id)` (True если была), `invariants_clear()`; хранение —
  `invariants.json` (`{"inv_xxxx": {"key":..., "value":...}}` или список);
  файл создаётся при первом изменении — проверить: тесты
  `tests/test_memory.py` (CRUD, пустой/битый файл → {}, изоляция от WM/LT).
- [ ] 2.2 `build_invariants_block()` → `""` при пустых, иначе
  `"\n\nИнварианты (неукоснительно):\n- key: value"` — проверить: тест в
  `tests/test_memory.py` (пусто/непусто).
- [ ] 2.3 `layer_stats()` → + поле `invariants` `{entries, tokens_est, items}`
  — проверить: тест на статистику.

## 3. Бэкенд: инъекция + правило + гард (agent.py)

- [ ] 3.1 `build_payload`: вставить `build_invariants_block` между профилем и
  памятью (порядок: базовый → профиль → ИНВАРИАНТЫ → память → правило
  конфликтов) — проверить: тесты `tests/test_agent.py`: инварианты в system
  выше памяти; существующие тесты не сломаны.
- [ ] 3.2 Константа `INVARIANTS_RULE` (жёстче MEMORY_RULE: высший приоритет,
  отказ с указанием инварианта, объяснение, альтернатива в рамках
  инвариантов) в конец system-промпта — проверить: тест на наличие правила
  при непустых инвариантах.
- [ ] 3.3 `_detect_invariant_conflict(dialogue_id, message)` — зеркало
  `_detect_memory_conflict` по `invariants_items()`, без тумблеров, ключ < 4
  символов пропуск — проверить: тесты (срабатывание/не-срабатывание, глагол,
  короткий ключ).
- [ ] 3.4 `ask_stream`: после `_detect_memory_conflict`/`_detect_taboo` — если
  сработали инварианты, в конец `messages` system-напоминание «запрос
  противоречит инварианту X — откажись» — проверить: тест на наличие
  напоминания в payload при конфликте; файл инвариантов не меняется.

## 4. Бэкенд: API (main.py)

- [ ] 4.1 `GET /api/invariants` → `{"invariants":[{id,key,value}]}`;
  `POST /api/invariants` `{key,value}` → 201 (400 — не-str/пустые/отсутствуют);
  `DELETE /api/invariants/{id}` → 200/404 — проверить: тесты
  `tests/test_api.py` (200/201/400/404 по веткам): `python -m pytest tests/test_api.py -q`.
- [ ] 4.2 `GET /api/rules` → + `invariants_block`; `GET /api/memory` → +
  `invariants` — проверить: тесты API.
- [ ] 4.3 Полный прогон офлайн-тестов бэкенда — проверить:
  `cd studio/backend && python -m pytest -q` зелёный.

## 5. Фронтенд: вкладка «Инварианты»

- [ ] 5.1 `api.ts` + `state.tsx`: хелперы `getInvariants`/`addInvariant`/
  `deleteInvariant`; типизация `invariant` — проверить: `npx tsc --noEmit` чистый.
- [ ] 5.2 `ContextPanel.tsx`: 5-й таб «Инварианты» → `InvariantsTab.tsx`
  (каркас MemoryTab: список key-value, поля добавления, удаление, пометка
  «неизменяемые", без свитчей) — проверить: Vitest-тесты InvariantsTab
  (список, добавление, удаление, нет свитчей): `cd studio/frontend && npm test`.
- [ ] 5.3 `GET /api/rules`/`/api/memory` данные для таба — проверить:
  Vitest-тесты.

## 6. Проверка задания (e2e + README)

- [ ] 6.1 Расширить `scripts/e2e_studio.py` блоком «Инварианты»: добавить
  инвариант → гард в теле запроса → удалить; SKIP-ветка при недоступном
  GPustack — проверить: `python scripts/e2e_studio.py` exit 0 (PASS/SKIP).
- [ ] 6.2 README: секция «День 14» (release notes, API-таблица инвариантов,
  статус тестов) + строка дня 14 в таблице веток — проверить: секция
  присутствует.

## 7. Финализация

- [ ] 7.1 Финальная верификация: бэкенд pytest + фронтенд npm test + e2e
  exit 0 — проверить: все зелёные, коммит(ы) на `day14-invariants` по
  конвенции репозитория.

---

## Дополнение: детальный план реализации (трассировка к спецификации)

> Дополнение к разделам выше: каждый пункт ссылается на требование (R1–R7)
> `specs/dialogue-invariants/spec.md`, содержит критерий приёмки. Дублирует
> верхние шаги в форме «требование/сценарий → проверка», ничего не удаляя.

### R1 — Хранилище инвариантов (spec: «Хранилище инвариантов»)

- [ ] **T1.1** Ветка `day14-invariants` от `day13-task-state-machine` — проверить: `git branch --show-current` = `day14-invariants`, `git log -1` = последний коммит day13.
- [ ] **T1.2** `memory.py` — `__init__` добавляет `self._p_invariants = os.path.join(self._data_dir, "invariants.json")`; завести `_read_invariants()` / `_write_invariants()` (атомарно: tmp + `os.replace`) — проверить: файл не создаётся сам по себе до первого изменения.
- [ ] **T1.3** Методы `MemoryStore`:
  - `invariants_items()` → `{}` при отсутствующем/битом файле (сценарий «Пустой/битый файл»);
  - `invariants_set(key, value)` — оба непустые str; id = `"inv_" + uuid4().hex[:8]`; обновление по существующему `key` сохраняет прежний `id` (сценарий «Инварианты переживают перезапуск»);
  - `invariants_remove(id)` → True если была, False иначе;
  - `invariants_clear()`.
  Проверить: `tests/test_memory.py` — CRUD, пустой/битый, пересохранение по key, изоляция от WM/LT (сценарий «Изоляция от памяти»).
- [ ] **T1.4** `build_invariants_block()` → `""` при пустых, иначе `"\n\nИнварианты (неукоснительно):\n- key: value"` — проверить: тест (пусто→"", непусто→блок).
- [ ] **T1.5** `layer_stats()` → + поле `invariants` `{entries, tokens_est, items}` — проверить: тест статистики; инварианты в отдельном блоке, не смешиваются с WM/LT.

### R2 — Блок инвариантов в system-промпте (spec: «Блок инвариантов в system-промпте»)

- [ ] **T2.1** `agent.py` `build_payload`: вставить `build_invariants_block` между профилем и памятью. Порядок: базовый промпт → профиль → **инварианты** → память (WM→LT) → правило конфликтов — проверить: `tests/test_agent.py` — инварианты в system выше памяти (сценарий «Инварианты выше памяти»).
- [ ] **T2.2** При пустых инвариантах блок не добавляется — проверить: тест «Пустые инварианты — блока нет»; остальные блоки без изменений (никого не сломать).

### R3 — Правило инвариантов INVARIANTS_RULE (spec: «Правило инвариантов (INVARIANTS_RULE)»)

- [ ] **T3.1** Константа `INVARIANTS_RULE` в конец system-промпта (после MEMORY_RULE): инварианты — высший приоритет над памятью/профилем/запросами; при противоречии ассистент отказывается, называет инвариант, объясняет отказ, предлагает альтернативу в рамках инвариантов; MUST NOT изменять/удалять инварианты — проверить: тест (при непустых инвариантах правило присутствует, сценарий «Правило при непустых инвариантах»).
- [ ] **T3.2** Доминирование над памятью — проверить: тест сценария «Правило доминирует над памятью» (запрос конфликтует и с инвариантом, и с пунктом памяти → отказ ссылается на инвариант).

### R4 — Server-side гард конфликта (spec: «Server-side гард конфликта инвариантов»)

- [ ] **T4.1** `_detect_invariant_conflict(dialogue_id, message)` — зеркало `_detect_memory_conflict`, но по `invariants_items()` и без учёта тумблеров (всегда активен): ключ в сообщении, значение НЕ в сообщении, есть глагол действия; ключ < 4 символов — пропуск — проверить: тесты (сценарии «Запрос противоречит инварианту» / «Запрос в рамках инварианта» / «Короткий ключ пропускается»).
- [ ] **T4.2** `ask_stream`: после `_detect_memory_conflict`/`_detect_taboo` — если сработали инварианты, в конец `messages` system-напоминание «запрос противоречит инварианту X — откажись»; проверяется до конфликта памяти — проверить: тест — payload с напоминанием при конфликте; файл инвариантов не меняется (сценарий «Запрос противоречит инварианту»).

### R5 — REST API (spec: «REST API инвариантов»)

- [ ] **T5.1** `main.py`:
  - `GET /api/invariants` → `{"invariants": [{id, key, value}]}`;
  - `POST /api/invariants` `{key, value}` → 201 (400 — не-str/пустое/отсутствует, RU-сообщение);
  - `DELETE /api/invariants/{id}` → 200; 404 — id не найден.
  Проверить: `tests/test_api.py` — сценарии «Добавление инварианта», «Валидация», «Удаление» (200/201/400/404).
- [ ] **T5.2** `GET /api/rules` → + `invariants_block`; `GET /api/memory` → + `invariants` (в `layer_stats`) — проверить: тесты API.
- [ ] **T5.3** Полный офлайн-прогон бэкенда — проверить: `cd studio/backend && python -m pytest -q` зелёный (существующие тесты не сломаны).

### R6 — Вкладка «Инварианты» в UI (spec: «Вкладка «Инварианты» в UI»)

- [ ] **T6.1** `api.ts` + `state.tsx`: тип `Invariant {id, key, value}`, хелперы `getInvariants()`/`addInvariant(key, value)`/`deleteInvariant(id)`; слой в reducer + `refreshInvariants`; загрузка неблокирующая (паттерн models/memory) — проверить: `npx tsc --noEmit` чистый.
- [ ] **T6.2** `ContextPanel.tsx` — 5-й таб «Инварианты» → `InvariantsTab.tsx` (каркас MemoryTab): список key-value, поля добавления, удаление по id, пометка «неизменяемые», **без свитчей вкл/выкл** — проверить: Vitest `tests/invariants-tab.test.tsx` (сценарии «Список инвариантов», «Добавление и удаление»).
- [ ] **T6.3** Данные таба из `GET /api/rules`/`/api/memory` — проверить: Vitest-тесты.

### R7 — Проверка задания (spec: «Проверка инвариантов (тесты)»)

- [ ] **T7.1** `scripts/e2e_studio.py` — блок «Инварианты» (детерминированное ядро, без GPustack): GET 200 → POST 201 → GET содержит → `GET /api/rules` `invariants_block` содержит → `GET /api/memory` `invariants` entries >= 1 → POST пустое → 400 → DELETE 200 → повтор DELETE 404; `_cleanup(..., inv_ids=[inv_id])`; live-ветка (GPustack): добавить инвариант → чат с ключом+противоречащим значением → system-напоминание гарда в теле запроса (видно в `GET /api/requests`); SKIP при недоступном GPustack — проверить: `python scripts/e2e_studio.py` exit 0 (PASS/SKIP).
- [ ] **T7.2** README: секция «День 14: Инварианты» (модель данных, инъекция, правило конфликтов, server-side гард, API-таблица, UI, проверка задания) + строка дня 14 в таблице веток — проверить: секция присутствует.

### Финализация

- [ ] **T8.1** Финальная верификация: `cd studio/backend && python -m pytest -q` (зелёный), `cd studio/frontend && npx tsc --noEmit && npm test` (зелёный), `python scripts/e2e_studio.py` exit 0 — проверить: все зелёные.
- [ ] **T8.2** Коммит(ы) на `day14-invariants` по конвенции репозитория (проверить `git status`; застажить только нужные файлы: backend/frontend/e2e/README/openspec).
