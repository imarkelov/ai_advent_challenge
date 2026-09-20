# Tasks: day13b-task-redesign

Порядок: TDD, бэкенд → API → фронтенд → e2e → доки. Каждая единица —
«тесты сначала, потом реализация», коммит после зелёного блока.
Спецификация: `specs/task-state-machine/spec.md` (delta); «как» —
`design.md`.

## 1. Бэкенд: состояние задачи (memory.py)

- [ ] 1.1 `studio/backend/tests/test_memory.py`: переписать/расширить
      task-раздел под новую схему — `task_create` (новый `task_id`,
      stage=planning, `plan` 4 записи pending, `expected_action=
      agent_response`; 400/ValueError при активной незавершённой
      задаче; при последней задаче `done`/`failed` — новая задача,
      `task_id` другой), unified stage (включая `paused`/`failed`,
      `task_set_stage`/`task_stage_done` с переходом только вперёд по
      FSM), `work_steps` CRUD (статусы pending→in_progress→completed,
      output+ts), `task_set_expected_action`, `task_set_instruction`
      (только paused), `task_snapshot` (запись `context_snapshot`),
      `task_reset`, бэкворд-совместимость (отсутствие поля =
      неактивна; старое поле без `task_id` не ломает чтение) —
      сначала failing, потом реализация в `memory.py` (методы под
      единым lock, атомарная запись).
- [ ] 1.2 Коммит: `feat(day13b-task): новая схема состояния задачи
      (task_id, unified stage, plan/work_steps, expected_action,
      context_snapshot)`. Верификация: `python -m pytest -q` зелёный.

## 2. Бэкенд: оркестратор (agent.py)

- [ ] 2.1 `studio/backend/tests/test_agent.py`: переписать
      task-раздел — сценарный mock-LLM (MockTransport, ответы по
      system-промпту): полный цикл planning→execution (N work-шагов,
      по вызову на шаг)→validation→done; события `agent_spawned` /
      `step_updated` / `step_delta` / `stage_done` / `task_done` в
      порядке; структурированный JSON-план Планировщика → `work_steps`
      (сбой парсинга → план 1 шаг, цикл не ломается); промпт каждого
      work-шага содержит описание + план + выводы предыдущих шагов +
      instruction (история чата НЕ входит — проверка по телу
      запроса); instruction на паузе → в следующий промпт, затем
      очищена; валидация fail → повтор work-шагов с фидбэком, 2-й
      fail → done с пометкой, битая метка → pass; пауза на границе
      work-шага → `task_paused`, следующий шаг не вызван; resume →
      продолжение без повторных вызовов; ошибка LLM → `task_failed`,
      stage=failed, повтор через run; `run` на done → ValueError.
- [ ] 2.2 Реализация в `agent.py`: константы stage-промптов (новый
      формат JSON-плана для Планировщика, work-step-промпт
      Исполнителя, вердикт Валидатора, синтез), парсер JSON-плана
      (best-effort, лимит 1–5 шагов, фолбэк 1 шаг), перестроенный
      `task_run`: цикл стадий × work-шаги (streaming вызовы шагов →
      `step_delta`), `context_snapshot` из состояния, paused/failed
      как стадии, `expected_action`, события из spec; стриминг
      LLM-вызова (паттерн `ask_stream`).
- [ ] 2.3 Коммит: `feat(day13b-task): оркестратор — пошаговое
      исполнение, JSON-план, step-события, paused/failed как стадии`.
      Верификация: `python -m pytest -q` зелёный.

## 3. Бэкенд: API (main.py)

- [ ] 3.1 `studio/backend/tests/test_api.py`: обновить task-раздел —
      `start` (200 + `task_id`; 400 при незавершённой задаче включая
      paused; после done/failed — новая задача; 400 пустое
      description; 404 диалог), `run` (SSE с новыми событиями; 400
      без задачи / на done; на failed — повтор), `pause` (400 без
      активной или на done/failed), `resume` (400 без паузы),
      `instruction` (400 вне паузы), `reset`, `GET /api/task` (новая
      схема в ответе), `task` в выдаче `GET /api/dialogues` /
      `/api/dialogues/{id}`, гард `/api/chat` (SSE error, сообщение не
      пишется).
- [ ] 3.2 Реализация в `main.py`: обновить роуты `/api/task/*` под
      новую схему и семантику (D9); 400/404 RU-detail.
- [ ] 3.3 Коммит: `feat(day13b-task): API — новая семантика
      start/run/pause/resume (task_id, done/failed → новая задача,
      failed → повтор)`. Верификация: `python -m pytest -q` зелёный.

## 4. Фронтенд: api + state

- [ ] 4.1 `studio/frontend/src/api.ts`: типы новой TaskState
      (`task_id`, unified `stage`, `plan[]`, `work_steps[]`,
      `expected_action`, `current_step`/`total_steps`) и событий
      (`agent_spawned`/`step_updated`/`step_delta`/`stage_done`/
      `task_paused`/`task_resumed`/`task_done`/`task_failed`/`error`);
      `taskStream` — обработчики новых событий; удаление устаревших
      (`stage`/`stage_done`-старой формы).
