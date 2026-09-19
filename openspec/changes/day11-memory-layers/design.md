# Design: day11-memory-layers

## Context

Текущее состояние (ветка day10): `SimpleAgent` (agent.py) собирает
LLM-контекст через стратегии дня 10 (`strategies.py`, ABC `ContextStrategy`,
реестр `STRATEGIES`: legacy / sliding_window / sticky_facts / branching);
стратегия привязана к диалогу (ключ `strategy` в `dialogues.json`), состояние —
`strategy_state`. `facts.py` — LLM-извлечение фактов ТЗ (маркер + fallback без
исключений). Invariants: legacy payload byte-identical
(tests/test_legacy_payload.py, golden-файл), новые фичи дефолт OFF
(tests/test_defaults_off.py), request-journal (in-memory deque), атомарные
записи (tmp + os.replace), все мутации под `RLock`. Тесты офлайн
(monkeypatch urlopen, tmp_path).

Ограничения: stdlib-only (без новых зависимостей), http.server + index.html,
файлы данных в `.gitignore`, RU-комментарии/docstring, ошибки — RuntimeError/
ValueError (RU), HTTP 400/404/502.

Смотреть мотивацию: proposal.md. Требования: specs/memory-layers/spec.md.

## Goals / Non-Goals

**Goals:**

- Явная 3-слойная модель памяти в отдельных хранилищах с наблюдаемой
  инъекцией в payload.
- Память — единственный ассамблер контекста; стратегии day10 → режимы ST.
- Три механизма контроля пользователя (routing, ручной save, тумблеры).
- Совместимость: legacy byte-identical, day10-поведение режимов, дефолты OFF.
- Проверка по шаблону дня 10: benchmark (mock/real) + e2e + README.

**Non-Goals:**

- Семантический поиск по памяти, векторные БД, ограничение размера LT
  (прунинг/старение), перенос WM → LT при закрытии задачи («архивация
  задачи»), мульти-пользовательность, изменение request-journal,
  изменения day9-механики сжатия (остаётся механикой режима legacy).

## Decisions

### D1. Модульная структура: новый `memory.py` (MemoryManager)

Новый модуль `memory.py` с классом `MemoryManager` — владелец рабочих/
долговременных хранилищ и настроек модели памяти. Публичные методы (использует
агент и роуты main.py):

- `build_system_context(base: str) -> str` — инъекция WM/LT-блоков в
  system-промпт (по тумблерам и непустоте);
- `auto_extract(user_input, llm_call) -> None` — извлечение + routing (только
  при включённом тумблере);
- `save(layer, content)` — ручной save (working — активный диалог; long_term —
  глобально);
- `set_routing(rules)`, `set_settings(toggles)`, `reset_layer(layer)`;
- `snapshot() -> dict` — содержимое 3 слоёв + настройки (для GET /agent/memory);
- `load()/save_*()` — атомарное чтение/запись файлов (паттерн
  `_save_dialogues_data`).

Агент (`ask()`) — точка интеграции (порядок вызовов):

```
with self._lock:
    st_name = self._resolve_strategy_name()      # 3 режима ST (реестр)
    st = self._get_strategy_instance(st_name)
    (legacy: _maybe_compress — механика day9 без изменений)
    st.on_user_message(...)                       # хук day10 (branching) — до build_payload
    self._memory.auto_extract(user_input, llm_call=self._extraction_call)
    system, slice_ = st.build_prompt_and_slice(...)
    system = self._memory.build_system_context(system)   # WM/LT-блоки
    messages = [system] + slice_                # далее как сейчас (journal, append, cap, save)
```

Альтернативы: (a) память внутри иерархии стратегий — отклонено: память
ортогональна срезу ST, а пользователь выбрал «память = единственный
ассамблер»; (b) пятая стратегия `memory` — отклонено: режим ST отвечает на
«как резать историю», память — «что где хранится и что инжектится».

### D2. Хранилища: 3 файла

