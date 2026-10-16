# Шпаргалка: как посмотреть CLI ассистента руками

Всё запускается **из корня репозитория**. Подставьте свой путь к питону,
если он другой (`.venv/bin/python`).

```bash
cd /Users/igorvladimirovich/IdeaProjects/ai_advent_challenge
PY=.venv/bin/python
CLI=studio/backend/cli.py
```

> **Совет:** начните с быстрых команд (`--no-llm`) — они отвечают мгновенно
> и не грузят модель. Команды с LLM (`ask`, `support` без `--no-llm`)
> первый раз думают ~10–60 с, пока Ollama грузит веса.

> **Важно про `--no-llm`.** Он есть в двух местах и это разные вещи:
> * **глобальный** — ставится ДО команды: `$PY $CLI --no-llm status`
>   (отключает LLM полностью, агент не создаётся);
> * **локальный** у `review` — ставится ПОСЛЕ команды:
>   `$PY $CLI review --no-llm` (отключает только LLM-часть ревью).
>
> Если поставить глобальный флаг после команды (`status --no-llm`),
> argparse вернёт код 2 «unrecognized arguments».

---

## 0. Общая справка

```bash
$PY $CLI --help
$PY $CLI <команда> --help          # справка по конкретной команде
```

---

## 1. Агенты (это то, что в формате Markdown)

```bash
# Список всех 8 агентов: 4 имени × 2 исполнения (api / cli)
$PY $CLI agents list

# То же машинно (JSON)
$PY $CLI agents list --json

# Карточка агента: метаданные
$PY $CLI agents show code-review

# Главное: ПОКАЗАТЬ САМ ПРОМПТ из md-файла
$PY $CLI agents show code-review --prompt

# Сравнить два исполнения одного агента — тексты разные
$PY $CLI agents show code-review --execution api --prompt
$PY $CLI agents show code-review --execution cli --prompt
```

Откуда это берётся — откройте сами файлы:

```bash
cat agents/code-review.cli.md      # frontmatter + промпт
cat agents/code-review.api.md
```

Правка промпта подхватывается **без перезапуска**: отредактируйте
`agents/code-review.cli.md`, сохраните и снова запустите `agents show ... --prompt`.

---

## 2. Состояние и git (мгновенно)

```bash
$PY $CLI --no-llm status     # корпус, индекс, CRM, LLM, git
$PY $CLI --no-llm git        # ветка, изменённые файлы, размер diff
```

---

## 3. Индекс проекта (один раз, ~5–10 с)

```bash
$PY $CLI index               # собрать индекс по документации и коду
```

Собранный индекс нужен для `ask`/`support`. Лежит в `data/kb/project/`
(gitignored). День 21 (`data/kb/index.db`) не трогается.

---

## 4. Ревью изменений — работает БЕЗ LLM (мгновенно)

```bash
# Ревью изменений рабочего дерева (ваш реальный git diff)
$PY $CLI --no-llm review

# Ревью конкретной ветки
$PY $CLI --no-llm review --base master

# Ревью заготовленного diff из файла
$PY $CLI --no-llm review --diff /путь/к/file.diff

# JSON (удобно смотреть поля)
$PY $CLI --no-llm review --json | head -40
```

**Код возврата для CI** — попробуйте так:

```bash
$PY $CLI --no-llm review --fail-on-high ; echo "код: $?"
```

Чтобы увидеть находки уровня «критично», скормите такой diff:

```bash
cat > /tmp/bad.diff <<'EOF'
diff --git a/studio/backend/mod.py b/studio/backend/mod.py
--- a/studio/backend/mod.py
+++ b/studio/backend/mod.py
@@ -1,2 +1,9 @@
 """Модуль."""
+import os
+API_KEY = "sk-abcdef1234567890abcd"
+def g():
+    try:
+        pass
+    except:
+        pass
 x = 1
EOF
$PY $CLI --no-llm review --diff /tmp/bad.diff
$PY $CLI --no-llm review --diff /tmp/bad.diff --fail-on-high ; echo "код: $?"
```

---

## 5. Тикеты и поддержка

```bash
# Список тикетов из CRM (мгновенно)
$PY $CLI --no-llm tickets
$PY $CLI --no-llm tickets --status open
$PY $CLI --no-llm tickets --priority high

# Ответ по тикету БЕЗ LLM (по правилам + данные CRM)
$PY $CLI --no-llm support T-5003

# То же С реальной моделью (первый раз дольше)
$PY $CLI support T-5003

# Несуществующий тикет — деградация, не падение
$PY $CLI --no-llm support T-9999
```

