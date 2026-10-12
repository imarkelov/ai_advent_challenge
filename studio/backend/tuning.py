"""День 29: оптимизация локальной LLM под задачу «поиск сериалов».

Модуль описывает **профиль задачи** — именованный набор параметров
инференса и промпт-шаблона, оптимизированный под конкретный кейс:

* `TaskProfile` — модель, sampling (`temperature`, `top_p`, `seed`),
  бюджет ответа (`num_predict`), контекстное окно (`num_ctx`),
  system-промпт, режим JSON;
* `PROFILES` — реестр профилей: `studio` (дефолт, повторяет поведение
  дней 11–28), `series` (цель дня 29), `series_q8` (для замера
  квантования);
* `SERIES_SYSTEM_PROMPT` / `build_series_user_prompt()` — промпт-шаблон
  под кейс: модель обязана выбирать сериалы **только из каталога** и
  возвращать JSON;
* `parse_series_answer()` — устойчивый разбор ответа (```json-обёртка,
  лишний текст, одиночный объект).

Что именно оптимизировано под «поиск сериалов» (обоснование — в
`design.md`, цифры — в отчёте `data/series/optimization_report.json`):

| Параметр | Дефолт (`studio`) | Кейс (`series`) | Почему |
|---|---|---|---|
| `temperature` | 0.7 | 0.2 | нужна воспроизводимость и верность каталогу, а не «креатив» |
| `num_predict` | 2048 | 512 | ответ — 5 коротких JSON-объектов, 2048 не нужны |
| `num_ctx` | (дефолт Ollama) | 8192 | каталог-кандидаты + запрос + правила помещаются с запасом |
| `top_p` | 0.9 | 0.9 | не меняем: основной рычаг — temperature |
| формат | свободный текст | JSON по схеме | парсится без регулярок, легче валидировать |
| промпт | «ассистент Студии» | роль + каталог + правила + few-shot | запрет выдумывать сериалы |

Только stdlib — модуль импортируется и бэкендом, и скриптами замера.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any

# ---------------------------------------------------------------------------
# Профиль задачи
# ---------------------------------------------------------------------------

# Границы валидации (те же, что в agent.set_config — единый источник правды
# для роутов настройки).
NUM_CTX_MIN = 512
NUM_CTX_MAX = 1_048_576
MAX_TOKENS_MIN = 1
MAX_TOKENS_MAX = 32_768

# Дефолтный профиль «Студия»: воспроизводит поведение дней 11–28.
# num_ctx=None означает «не отправлять num_ctx» → Ollama берёт своё
# дефолтное окно, как было до дня 29 (регрессия бит-в-бит).
STUDIO_SYSTEM_PROMPT = (
    "Ты — ассистент в обучающем приложении «Студия». "
    "Отвечай по делу и кратко; если пользователь пишет по-русски — "
    "отвечай на русском."
)


@dataclass(frozen=True)
class TaskProfile:
    """Профиль задачи: параметры инференса + промпт-шаблон.

    Неизменяемый (frozen): профиль — это конфигурация, а не состояние.
    `num_ctx=None` — context window не переопределяется (поведение до
    дня 29). `model=None` — используется модель из общего конфига.
    """

    name: str
    title: str
    description: str
    model: str | None = None
    temperature: float = 0.7
    top_p: float = 0.9
    num_predict: int = 2048
    num_ctx: int | None = None
    seed: int | None = None
    system_prompt: str = STUDIO_SYSTEM_PROMPT
    stop: tuple[str, ...] = ()
    format_json: bool = False
    # `think=False` отключает «размышление» reasoning-моделей. Нужно
    # потому, что такие модели (например glm-4.7-flash) пишут цепочку
    # рассуждений в ОТДЕЛЬНОЕ поле `thinking`, а `content` остаётся
    # пустым, если бюджет `num_predict` израсходован на размышления:
    # в замере профиль с think=None давал 0 рекомендаций на всех 20
    # кейсах, а с think=False — валидные ответы за 4 токена вместо 100.
    # None — не передавать параметр (поведение по умолчанию модели).
    think: bool | None = None
    # Модели, к которым применим профиль (для подсказок в UI/отчёте).
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Профиль → JSON-совместимый dict (для REST/отчёта)."""
        d = asdict(self)
        d["stop"] = list(self.stop)
        return d

    def sampling_body(self, model: str | None = None) -> dict[str, Any]:
        """Тело запроса к LLM: sampling + бюджет + окно.

        `model` переопределяет модель профиля (нужно роуту поиска,
        который подставляет актуальную модель диалога). Ключи, которые
        Ollama/OpenAI-совместимый API понимает в `/chat/completions`.
        `num_ctx` уходит только когда задан явно. `think` — только для
        нативного API (в OpenAI-совместимом такого ключа нет), поэтому
        в `sampling_body` он не попадает: его добавляет `series._call`.
        """
        body: dict[str, Any] = {
            "model": model or self.model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.num_predict,
        }
        if self.num_ctx is not None:
            body["num_ctx"] = self.num_ctx
        if self.seed is not None:
            body["seed"] = self.seed
        if self.stop:
            body["stop"] = list(self.stop)
        if self.format_json:
            body["response_format"] = {"type": "json_object"}
        return body