| Слой | Файл | Формат |
|---|---|---|
| ST | `dialogues.json` (существует) | без новых ключей (ключ `strategy` — режим ST) |
| WM | `working_memory.json` (новое) | `{dialogue_id: {цель, стек, ограничения, дедлайн}}` |
| LT | `memory.json` (новое) | `{"profile": {k: v}, "decisions": [str], "knowledge": [str], "settings": {"routing": {cat: target}, "auto_extract": false, "inject": {"working": true, "long_term": true}}}` |

- Оба новых файла: атомарная запись (tmp + `os.replace`), кодировка UTF-8,
  `ensure_ascii=False`; при отсутствии/битости — пустые дефолты (паттерн
  `_load_dialogues`: OSError/ValueError → дефолты, без crash).
- Настройки модели памяти в `memory.json` (секция `settings`): альтернатива —
  отдельный `memory_settings.json` (4 файла) — отклонено: инвариант «3 типа —
  3 отдельных хранилища» держится для ДАННЫХ; настройки — глобальные параметры
  модели, а не четвёртый слой.
- WM пер-диалоговая, без pruning (архивные диалоги хранят свои WM; YAGNI).
- LT: `decisions`/`knowledge` — списки (append, дешёвый dedup по точному
  совпадению), `profile` — dict (обновление по ключу, семантика day10 facts).

### D3. Схемы и форматы блоков

- WM-схема: `WM_KEYS = ["цель", "стек", "ограничения", "дедлайн"]` (FACT_KEYS
  дня 10 минус «решения» — решения = LT-категория по ТЗ).
- LT-секции: `profile` (dict), `decisions` (list[str]), `knowledge` (list[str]).
- Категории извлечения: `MEMORY_CATEGORIES = WM_KEYS + ["профиль", "решения",
  "знания"]` (7).
- Формат WM-блока = формат инъекции sticky_facts дня 10 (байт-в-байт):
  `"\n\nАктуальные факты:\n" + строки "- ключ: значение"` по WM_KEYS для
  непустых. Мигрированные sticky_facts-диалоги при включённой инъекции дают
  тот же block, что и day10.
- Формат LT-блока: `"\n\nДолговременная память:\n" + строки`:
  `- профиль: k=v; k2=v2` (если profile не пусто), `- решения: item1; item2`,
  `- знания: item1; item2` — по непустым секциям.

### D4. Извлечение (авто-извлечение)

- Один служебный LLM-вызов на user-сообщение (только при `auto_extract=True`),
  механизм `_llm_raw_text` (temperature=0, max_tokens=SUMMARY_MAX_TOKENS,
  thinking off), НЕ попадает в `_last_request` (паттерн day9-сводки/
  day10-извлечения).
- Новый маркер `MEMORY_MARKER = "MEMORY-ИЗВЛЕЧЕНИЕ"` (аналог FACTS_MARKER —
  по нему mock-заглушка отличает запрос; факты.parse_facts_json переиспользуем
  для разбора JSON с markdown-оградами).
- Промпт (RU): верни ТОЛЬКО JSON с ключами 7 категорий; ключ без данных в
  сообщении — опустить; profile — объект, решения/знания — массивы строк,
  остальное — строки. Не выдумывать.
- Слияние: WM — по ключу (есть значение → обновить; нет → прежний); LT:
  profile — merge dict, decisions/knowledge — append с dedup по точному
  совпадению.
- Routing после извлечения: категория → `working` (WM активного диалога) /
  `long_term` (LT) / `dialogue_only` (выброс — сообщение и так в диалоге).
  Дефолтные правила: цель/стек/ограничения/дедлайн → working;
  профиль/решения/знания → long_term. Правила редактируемые (валидация:
  категория ∈ 7, target ∈ {working, long_term, dialogue_only}).
- Ошибка LLM/разбора → слои не меняются, ask() продолжается (fallback,
  лог warning — паттерн facts.py).

### D5. Режимы ST и реестр

- `STRATEGIES` (strategies.py) = режимы ST: `legacy`, `sliding_window`,
  `branching`. `StickyFactsStrategy` — класс остаётся в модуле (день 10, его
  тесты зелёные), но ИЗ РЕЕСТРА исключён: `switch_strategy("sticky_facts")` →
  ValueError (RU: «поглощена рабочим слоем памяти (day11)») → HTTP 400.
