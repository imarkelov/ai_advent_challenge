"""День 35 — агенты в формате Markdown (`agents/*.md`).

Определение агента — это **документ, а не код**: frontmatter с
метаданными плюс тело-промпт. Такой файл читается человеком (видно, что
инструктирует модель), диффится в ревью построчно, версионируется в git
и правится без правки Python.

Формат файла `agents/<name>.<execution>.md`:

```markdown
---
name: code-review
execution: cli
description: Ревью изменений — находки, severity, рекомендации
tools: [git, diff, corpus]
temperature: 0.1
---
Ты — код-ревьюер. ...
```

Два исполнения одного агента (`.api.md` / `.cli.md`) различаются формой
вывода и каналом, а не смыслом: API отдаёт структуру для веб-панели,
CLI — markdown для терминала, пайпа и CI.

Frontmatter парсится stdlib-ом: PyYAML в зависимостях проекта нет, а
NFR-1 запрещает новые пакеты. Поддерживается нужное подмножество YAML —
`key: value` и inline-списки `[a, b]`.

Модуль импортируется без побочных эффектов: диск не читается, пока не
создан `AgentRegistry`.
"""
import os
import re
from dataclasses import dataclass, field

AGENTS_DIRNAME = "agents"
AGENT_EXTS = (".md",)
EXECUTIONS = ("api", "cli")

# Обязательные поля frontmatter.
REQUIRED_FIELDS = ("name", "execution", "description")
# Значения по умолчанию для необязательных полей.
DEFAULT_TEMPERATURE = 0.2

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_FILENAME_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+?)"
                          r"\.(?P<execution>api|cli)\.md$")


class AgentError(Exception):
    """Ошибка определения агента (RU-сообщение)."""


@dataclass
class AgentSpec:
    """Определение агента: метаданные + системный промпт."""

    name: str
    execution: str
    description: str
    system_prompt: str
    tools: list = field(default_factory=list)
    temperature: float = DEFAULT_TEMPERATURE
    path: str = ""
    # True, если запрошенного исполнения не нашлось и взято другое.
    fallback: bool = False

    def to_dict(self, with_prompt: bool = False) -> dict:
        """Метаданные для API/UI; тело промпта — только по запросу."""
        out = {"name": self.name, "execution": self.execution,
               "description": self.description, "tools": list(self.tools),
               "temperature": self.temperature,
               "file": os.path.basename(self.path) if self.path else ""}
        if self.fallback:
            out["fallback"] = True
        if with_prompt:
            out["system_prompt"] = self.system_prompt
        return out