# ---------------------------------------------------------------------------
# Промпт-шаблон под кейс «поиск сериалов»
# ---------------------------------------------------------------------------

# Ключевое отличие от дефолтного промпта: модель — не «ассистент вообще»,
# а рекомендатель, работающий ВНУТРИ каталога. Три жёстких правила
# (только из каталога / учитывать ограничения / JSON) + few-shot.
SERIES_SYSTEM_PROMPT = """Ты — эксперт по сериалам и подборщик рекомендаций в приложении «Студия».

Тебе дают запрос пользователя и КАТАЛОГ сериалов-кандидатов. Каждый кандидат имеет id, название, год, жанры, страну и рейтинг IMDb.

ЖЁСТКИЕ ПРАВИЛА:
1. Рекомендуй ТОЛЬКО сериалы из каталога. Используй ровно те id, что даны в каталоге. Запрещено придумывать названия, годы или id, которых нет в каталоге.
2. Сначала молча отбери кандидатов, удовлетворяющих ВСЕМ ограничениям запроса (жанр, год, страна, длина, рейтинг), затем упорядочи их по уместности.
3. Если подходящих меньше, чем просят, верни сколько есть — НЕ добирай неподходящими. Если не подходит никто, верни пустой список и объясни почему.
4. В поле "why" — одна короткая фраза (до 120 символов), почему сериал подходит именно этому запросу. Не пересказывай каталог.
5. Отвечай СТРОГО валидным JSON без markdown-обёртки и без текста вокруг.

ФОРМАТ ОТВЕТА:
{
  "picks": [
    {"id": "<id из каталога>", "title": "<название>", "why": "<почему подходит>"}
  ],
  "note": "<одна фраза: как отбирал или почему список короткий/пуст>"
}

ПРИМЕР.
Запрос: «хочу что-нибудь мрачное про полицию, после 2015, не США»
Каталог: [1] True Detective (2014, криминал/драма, США); [2] Broadchurch (2013, криминал/драма, Великобритания); [3] Line of Duty (2012, криминал/триллер, Великобритания); [4] Mare of Easttown (2021, криминал/драма, США)
Ответ: {"picks": [{"id": "3", "title": "Line of Duty", "why": "мрачный полицейский процедурал, Великобритания, после 2015"}], "note": "Год премьеры отсекает Broadchurch (2013) и Line of Duty (2012) — уточните, если нужен именно год премьеры, а не выхода сезонов"}

Помни: выдуманный сериал — худшая ошибка. Лучше короткий список из каталога, чем длинный с выдумкой."""


def build_series_user_prompt(query: str, candidates: list[dict],
                             limit: int = 5) -> str:
    """Пользовательское сообщение: запрос + каталог кандидатов.

    `candidates` — список dict из RAG-поиска по каталогу; ожидаемые
    ключи: `id`, `title`, `year`, `genres`, `country`, `rating`.
    Нумерация `[n]` — человекочитаемая; в ответе модель ссылается на
    `id`, который совпадает с `n` (это один и тот же индекс).
    """
    lines = []
    for i, c in enumerate(candidates, 1):
        bits = [str(c.get("title") or "?")]
        if c.get("year"):
            bits.append(str(c["year"]))
        genres = c.get("genres")
        if genres:
            bits.append("/".join(genres) if isinstance(genres, list)
                        else str(genres))
        if c.get("country"):
            bits.append(str(c["country"]))
        rating = c.get("rating")
        if rating:
            bits.append(f"IMDb {rating}")
        lines.append(f"[{i}] " + " — ".join(bits))
    catalog = "\n".join(lines) if lines else "(каталог пуст)"
    return (
        f"Запрос пользователя: {query}\n\n"
        f"КАТАЛОГ КАНДИДАТОВ ({len(candidates)} шт.):\n{catalog}\n\n"
        f"Верни до {limit} рекомендаций строго в формате JSON."
    )


