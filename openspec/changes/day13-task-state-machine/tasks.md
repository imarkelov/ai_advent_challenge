# Tasks: day13-task-state-machine

Порядок: TDD, бэкенд → API → фронтенд → e2e → доки. Каждая единица —
«тесты сначала, потом реализация», коммит после зелёного блока.

## 1. Storage: состояние задачи в MemoryStore

- [x] 1.1 `studio/backend/tests/test_memory.py` (новый раздел):
      тесты task-методов — `task_create` (stage=planning, active,
      400/ValueError при любой активной задаче, включая done), `task_set_stage`/
      `task_stage_done` (output+ts, переход только вперёд по FSM,
      backward/прыжок игнорируется), `task_pause`/`task_resume` (флаг,
      ValueError вне условий), `task_set_instruction` (только paused,
      очистка после применения), `task_set_error`, `task_reset`
      (active=false, поля чистые), `task_get` (отсутствие поля =
      неактивна, бэкворд-совместимость), task в `list_dialogues`/
      `get_dialogue` — сначала failing, потом реализация в `memory.py`
      (методы под единым lock, атомарная запись, паттерн `profile_*`).
- [x] 1.2 Коммит: `feat(day13-task): хранилище состояния задачи per-диалог
      (FSM-стадии, paused, instruction, outputs, reset)`

## 2. Агент: оркестратор пайплайна

- [x] 2.1 `studio/backend/tests/test_agent.py` (новый раздел
      `test_task_run*`): сценарный mock-LLM (MockTransport: ответ
      определяется по system-промпту стадии):
      - полный FSM-цикл planning→execution→validation→done: порядок
        вызовов, system-промпт каждой стадии содержит description и
        outputs предыдущих стадий, events
        `stage`/`stage_done`/`task_done`, outputs в `task` state и в
        messages (метка `task_stage`), финальный синтез в assistant-
        сообщении с `model`;
      - инъекция instruction: на паузе задана → в промпт следующей
        стадии, затем очищена из state;
      - валидация: `fail` → повтор execution (фидбэк: plan + work +
        замечания в промпте), 2-й `fail` → done с сохранённым verdict,
        битая/отсутствующая метка `<verdict>` → pass без ошибки;
      - пауза: pause-флаг между стадиями → event `task_paused` на
        границе, следующая стадия не вызвана; после resume (run) —
        продолжение с сохранённого state без новых вызовов выполненных
        стадий;
      - ошибка LLM-вызова → event `error`, state с `error` на текущей
        стадии, повтор той же стадии при следующем run.
- [x] 2.2 Реализация в `agent.py`: константы stage-промптов
      (Планировщик/Исполнитель/Валидатор/Синтез, паттерн
      `PROFILE_*_TEXT`), построение блока «Состояние задачи»,
      `task_run(dialogue_id)` — синхронный генератор-цикл (паттерн
      `ask_stream`): выполнить стадию → сохранить output → проверить
      stop-флаг/паузу → следующая; парсинг `<verdict>` (best-effort);
      non-stream для всех стадий, включая синтез (событий `delta` в task-стриме нет).
- [x] 2.3 Гард в `ask_stream`/`/api/chat`: активная непаузанная задача →
      error-событие/400 RU, сообщение в диалог не пишется (тест в
      `test_agent.py` + `test_api.py`).
- [x] 2.4 Коммит: `feat(day13-task): оркестратор пайплайна stage-агентов
      (FSM-цикл, SSE-события, verdict-ретрай, пауза на границе стадии,
      гард /api/chat)`

## 3. API: эндпоинты /api/task/*

- [x] 3.1 `studio/backend/tests/test_api.py` (новый раздел):
      `POST /api/task/start` (200; 400 при любой активной задаче, включая done;
      400 пустое description; 404 диалог), `POST /api/task/run`
      (SSE: stage/stage_done/task_done; 400 без задачи), `pause`
      (400 без активной), `resume` (400 без паузы), `instruction`
      (400 вне паузы; 400 не-str text), `reset`, `GET /api/task`
      (active=false без задачи; полное состояние с задачей), `task` в
      ответах `GET /api/dialogues` и `GET /api/dialogues/{id}`.