- Миграция при загрузке (в `_load_dialogues`, паттерн migrate_branching_state_v1
  — только в памяти, обычный save закрепляет): диалог со
  `strategy == "sticky_facts"` → `strategy = "sliding_window"`,
  `strategy_state.facts`: цель/стек/ограничения/дедлайн → запись WM-хранилища
  под id диалога (создаётся/объединяется), непустое «решения» → append в
  LT.decisions. `strategy_state` диалога — свежий default_state()
  sliding_window. Сообщения не трогаются.
- `benchmark_day10.py` и `scripts/e2e_day10.py` на ветке day11 патчим
  минимально: sticky_facts пропускается / ожидается 400 (артефакты дня 10,
  их поведение на day11-ветке задокументировано в README).

### D6. Гарантия byte-identical legacy

- Инъекция WM/LT — только при (тумблер слоя вкл AND слой непуст). Свежий
  агент: файлов памяти нет → слои пусты → system-промпт не меняется.
- `_maybe_compress`, `LegacyStrategy.build_payload`, обрезка HISTORY_CAP,
  request-journal — без изменений.
- Locking-тест (tmp-каталог, файлов памяти нет) проходит без правок;
  новые тесты фиксируют инъекцию (блок есть/нет при тумблерах) отдельно.

### D7. REST API (main.py, паттерн существующих роутов)

| Метод | Маршрут | Тело | Ответ |
|---|---|---|---|
| GET | `/agent/memory` | — | `{"short_term": {message_count, dialogue_id}, "working": {WM_KEYS...} (активного диалога), "long_term": {profile, decisions, knowledge}, "settings": {routing, auto_extract, inject}}` |
| POST | `/agent/memory/save` | `{"layer": "working"|"long_term", "content": str}` | 200 `{"ok": true}`; не-str content / неизвестный слой → 400 |
| POST | `/agent/memory/routing` | `{"категория": target}` (1+ пар) | 200 `{"routing": {...}}`; неизвестная категория/target → 400, правила не меняются |
| POST | `/agent/memory/settings` | `{"auto_extract"?: bool, "inject"?: {"working"?: bool, "long_term"?: bool}}` | 200 `{"settings": {...}}`; не-bool → 400 |
| DELETE | `/agent/memory/working` | — | сброс WM активного диалога → 200 |
| DELETE | `/agent/memory/long_term` | — | сброс LT (profile/decisions/knowledge → пустые; settings сохраняются) → 200 |
| — | `/agent/memory/<другое>` | — | 404 |

Валидация в main.py (тип/формат → 400), бизнес-логика — методы агента
(`agent.memory_*`), которые делегируют `MemoryManager` под `RLock`.

### D8. UI (index.html, TRON-HUD-стиль)

- Новый блок «Память» (рядом с панелью стратегии): три секции —
  Краткосрочная (id диалога, число сообщений), Рабочая (строки
  «- ключ: значение» или «— пустая —»), Долговременная (секции
  профиль/решения/знания). Опрос `GET /agent/memory` (интервал 2,5 с —
  паттерн day10-панели).
- Редактор routing: 7 строк «категория → select(working/long_term/
  dialogue_only)» + кнопка «Применить правила» (POST /agent/memory/routing).
- Тумблеры: «Рабочая в payload», «Долговременная в payload», «Авто-
  извлечение» (POST /agent/memory/settings).
- На каждой строке сообщения — кнопка «Запомнить» → выбор слоя
  (working/long_term) → POST /agent/memory/save (content = текст сообщения).
- Селектор «Стратегия» → подпись «Режим краткосрочной», опции: legacy /
  sliding_window / branching (sticky_facts из селектора удалён).
- Панель sticky_facts-фактов (day10) — убирается (её функция — секция
  «Рабочая» в панели памяти); branching-панель — без изменений.

### D9. Проверка (benchmark/e2e по шаблону дня 10)