# ---------------------------------------------------------------------------
# Разбор ответа модели
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def extract_json(text: str) -> Any | None:
    """Вытащить JSON из ответа модели.

    Терпит: чистый JSON, ```json-обёртку, текст вокруг объекта/массива.
    Возвращает распарсенное значение или None. Порядок попыток — от
    самого надёжного к самому эвристичному.
    """
    if not text:
        return None
    s = text.strip()
    # 1. Чистый JSON.
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass
    # 2. Fenced-блок ```json ... ```.
    m = _JSON_BLOCK.search(s)
    if m:
        try:
            return json.loads(m.group(1))
        except (ValueError, TypeError):
            pass
    # 3. Первый сбалансированный объект/массив в тексте. Пробуем оба
    #    открывателя в порядке ИХ ПОЯВЛЕНИЯ в тексте: иначе для
    #    `[{"a": 1}]` сканирование `{...}` нашло бы вложенный объект
    #    вместо массива.
    starts = [(s.find(o), o, c) for o, c in (("{", "}"), ("[", "]"))]
    starts = sorted((t for t in starts if t[0] >= 0), key=lambda t: t[0])
    for start, opener, closer in starts:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except (ValueError, TypeError):
                        break
    return None


def parse_series_answer(text: str) -> dict:
    """Разобрать ответ модели в нормализованный вид.

    Возвращает `{"picks": [{"id","title","why"}], "note": str,
    "parsed": bool, "raw": str}`. Никогда не бросает исключение:
    неразобранный ответ → `parsed=False`, `picks=[]` (метрика
    format-validity считает такие ответы провалом).
    """
    out = {"picks": [], "note": "", "parsed": False, "raw": text or ""}
    data = extract_json(text)
    if data is None:
        return out
    # Одиночный объект-рекомендация вместо {"picks": [...]}.
    if isinstance(data, dict) and "picks" not in data and "id" in data:
        data = {"picks": [data]}
    if not isinstance(data, dict):
        if isinstance(data, list):
            data = {"picks": data}
        else:
            return out
    picks = data.get("picks")
    if not isinstance(picks, list):
        return out
    for p in picks:
        if not isinstance(p, dict):
            continue
        pid = p.get("id")
        if pid is None:
            continue
        out["picks"].append({
            "id": str(pid).strip(),
            "title": str(p.get("title") or "").strip(),
            "why": str(p.get("why") or "").strip(),
        })
    note = data.get("note")
    out["note"] = str(note).strip() if note else ""
    out["parsed"] = True
    return out


# ---------------------------------------------------------------------------
# Реестр профилей
# ---------------------------------------------------------------------------