- [x] 3.2 Реализация в `main.py`: роуты `/api/task/*` (валидация
      400/404 RU-detail, паттерн существующих).
- [x] 3.3 Коммит: `feat(day13-task): REST /api/task (start/run/pause/
      resume/instruction/reset/get) + task в выдаче диалогов`

## 4. Фронтенд: api + state

- [x] 4.1 `studio/frontend/src/api.ts`: `apiTaskGet/Start/Pause/Resume/
      Instruction/Reset` + `taskRun` (SSE через fetch/ReadableStream,
      парсер событий `stage`/`stage_done`/`task_paused`/`task_done`/
      `error`, паттерн `chatStream`).
- [x] 4.2 `studio/frontend/src/state.tsx`: `task` в reducer (из
      dialogues), экшены (запуск/пауза/резюм/instruction/reset,
      stage-события из SSE), перечитывание `/api/dialogues`+`/api/task`
      после завершения стрима; тесты Vitest (unit state-машину).
- [x] 4.3 Коммит: `feat(day13-task): frontend api + state (task, SSE
      taskRun, перечитывание после стрима)`

## 5. Фронтенд: UI (TaskTab + ChatPanel)

- [x] 5.1 `components/TaskTab.tsx` + тесты: описание, стадии с outputs
      (текущая/выполненные), вердикт, instruction, paused/error-чипы,
      кнопки Пауза/Продолжить/Новая задача (доступность по состоянию),
      переключение диалога; встроить в `ContextPanel.tsx` (вкладка
      «Задача»).
- [x] 5.2 `ChatPanel.tsx` + тесты: кнопка «Задача» (режим: поле ввода =
      описание, start+run), кнопка **Стоп/Продолжить** в шапке (стоп →
      pause, пауза → Продолжить запускает run), статус-строка стадий
      (4 чипа: текущая — спиннер «агент работает…», пройденные ✓, done —
      финальная отметка), блокировка поля ввода во время стадии, на
      паузе — pлейсхолдер инструкции (отправка → instruction).
- [x] 5.3 `styles.css`: чипы стадий, статус-строка, paused/error
      (существующие токены).
- [x] 5.4 Коммит: `feat(day13-task): UI — режим «Задача», стоп/резюм в
      чате, статус-строка стадий, вкладка «Задача»`

## 6. E2E

- [x] 6.1 `scripts/e2e_studio.py`: блок «Задача»: детерминированное ядро
      (start → run SSE ≥ stage(planning)+stage_done → pause →
      instruction → resume → task_done → reset; state через
      `GET /api/task` и в выдаче диалога) + live best-effort полный
      пайплайн (3 модели: stage-события, stage-сообщения с метками,
      финальный синтез; чат-часть SKIP при недоступности GPustack,
      паттерн дня 12). Exit 0 = PASS/SKIP, 1 = FAIL.
- [x] 6.2 Прогон: полный бэкенд-пакет, фронтенд-пакет, e2e — всё
      зелёное; коммит: `test(day13-task): e2e-блок «Задача» (ядро
      start→pause→instruction→resume→done→reset + live-пайплайн)`

## 7. Документация

- [x] 7.1 README.md (корень): секция «День 13» (задание, архитектура
      оркестратора + stage-агентов, FSM, пауза/продолжение, API-таблица,
      статус тестов) + строка в таблице веток.
- [x] 7.2 `studio/backend/README.md`: блок «Задача (день 13)» — FSM,
      SSE-протокол `/api/task/run`, новые эндпоинты.
- [x] 7.3 Коммит: `docs(day13-task): секция «День 13» в README +
      API/протокол в backend-README`