- `scenario_day11.py` — детерминированный сценарий: диалог 1: 4–6 сообщений
  с данными профиля (имя, роль) и задачей (цель/стек/дедлайн/решение);
  закрытие; диалог 2: вопрос «Кто я и что мы решили?» (новый диалог → ST
  пуст, LT должен нести ответ).
- `benchmark_day11.py`:
  - `--mock` (fake urlopen, маркер MEMORY-ИЗВЛЕЧЕНИЕ → canned JSON): метрики —
    (1) **routing-точность**: доля expected-значений в правильном хранилище
    после сценария; (2) **инъекция**: в payload финального хода система
    содержит LT- и WM-блоки (тумблеры вкл) и не содержит (тумблеры выкл —
    отдельный прогон); (3) **персистентность**: LT-значения в payload
    диалога 2 (пустой ST). Таблица + exit code.
  - `--real` (реальный GPustack, auto_extract вкл): диалог 1 формирует
    профиль/решение → диалог 2, вопрос о них: прогон А (LT вкл) и прогон Б
    (LT выкл, тот же новый диалог) → печатает оба ответа + вердикт
    (профиль/решение в ответе А, не в Б — substring-хэвистик по ключевым
    словам сценария).
  - `--out FILE.json` — отчёт.
- `scripts/e2e_day11.py` (паттерн e2e_day10: сервер на 8000): HTTP-поток —
  save → GET /agent/memory (значение на месте), routing (смена + валидация
  400), settings (тумблеры), ask-независимые проверки + при достижимом
  GPustack 1–2 реальных ask и проверка блока LT в `/agent/last-request`
  (тумблеры вкл/выкл); UI: `cfg-strategy`-селектор с 3 опциями, элементы
  панели памяти на странице. exit 0 = PASS/SKIP, 1 = FAIL.
- README: строка «День 11» в таблице + секция «День 11: модель памяти»
  (слои, хранилища, routing, дефолты, метрики бенчмарка, запуск).

## Risks / Trade-offs

- [Падение legacy byte-identical при неосторожной инъекции] → инъекция
  только при (тумблер AND непустота); locking-тест обязателен в прогоне;
  файлы памяти в tmp-каталогах тестов не создаются.
- [Миграция sticky_facts-диалогов с данными] → в-памяти, non-destructive к
  файлу (паттерн v1→v2); данные «решений» при откате на day10 теряются
  (задокументировано; day11-ветка — обучающая, откат = переключение ветки).
- [Задержка ask() при auto_extract вкл] → дефолт OFF; включено = +1
  служебный вызов на сообщение (прецедент day10 sticky_facts); mock в тестах.
- [Рост LT-списков без предела] → YAGNI: прунинг вне дня 11; dedup точных
  дублей + DELETE-сброс задокументированы в README.
- [Повреждение новых JSON-файлов] → атомарная запись; битость → дефолты
  (агент стартует, слои пусты) — тот же паттерн, что dialogues.json.
- [Конкурентность] → все операции памяти под RLock агента; файлы пишутся
  атомарно (как dialogues.json).
- [Смертельный код day10 (StickyFactsStrategy вне реестра)] → класс и его
  тесты сохранены (регрессия дня 10 видна), из реестра исключён;
  benchmark/e2e дня 10 патчатся минимально на ветке day11.

## Migration Plan

1. Закоммитить незакоммиченный day10 WIP (request-journal) на ветке `day10`.
2. Создать ветку `day11` от `day10`.
3. Реализация по tasks.md (TDD: тест → код, прогон `python -m pytest -q`
   после каждого блока).
4. Прогон: `benchmark_day11.py --mock`, `scripts/e2e_day11.py`,
   (опционально) `--real`; обновление README.
5. Откат: `git checkout day10` — старые dialogues.json читаются (новые
   ключи в dialogues.json не добавляются); working_memory.json/memory.json
   day10-кодом игнорируются (лишние файлы).

## Open Questions

- Нет блокирующих: детали формата LT-блока и имён HTTP-полей фиксируются
  тестами/README при реализации; эвристика вердикта `--real` (ключевые слова
  сценария) подбирается при прогоне.