Тикеты живут в `data/crm/tickets.json` — можно править и смотреть, как
меняется ответ.

---

## 6. Вопрос по проекту (RAG; нужен индекс + LLM)

```bash
$PY $CLI ask "как устроен шлюз локальной LLM?"
$PY $CLI ask "как работает компрессия контекста?" --json | head -30

# Сравнить агентов на ОДНОМ вопросе — ответы в разном стиле
$PY $CLI ask "что делать с длинными строками?" --agent code-review
$PY $CLI ask "что делать с длинными строками?" --agent project-qa

# Без модели (детерминированно, мгновенно)
$PY $CLI --no-llm ask "как устроен шлюз?"
```

---

## 7. Файловые сценарии (автономная работа с файлами)

```bash
# Каталог сценариев
$PY $CLI --no-llm scenarios

# 1) Где используется символ
$PY $CLI --no-llm scenario find_usages --param symbol=compress_messages
$PY $CLI --no-llm scenario find_usages --param symbol=compress_messages --json

# 2) Проверка инвариантов проекта
$PY $CLI --no-llm scenario check_invariants

# 3) Генерация ADR — СНАЧАЛА безопасно (dry-run, ничего не пишет)
$PY $CLI --no-llm scenario generate --param kind=adr \
    --param title="Выбор базы данных" --param dry_run=true

#    затем реальная запись (появится docs/adr/0001-....md)
$PY $CLI --no-llm scenario generate --param kind=adr \
    --param title="Выбор базы данных" --param dry_run=false
ls -la docs/adr/
cat docs/adr/*.md

# 4) Отчёт об изменениях
$PY $CLI --no-llm scenario diff_report
$PY $CLI --no-llm scenario diff_report --param path=docs/report.md
```

> ⚠️ Сценарий `generate` **пишет файл** (в `docs/`, `README.md`,
> `CHANGELOG.md`, `openspec/`). Чтобы убрать следы: `rm -rf docs/adr`.

---

## 8. Что стоит проверить самому (самое интересное)

**а) Защита от записи вне проекта:**

```bash
$PY $CLI --no-llm scenario generate --param kind=readme --param path=../evil.md
echo "код: $?"        # ожидается 1
ls ../evil.md         # файла быть не должно
```

**б) `dry_run=false` реально пишет** (это тот самый баг типизации):

```bash
rm -rf docs/adr
$PY $CLI --no-llm scenario generate --param kind=adr --param title=T1 --param dry_run=true
ls docs/adr 2>/dev/null || echo "dry-run: файла нет — верно"

$PY $CLI --no-llm scenario generate --param kind=adr --param title=T2 --param dry_run=false
ls docs/adr      # файл появился
```

**в) Ошибки без traceback:**

```bash
$PY $CLI --no-llm agents show неттакого ; echo "код: $?"
$PY $CLI --no-llm scenario неттакого ; echo "код: $?"
```

**г) JSON-вывод для скриптов:**

```bash
$PY $CLI --no-llm status --json | python3 -m json.tool | head -20
```

**д) Запись отчёта в файл:**

```bash
$PY $CLI --no-llm review --out /tmp/review.md ; cat /tmp/review.md
```

---

## 9. Автотесты (если хотите увидеть проверки целиком)

```bash
# только CLI и агенты (быстро, офлайн)
.venv/bin/python -m pytest studio/backend/tests/test_cli.py \
    studio/backend/tests/test_agents.py -q

# e2e офлайн: CLI как реальный подпроцесс
.venv/bin/python scripts/e2e_day32.py --part-a-only

# e2e целиком, с живой моделью
.venv/bin/python scripts/e2e_day32.py
```

---

## Если что-то не так

| Симптом | Причина / что делать |
|---|---|
| `ask`/`support` долго молчит | Ollama грузит модель в первый раз; подождите или добавьте `--no-llm` |
| `LLM: недоступна` в `status` | Ollama не запущена; команды с `--no-llm` всё равно работают |
| «Индекс проекта не построен» | выполните `$PY $CLI index` |
| `ModuleNotFoundError` | запускайте из **корня** репозитория, путь `studio/backend/cli.py` |
| Хочу другой порт/адрес Ollama | переменная `OLLAMA_BASE_URL` в `.env` |
