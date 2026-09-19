# Tasks: day11-memory-layers

Порядок — по зависимости; каждый блок — TDD (тест → код, прогон
`python -m pytest -q` в конце блока). Спецификации:
specs/memory-layers/spec.md; решения: design.md.

## 1. Подготовка (процесс)

- [x] 1.1 Закоммитить незакоммиченный day10 WIP (request-journal: agent.py, main.py, index.html, тесты) на ветке `day10` — проверить: `git status` чистый, последний коммит day10-ветки
- [x] 1.2 Создать ветку `day11` от `day10` — проверить: `git branch --show-current` = `day11`
- [x] 1.3 Добавить `working_memory.json`, `memory.json` в `.gitignore` — проверить: после создания файлов `git status` их не показывает

## 2. Хранилища памяти (memory.py, TDD)

- [x] 2.1 `memory.py`: `MemoryManager` — чтение/запись `working_memory.json` (per-dialogue dict) и `memory.json` (LT + settings): атомарно (tmp + os.replace), отсутствие/битость → пустые дефолты; тесты `tests/test_memory_store.py` (round-trip, битый JSON → дефолты) — проверить: `pytest tests/test_memory_store.py -q` зелёный
- [x] 2.2 Настройки модели памяти: дефолты (auto_extract=False, inject working/long_term=True, дефолтные routing-правила: цель/стек/ограничения/дедлайн → working, профиль/решения/знания → long_term); `set_routing`/`set_settings` с валидацией (неизвестная категория/target/не-bool → ValueError, изменения атомарны); тесты (дефолты, round-trip на диск, невалидные → без изменений) — проверить: pytest блок зелёный
- [x] 2.3 Ручной save: `save("working", content)` — WM активного диалога; `save("long_term", content)` — глобально; `reset_layer` (working — активный диалог; long_term — глобально); неизвестный слой → ValueError; тесты (save → snapshot → файл на диске; working не попадает в LT) — проверить: pytest блок зелёный
- [x] 2.4 LT-семантика слияния: profile — merge dict, decisions/knowledge — append с dedup по точному совпадению; тесты (повторное значение не дублируется, новый — добавляется) — проверить: pytest блок зелёный

## 3. Извлечение и routing (TDD)

- [x] 3.1 Промпт извлечения: `MEMORY_MARKER`, RU-промпт «верни ТОЛЬКО JSON, 7 категорий, опустить отсутствующее», разбор через facts.parse_facts_json (markdown-ограды); любой сбой (исключение llm_call, не-JSON) → слои не меняются, исключений наружу нет; тесты с fake llm_call — проверить: pytest блок зелёный
- [x] 3.2 `auto_extract(user_input, llm_call)`: извлечение → слияние → routing по правилам (working → WM активного диалога, long_term → LT, dialogue_only → выброс); выключенный тумблер → вызова LLM нет; тесты (canned extraction JSON → значения в правильных хранилищах; перемаршрутизированная категория попадает в новый слой; dialogue_only не копируется; тумблер off → 0 вызовов) — проверить: pytest блок зелёный
- [x] 3.3 `build_system_context(base)`: WM-блок в точном day10-формате («Актуальные факты:\n- ключ: значение») + LT-блок («Долговременная память:\n- профиль: ...\n- решения: ...\n- знания: ...» по непустым секциям); пустой слой → промпт не меняется; выключенный тумблер → блока нет; тесты (точные строки system-промпта в 4 комбинациях) — проверить: pytest блок зелёный

## 4. Интеграция в агент (TDD)

- [x] 4.1 Реестр: `STRATEGIES` = 3 режима ST (legacy/sliding_window/branching); `StickyFactsStrategy` остаётся классом (тесты дня 10 зелёные) но вне реестра; `switch_strategy("sticky_facts")` → ValueError (RU); тесты `tests/test_memory_st_modes.py` — проверить: `pytest tests/test_memory_st_modes.py tests/test_strategies.py -q` зелёные
- [x] 4.2 Миграция sticky_facts-диалогов в `_load_dialogues` (паттерн v1→v2: в памяти, обычный save закрепляет): режим → sliding_window, факты задачи → WM-хранилище под id диалога, непустое «решения» → LT.decisions, сообщения не трогать; тест (старый dialogues.json с facts → после загрузки: режим, WM-файл, LT) — проверить: pytest блок зелёный
- [x] 4.3 `ask()`: новый порядок (st.on_user_message → auto_extract → build_payload → инъекция build_system_context); request-journal, append-истории, branching-append, cap — без изменений; тесты: `tests/test_legacy_payload.py` (locking) пройдёт БЕЗ ПРАВОК + новый тест (WM/LT непустые → блоки в system payload; пустые → byte-identical legacy) — проверить: `pytest tests/test_legacy_payload.py tests/test_memory_* -q` зелёные
- [x] 4.4 Публичные методы агента памяти (memory_snapshot / memory_save / memory_set_routing / memory_set_settings / memory_reset_layer) — делегирование MemoryManager под RLock, ValueError → наружу; тесты через агента (tmp-файлы) — проверить: pytest блок зелёный
- [x] 4.5 Дефолты OFF (tests/test_memory_defaults.py): свежий агент — auto_extract=False, инъекция ON, дефолтные правила; ask() с fake-LLM — ни одного вызова с MEMORY_MARKER; payload legacy-диалога без блоков памяти — проверить: `pytest tests/test_memory_defaults.py -q` зелёный

## 5. REST API (main.py)