PROFILES: dict[str, TaskProfile] = {
    # Дефолт: поведение дней 11–28 (num_ctx не отправляется).
    "studio": TaskProfile(
        name="studio",
        title="Студия (по умолчанию)",
        description=("Базовый режим приложения: свободный диалог, "
                     "context window — дефолт Ollama, T=0.7."),
        temperature=0.7,
        top_p=0.9,
        num_predict=2048,
        num_ctx=None,
        system_prompt=STUDIO_SYSTEM_PROMPT,
        notes="Регрессия: повторяет поведение до дня 29.",
    ),
    # Цель дня 29: поиск сериалов, Q4_K_M-модель.
    "series": TaskProfile(
        name="series",
        title="Поиск сериалов (Q4_K_M)",
        description=("Подбор сериалов по каталогу: T=0.2, num_predict=512, "
                     "num_ctx=8192, JSON по схеме, промпт с запретом "
                     "выдумывать."),
        model="qwen3-coder:30b",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=False,
        notes="Промпт уже требует JSON; format_json=False — меньше "
              "ограничений декодера, проверяем послушание промпту.",
    ),
    # Тот же профиль на Q8_0 — для замера квантования.
    "series_q8": TaskProfile(
        name="series_q8",
        title="Поиск сериалов (Q8_0)",
        description=("Тот же профиль, что series, но на Q8_0-кванте: "
                     "замер «сколько качества стоит квантование»."),
        model="gemma3:4b-it-q8_0",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=False,
        notes="Контроль квантования: Q8_0 против Q4_K_M той же модели.",
    ),
    # Пара к series_q8: та же модель в Q4_K_M — чтобы отличие в цифрах
    # объяснялось ТОЛЬКО квантованием (одинаковая модель, один промпт,
    # один num_ctx, один seed), а не сменой семейства моделей.
    "series_q4": TaskProfile(
        name="series_q4",
        title="Поиск сериалов (Q4_K_M, та же модель)",
        description=("Та же модель, что series_q8, но в Q4_K_M: чистый "
                     "замер влияния квантования (A/B одной модели)."),
        model="gemma3:4b",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=False,
        notes="A/B-пара к series_q8: отличается только квант.",
    ),
    # Сравнение семейств моделей на ОДНОМ кванте (Q4_K_M, как лежат в
    # Ollama): показывает, что выбор модели/кванта — это компромисс
    # «качество ↔ память ↔ скорость», а не «больше = лучше».
    "series_gemma": TaskProfile(
        name="series_gemma",
        title="Поиск сериалов (gemma3:27b, Q4_K_M)",
        description=("Тот же профиль на gemma3:27b (Q4_K_M): сравнение "
                     "семейств моделей на одном кванте."),
        model="gemma3:27b",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=False,
        notes="Плотная модель 27B — дороже по KV-кэшу (16 KV-голов).",
    ),
    "series_glm": TaskProfile(
        name="series_glm",
        title="Поиск сериалов (glm-4.7-flash, Q4_K_M)",
        description=("Тот же профиль на glm-4.7-flash (Q4_K_M, MoE): "
                     "третье семейство на том же кванте."),
        model="glm-4.7-flash:q4_K_M",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=False,
        # Без этого флага модель уходит в размышления и возвращает
        # пустой `content` (0 рекомендаций на 20/20 кейсов).
        think=False,
        notes="MoE + reasoning: обязателен think=False, иначе пустой ответ.",
    ),
    # Строгий JSON-режим (constrained decoding) — проверка гипотезы
    # «грамматика снижает долю невалидного JSON».
    "series_json": TaskProfile(
        name="series_json",
        title="Поиск сериалов (строгий JSON)",
        description=("Как series, но с response_format=json_object: "
                     "проверяем, растёт ли format-validity."),
        model="qwen3-coder:30b",
        temperature=0.2,
        top_p=0.9,
        num_predict=512,
        num_ctx=8192,
        seed=29,
        system_prompt=SERIES_SYSTEM_PROMPT,
        format_json=True,
        notes="Гипотеза: JSON-режим повышает format-validity.",
    ),
}

DEFAULT_PROFILE = "studio"


def profile_for(name: str | None) -> TaskProfile:
    """Профиль по имени; None/неизвестное имя → дефолтный `studio`."""
    if not name:
        return PROFILES[DEFAULT_PROFILE]
    return PROFILES.get(name, PROFILES[DEFAULT_PROFILE])


def profile_names() -> list[str]:
    """Имена профилей в порядке объявления."""
    return list(PROFILES)


def with_overrides(profile: TaskProfile, **kw) -> TaskProfile:
    """Копия профиля с переопределёнными полями (для замеров)."""
    return replace(profile, **kw)


def validate_profile_params(temperature=None, top_p=None, num_predict=None,
                            num_ctx=None, seed=None) -> dict:
    """Валидация параметров профиля (RU-ошибки, как в agent.set_config).

    Возвращает dict только с переданными (не None) валидными полями.
    Бросает ValueError с русским текстом — роут превращает в 400.
    """
    out: dict[str, Any] = {}
    if temperature is not None:
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not (0 <= temperature <= 2)):
            raise ValueError("Поле temperature должно быть числом от 0 до 2")
        out["temperature"] = temperature
    if top_p is not None:
        if (isinstance(top_p, bool) or not isinstance(top_p, (int, float))
                or not (0 < top_p <= 1)):
            raise ValueError("Поле top_p должно быть числом больше 0 и не больше 1")
        out["top_p"] = top_p
    if num_predict is not None:
        if (isinstance(num_predict, bool) or not isinstance(num_predict, int)
                or not (MAX_TOKENS_MIN <= num_predict <= MAX_TOKENS_MAX)):
            raise ValueError(f"Поле num_predict должно быть целым числом "
                             f"от {MAX_TOKENS_MIN} до {MAX_TOKENS_MAX}")
        out["num_predict"] = num_predict
    if num_ctx is not None:
        if (isinstance(num_ctx, bool) or not isinstance(num_ctx, int)
                or not (NUM_CTX_MIN <= num_ctx <= NUM_CTX_MAX)):
            raise ValueError(f"Поле num_ctx должно быть целым числом "
                             f"от {NUM_CTX_MIN} до {NUM_CTX_MAX}")
        out["num_ctx"] = num_ctx
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("Поле seed должно быть целым числом")
        out["seed"] = seed
    return out
