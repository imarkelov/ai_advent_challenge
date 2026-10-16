# Tasks: day35-cli-md-agents

День 35: терминальный вход в ассистента (CLI поверх того же
`AssistantService`) и определения агентов в Markdown — 4 агента × 2
исполнения. Финальные цифры — после задачи 9.

## Определения агентов (md)
- [x] Task 1: `agents/*.md` — 8 файлов (4 агента × `.api.md`/`.cli.md`):
  `project-qa`, `code-review`, `support`, `file-ops`; frontmatter
  (`name`, `execution`, `description`, `tools`, `temperature`) + тело
  промпта, различающееся по исполнению (API — структура/UI, CLI —
  markdown/пути файлов)

## Ядро (TDD)
- [x] Task 2: `parse_agent_md()` — разбор frontmatter и тела без YAML-
  пакета, валидация обязательных полей и `execution` (api|cli),
  RU-ошибки с указанием файла; тесты СНАЧАЛА в `test_agents.py`
- [x] Task 3: `AgentRegistry` — загрузка `agents/*.md`, `list()`,
  `get(name, execution)` с деградацией на другое исполнение и флагом
  `fallback`, кэш по mtime, устойчивость к пустому каталогу; тесты
- [x] Task 4: интеграция промпта — `AssistantService` принимает
  системный промпт агента для `ask`/`support`/`review` (без изменения
  существующего поведения по умолчанию); тесты

## CLI (TDD)
- [x] Task 5: `studio/cli.py` — каркас: argparse, подкоманды,
  типизация `--param` (`true/false/none/числа/строки`), вывод
  markdown/`--json`/`--out`, коды возврата, ошибки в stderr без
  traceback (только `--debug`); тесты в `test_cli.py`
- [x] Task 6: команды `ask`, `review`, `support`, `tickets` + выбор
  агента (`--agent`, дефолты по команде); тесты
- [x] Task 7: команды `scenario`, `scenarios`, `index`, `status`,
  `git`; тесты (включая `dry_run=false` → файлы НЕ пишутся)

## Проверка (e2e)
- [x] Task 8: `scripts/e2e_day32.py` — Part A офлайн (CLI как реальный
  подпроцесс: agents list/show, ask, review с находками, support с CRM,
  5 сценариев, dry_run=false, коды возврата, `--json`, отказ записи
  вне allow-list) + Part B live (реальная Ollama через CLI)

## Документация
- [x] Task 9: README (строка дня 35 в таблице + секция с примерами
  команд), RELEASE.md (release notes), evidence в
  `.omo/evidence/day35/`

## Финальные цифры

- тесты: `test_agents.py` — **46**, `test_cli.py` — **59** (всего 105)
- полная регрессия бэкенда: **1193 passed, 6 skipped** (до дня 35 — 1088)
- `scripts/e2e_day32.py`: **Part A 26/26 + Part B 5/5 = PASS 31, FAIL 0, SKIP 0**
- агенты: **8 файлов** (4 агента × api/cli), промпты исполнений различаются
- CLI: 10 команд, коды возврата 0/1, ошибки без traceback
- evidence: `.omo/evidence/day35/`