- [ ] 4.2 `studio/frontend/src/state.tsx`: reducer под новые события и
      схему; состояние тумблера режимов (`chatMode: 'chat'|'task'`,
      localStorage, default 'chat'); action запуска задачи сообщением
      (start+run) и instruction (paused); перечитывание
      `/api/dialogues`+`/api/task` после стрима; Vitest-тесты
      (state-машина: спавн/шаги/пауза/done/failed, тумблер).
- [ ] 4.3 Коммит: `feat(day13b-task): frontend api+state — новые
      события, схема, тумблер режимов`. Верификация: `npm test` +
      `npx tsc --noEmit` зелёные.

## 5. Фронтенд: карточка процесса + тумблер, удаление TaskTab

- [ ] 5.1 Новый `components/TaskCard.tsx` + тесты (Vitest): заголовок
      (описание, статус-бейдж, кнопки Пауза/Продолжить/Повтор по
      состоянию), прогресс-бар (`current_step/total_steps` + «Этап
      N/4: <label>»), секции `plan[]` (`<details>/<summary>`, паттерн
      `.msg-details`): SVG-иконка агента, имя, бейдж статуса,
      «спавн X с назад» (relative-время); тело: Планировщик — план,
      Исполнитель — чек-лист `work_steps` [✓]/[⏳]/[ ] + живой бокс
      (`step_delta`), Валидатор — вердикт, Оркестратор — ответ;
      авто-развёртывание активного агента, сворачивание при
      завершении (DONE — всё свёрнуто; FAILED — секция ошибки
      развёрнута); восстановление из маркеров сообщений
      (`task_id`/`task_stage`/`task_step`), бэкворд-рендер старых
      маркеров без `task_step`.
- [ ] 5.2 `ChatPanel.tsx` + тесты: рендер TaskCard после
      сообщения-запроса (в потоке, chain на несколько задач); тумблер
      режимов у поля ввода (блокировка при активной непаузанной
      задаче); семантика ввода (чат — обычный ответ; задача —
      запуск; paused — instruction, плейсхолдеры); удаление кнопки
      Стоп из шапки и статус-строки стадий; чип «задача» на
      stage-сообщениях убрать (маркеры рендерятся внутри карточки).
- [ ] 5.3 Удаление `TaskTab.tsx`, 5-й вкладки в `ContextPanel.tsx`,
      её тестов; `styles.css`: стили карточки (progress-bar,
      agent-секции, чек-лист, живой бокс, тумблер) на существующих
      токенах; cleanup мёртвых классов (чипы стадий, статус-строка).
- [ ] 5.4 Коммит: `feat(day13b-task): UI — карточка процесса в чате,
      тумблер режимов, удаление вкладки «Задача»`. Верификация:
      `npm test` + `npx tsc --noEmit` зелёные.

## 6. E2E

- [ ] 6.1 `scripts/e2e_studio.py`: переписать блок «Задача» —
      детерминированное ядро (start → run SSE: `agent_spawned`
      planning → … → pause на границе work-шага → instruction →
      resume → `task_done` → reset; `GET /api/task` + task в выдаче
      диалога, новая схема) + live best-effort (полный пайплайн:
      события спавна/шагов, step-сообщения с маркерами в истории,
      карточка восстанавливается после перечитывания диалога,
      `run` на failed → повтор; чат-режим: сообщение не создаёт
      задачу); exit 0 = PASS/SKIP, 1 = FAIL.
- [ ] 6.2 Прогон: полный бэкенд-пакет, фронтенд-пакет, e2e — всё
      зелёное; коммит: `test(day13b-task): e2e-блок «Задача» (новые
      события, пошаговый пайплайн, тумблер)`.

## 7. Документация

- [ ] 7.1 README.md (корень): обновить секцию «День 13» (режимы
      чат/задача, карточка процесса, пошаговое исполнение, новая
      схема состояния, новые SSE-события, обновлённая API-таблица,
      статус тестов).
- [ ] 7.2 `studio/backend/README.md`: обновить блок «Задача (день
      13)» — unified FSM, SSE-протокол `/api/task/run` (новые
      события), семантика эндпоинтов.
- [ ] 7.3 Коммит: `docs(day13b-task): README — режимы/карточка/новая
      схема/события`.

## 8. Финальная верификация

- [ ] 8.1 Полный прогон: `python -m pytest -q` (бэкенд), `npm test` +
      `npx tsc --noEmit` (фронтенд), `python -m py_compile` на
      e2e-скрипте, `python scripts/e2e_studio.py` (live, GPustack) —
      всё зелёное; зафиксировать новые baseline-счёты тестов.
- [ ] 8.2 Rebuild `dist` (`npm run build`), перезапуск uvicorn,
      ручная проверка: режим «задача» запускает пайплайн, карточка
      живая (спавн, шаги, живой вывод), пауза/продолжить/повтор,
      режим «чат» — обычный ответ; старая вкладка «Задача»
      отсутствует.