- [x] 5.1 `GET /agent/memory` → {short_term, working (активного диалога), long_term, settings}; тест (http-клиент к поднятому обработчику/агенту, tmp-файлы) — проверить: pytest блок зелёный
- [x] 5.2 `POST /agent/memory/save` (200; не-str content/неизвестный слой → 400), `POST /agent/memory/routing` (200 + актуальные правила; неизвестная категория/target → 400, правила не меняются), `POST /agent/memory/settings` (200; не-bool → 400); тесты каждого маршрута (200/400) — проверить: pytest блок зелёный
- [x] 5.3 `DELETE /agent/memory/working` и `/agent/memory/long_term` (200, слой сброшен в GET /agent/memory; settings при сбросе LT сохраняются); `DELETE /agent/memory/unknown` → 404; тесты — проверить: pytest блок зелёный
- [x] 5.4 `POST /agent/strategy/switch {"strategy":"sticky_facts"}` → 400 RU-сообщение (поглощена рабочим слоем); тест — проверить: pytest блок зелёный

## 6. UI (index.html, TRON-HUD-стиль)

- [x] 6.1 Панель «Память»: 3 секции (краткосрочная — id/число сообщений; рабочая — строки фактов или «пустая»; долговременная — профиль/решения/знания), опрос `GET /agent/memory` каждые 2,5 с (паттерн day10-панели); проверить: e2e/UI-проверка — панель с содержимым на странице
- [x] 6.2 Редактор routing: 7 строк «категория → select(working/long_term/dialogue_only)» + «Применить правила» (POST /agent/memory/routing); 3 тумблера (WM в payload, LT в payload, авто-извлечение; POST /agent/memory/settings); проверить: e2e — смена правила/тумблера видна в следующем GET /agent/memory
- [x] 6.3 Кнопка «Запомнить» на строках сообщений → выбор слоя (working/long_term) → POST /agent/memory/save (content = текст сообщения); проверить: e2e — клик → значение в панели слоя и в GET /agent/memory
- [x] 6.4 Селектор «Стратегия» → «Режим краткосрочной» (опции: legacy/sliding_window/branching, sticky_facts удалён); панель sticky_facts-фактов (day10) убрана (её функция — секция «Рабочая»); branching-панель без изменений; проверить: e2e/UI — селектор с 3 опциями, панель памяти на месте

## 7. Инструменты дня 10 на ветке day11

- [x] 7.1 `benchmark_day10.py`: sticky_facts пропускается (2 стратегии + RU-примечание в выводе); проверить: `python benchmark_day10.py --mock` exit 0
- [x] 7.2 `scripts/e2e_day10.py`: sticky_facts → ожидается 400 (проверка совместимости day11); проверить: `python scripts/e2e_day10.py` exit 0
- [x] 7.3 Полный прогон тестов: `python -m pytest -q` — без падений (включая locking-тест legacy и тесты дня 10) — проверить: exit 0

## 8. Проверка модели памяти (benchmark/e2e/README)

- [x] 8.1 `scenario_day11.py`: детерминированный сценарий — диалог 1 (4–6 сообщений: профиль — имя/роль; задача — цель/стек/ограничения/дедлайн/решение), закрытие, диалог 2 (вопрос «Кто я и что мы решили?»); сценарий с фиксированными текстами (canned) для mock; проверить: импорт модуля, count ходов (unit-тест)
- [x] 8.2 `benchmark_day11.py --mock` (fake urlopen, MEMORY-ИЗВЛЕЧЕНИЕ → canned JSON): метрики — routing-точность (доля значений в правильном хранилище), инъекция (блоки в system payload: вкл/выкл тумблеров), персистентность (LT-значения в payload нового диалога 2); таблица + `--out FILE.json`; проверить: `python benchmark_day11.py --mock` exit 0, таблица напечатана
- [x] 8.3 `benchmark_day11.py --real`: диалог 1 формирует профиль/решение (auto_extract вкл) → новый диалог 2, вопрос о них; прогон А (LT вкл) / Б (LT выкл); печать обоих ответов + вердикт (ключевые слова в А, не в Б); проверить: реальный прогон (при достижимом GPustack), отчёт напечатан
- [x] 8.4 `scripts/e2e_day11.py` (паттерн e2e_day10, порт 8000): HTTP-поток — save → слой, routing (смена + 400), settings-тумблеры, delete, GET /agent/memory; при достижимом GPustack — ask и блок LT в `/agent/last-request` (тумблеры вкл/выкл); UI: селектор режима (3 опции), элементы панели памяти; exit 0 = PASS/SKIP, 1 = FAIL; проверить: `python scripts/e2e_day11.py` exit 0
- [x] 8.5 README: строка «День 11» в таблице дней + секция «День 11: модель памяти» (3 слоя и хранилища, routing + дефолты, инъекция, API, метрики benchmark-таблицы, команды запуска); проверить: секция присутствует, числа таблиц — из реального mock-прогона

## 9. Финальная проверка

- [x] 9.1 Полная регрессия: `python -m pytest -q` + `python scripts/e2e_day11.py` + `python benchmark_day11.py --mock` — все exit 0 — проверить: три прогона зелёные в одной сессии
- [x] 9.2 `openspec validate day11-memory-layers` — valid
- [x] 9.3 `verification-before-completion`: evidence (выводы pytest/e2e/benchmark, скриншот/снимок панели) ДО заявления «готово»; затем — отчёт пользователю — проверить: evidence собраны и приложены к финальному сообщению