def _parse_scalar(raw: str):
    """Разобрать значение frontmatter: список, число, bool или строка."""
    v = raw.strip()
    if not v:
        return ""
    # Кавычки снимаем: "текст" / 'текст'
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    # Inline-список: [a, b, c]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def parse_frontmatter(text: str, where: str = "<text>") -> tuple:
    """Разделить md на (frontmatter-dict, тело).

    Возврат: (meta, body). Нет блока `---` → AgentError: файл агента без
    frontmatter бессмыслен (неизвестны имя и исполнение), и молча
    пропустить его нельзя — иначе агент «исчезнет» без объяснения.
    """
    if not text or not text.strip():
        raise AgentError(f"{where}: пустой файл агента")
    m = _FRONTMATTER_RE.match(text.lstrip("\ufeff"))
    if not m:
        raise AgentError(
            f"{where}: нет frontmatter-блока — файл агента должен "
            "начинаться со строки `---`")
    raw_meta, body = m.group(1), m.group(2)

    meta: dict = {}
    for lineno, line in enumerate(raw_meta.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            raise AgentError(
                f"{where}: строка {lineno} frontmatter без ':' — "
                f"ожидается `ключ: значение`, получено {s!r}")
        key, _, value = s.partition(":")
        key = key.strip()
        if not key:
            raise AgentError(
                f"{where}: строка {lineno} frontmatter с пустым ключом")
        meta[key] = _parse_scalar(value)
    return meta, (body or "").strip()


def agent_from_md(text: str, path: str = "") -> AgentSpec:
    """Разобрать определение агента из md-текста с валидацией.

    Проверяются: обязательные поля, допустимое `execution`, непустое
    тело, числовая `temperature`. Ошибки называют файл и причину —
    иначе правка md не давала бы эффекта молча.
    """
    where = path or "<text>"
    meta, body = parse_frontmatter(text, where)

    for f in REQUIRED_FIELDS:
        if f not in meta or meta[f] in ("", None):
            raise AgentError(f"{where}: обязательное поле `{f}` отсутствует "
                             "или пустое")
    name = str(meta["name"]).strip()
    execution = str(meta["execution"]).strip().lower()
    description = str(meta["description"]).strip()
    if execution not in EXECUTIONS:
        raise AgentError(
            f"{where}: недопустимое execution={execution!r} "
            f"(ожидается {'|'.join(EXECUTIONS)})")
    if not body:
        raise AgentError(f"{where}: пустое тело — агент без промпта "
                         "не имеет смысла")

    tools = meta.get("tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    if not isinstance(tools, list):
        raise AgentError(f"{where}: поле `tools` должно быть списком")

    temp = meta.get("temperature", DEFAULT_TEMPERATURE)
    try:
        temp = float(temp)
    except (TypeError, ValueError):
        raise AgentError(
            f"{where}: `temperature` должна быть числом, получено "
            f"{temp!r}")

    return AgentSpec(name=name, execution=execution,
                     description=description, system_prompt=body,
                     tools=[str(t) for t in tools], temperature=temp,
                     path=path)


class AgentRegistry:
    """Реестр агентов из каталога `agents/`.

    Кэш инвалидируется по mtime каталога и файлов: правка md
    подхватывается без перезапуска процесса (важно для CLI и для
    отладки промптов).
    """

    def __init__(self, agents_dir: str):
        self.agents_dir = os.path.abspath(agents_dir)
        self._cache: list | None = None
        self._stamp: tuple | None = None
        self._errors: list = []

    # ---------- загрузка ----------

    def _dir_stamp(self) -> tuple:
        """Отпечаток каталога: mtime и размер каждого md-файла."""
        try:
            names = sorted(os.listdir(self.agents_dir))
        except OSError:
            return ()
        stamp = []
        for fn in names:
            if not fn.endswith(AGENT_EXTS):
                continue
            try:
                st = os.stat(os.path.join(self.agents_dir, fn))
            except OSError:
                continue
            stamp.append((fn, int(st.st_mtime), st.st_size))
        return tuple(stamp)

    def load(self, force: bool = False) -> list:
        """Загрузить агентов (с кэшем). Битые файлы → `errors()`."""
        stamp = self._dir_stamp()
        if not force and self._cache is not None and stamp == self._stamp:
            return list(self._cache)

        specs: list = []
        errors: list = []
        if not os.path.isdir(self.agents_dir):
            # Каталога нет — это не падение: реестр просто пуст.
            self._cache, self._stamp, self._errors = [], stamp, []
            return []

        for fn in sorted(os.listdir(self.agents_dir)):
            if not fn.endswith(AGENT_EXTS):
                continue
            path = os.path.join(self.agents_dir, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                errors.append(f"{fn}: не читается ({e})")
                continue
            try:
                specs.append(agent_from_md(text, path=path))
            except AgentError as e:
                # Битый md не роняет реестр, но и не исчезает молча:
                # причина доступна через errors() и CLI её показывает.
                errors.append(str(e))

        self._cache, self._stamp, self._errors = specs, stamp, errors
        return list(specs)

    def errors(self) -> list:
        """Ошибки разбора последней загрузки (RU-тексты)."""
        self.load()
        return list(self._errors)

    # ---------- выборка ----------

    def list(self) -> list:
        """Метаданные всех агентов (без тел промптов)."""
        return [s.to_dict() for s in self.load()]

    def names(self) -> list:
        """Имена агентов (уникальные), в порядке файлов."""
        seen, out = set(), []
        for s in self.load():
            if s.name not in seen:
                seen.add(s.name)
                out.append(s.name)
        return out

    def get(self, name: str, execution: str = "cli") -> AgentSpec:
        """Агент по имени и исполнению.

        Если файла запрошенного исполнения нет — берётся другое
        исполнение того же агента с флагом `fallback=True`. Молчаливая
        подмена недопустима: пользователь думал бы, что читает
        CLI-инструкции, а получал API-текст.
        """
        if not name or not name.strip():
            raise AgentError("Не указано имя агента")
        name = name.strip()
        specs = [s for s in self.load() if s.name == name]
        if not specs:
            available = ", ".join(self.names()) or "(нет)"
            raise AgentError(f"Агент {name!r} не найден. Доступны: "
                             f"{available}")
        for s in specs:
            if s.execution == execution:
                return s
        other = specs[0]
        return AgentSpec(
            name=other.name, execution=other.execution,
            description=other.description, system_prompt=other.system_prompt,
            tools=list(other.tools), temperature=other.temperature,
            path=other.path, fallback=True)

    def prompt_for(self, name: str, execution: str = "cli") -> tuple:
        """(системный промпт, был ли fallback) — для вызова LLM."""
        spec = self.get(name, execution=execution)
        return spec.system_prompt, spec.fallback
