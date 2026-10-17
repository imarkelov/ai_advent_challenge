# Tasks: day30-local-llm-service

Задачи дня 30: локальная LLM как приватный сетевой сервис —
OpenAI-совместимый шлюз, ключи клиентов, rate limit, max context с
LLM-компрессией, учёт использования, вкладка «Сервис».
Финальные цифры — после задачи 9.

## Шлюз: ядро (TDD)
- [x] Task 1: `studio/backend/gateway.py` — `GatewayClient`
  (name/key_hash/rpm/tpm/max_context/max_output_tokens/allow_models/
  enabled), `hash_key` (SHA-256), `mask_key`, валидация лимитов;
  тесты СНАЧАЛА в `test_gateway.py`
- [x] Task 2: `GatewayConfig` — хранение клиентов в
  `studio/data/gateway.json`, `load`/`save`/`upsert`/`delete`,
  `find_by_key` (через `hmac.compare_digest`), дефолт из `.env`
  (`GATEWAY_CLIENT_KEY`), атомарная запись; тесты
- [x] Task 3: `RateLimiter` — скользящее окно 60 с по RPM и TPM на
  клиента, потокобезопасно, `check()` без побочных эффектов при
  отказе, `retry_after`; тесты (включая стык минут и потоковую гонку)
- [x] Task 4: `compress_messages()` — LLM-сводка старых ходов при
  упоре в `max_context`: system + последние `keep_recent` целиком,
  рекурсия (кап `MAX_COMPRESS_ROUNDS`), существующая сводка не
  пересжимается, деградация при падении LLM; тесты

## Шлюз: сервис (TDD)
- [x] Task 5: `UsageStore` — запросы/токены/отказы по клиентам,
  персистентно, кольцевой лог отказов; тесты
- [x] Task 6: `GatewayService.handle_chat()` — конвейер
  (аутентификация → модель → rate limit → подсчёт входа →
  компрессия → прокси в Ollama → учёт), ошибки в формате OpenAI
  (401/403/404/413/429/502), блок `gateway` в ответе; тесты
- [x] Task 7: стриминг (`stream: true`) — SSE-прокси через шлюз с
  учётом токенов после завершения потока; тесты

## Роуты (TDD)
- [x] Task 8: `studio/backend/main.py` — `POST /v1/chat/completions`,
  `GET /v1/models`, `GET /api/gateway/status`, `GET|POST
  /api/gateway/clients`, `DELETE /api/gateway/clients/{name}`,
  `GET /api/gateway/usage`, `GET /api/gateway/rejections`; тесты

## UI
- [x] Task 9: вкладка «Сервис» (`ServiceTab.tsx` + `ServiceOverlay.tsx`,
  кнопка в шапке, состояние в `state.tsx`, типы и вызовы в `api.ts`):
  LAN-адрес + пример подключения, таблица клиентов (создание/
  удаление/вкл-выкл/лимиты), живые счётчики, лог отказов

## Проверка (e2e)
- [x] Task 10: `scripts/e2e_day30.py` — Part A офлайн (TestClient:
  401/403/404/429/413, компрессия, учёт) + Part B live (реальный
  сервер `0.0.0.0:PORT`, реальный Ollama: запрос с «другого
  устройства» = отдельный HTTP-клиент, реальный rate limit, реальная
  компрессия длинного диалога, `ollama ps` CONTEXT)

## Документация
- [x] Task 11: README (строка дня 30 в таблице + секция), RELEASE.md
  (release notes day30-local-llm-service: что в релизе / API /
  проверка задания / безопасность / коммиты), `.env.example`
  (`GATEWAY_*`), evidence в `.omo/evidence/day30/`

## Находки живой проверки (закрыты в рамках дня)

- [x] Task 12: reasoning-модели и компрессия — сводка идёт цепочкой
  моделей × два пути вызова (OpenAI-совместимый + нативный `/api/chat`);
  `DEFAULT_SUMMARY_PREFERENCE` = `qwen3-coder:30b`, `gemma3:27b`
- [x] Task 13: пустой ответ модели помечается `gateway.empty_response`
  (+`empty_reason`, `finish_reason`, `reasoning_chars`) — клиент не
  получает молчаливую пустоту
- [x] Task 14: компрессия сокращает хвост, если `keep_recent` больше
  лимита, но никогда не выбрасывает последнее сообщение пользователя
- [x] Task 15: `lan_url` берёт фактический порт запроса (был хардкод
  `DEFAULT_GATEWAY_PORT` → неверный адрес при нестандартном порте)
- [x] Task 16: `scripts/demo_day30_service.py` — живая демонстрация
  (LAN-запрос, 401, ключ один раз, SHA-256 на диске, реальный ответ,
  rate limit, Ollama закрыта наружу)

## Финальные цифры

- офлайн-тесты шлюза: `studio/backend/tests/test_gateway.py` —
  107 тестов, `test_gateway_api.py` — 51 тест (всего 158)
- полная регрессия бэкенда: **959 passed, 6 skipped**
- `scripts/e2e_day30.py`: **Part A 13/13 + Part B 8/8 = PASS 21, FAIL 0**
- живая демонстрация: LAN-доступ, реальная модель, rate limit 3 → 429
