"""День 29: сервис «Поиск сериалов» — рекомендации локальной LLM.

Собирает кейс дня 29 в один объект:

* **каталог** — реальные сериалы из Wikidata (`data/series/catalog.json`,
  готовится `scripts/build_series_catalog.py`);
* **отбор кандидатов** — детерминированный, без LLM (по ограничениям
  запроса и совпадению слов), поэтому вход у всех профилей одинаков;
* **промпт** — `tuning.SERIES_SYSTEM_PROMPT` + каталог кандидатов;
* **вызов модели** — профиль из `tuning.py`; `num_ctx` уходит
  НАТИВНЫМ запросом `/api/chat`, потому что OpenAI-совместимый
  `/v1/chat/completions` его игнорирует (проверено на Ollama 0.34.4,
  см. `openspec/changes/day29-local-llm-tuning/design.md`);
* **метрики** — формат, верность каталогу (антигаллюцинация),
  соблюдение ограничений, латентность, токены/с.

Модуль не зависит от FastAPI — тестируется офлайн с подменённым
транспортом (`client` DI, как в `kb.py`).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

from tuning import (PROFILES, build_series_user_prompt, parse_series_answer,
                    profile_for)

# Сколько кандидатов показывать модели и сколько рекомендаций просить.
CANDIDATES_K = 24
TOP_K = 5

# Последний рубеж для модели запроса: профиль `studio` намеренно не
# задаёт модель (в приложении берётся текущая модель диалога), но
# запрос без модели Ollama отвергает (400 «model is required»).
DEFAULT_SERIES_MODEL = "qwen3-coder:30b"

# Путь к каталогу: <repo>/data/series/catalog.json
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CATALOG_PATH = os.path.join(_ROOT, "data", "series", "catalog.json")
DEFAULT_REPORT_PATH = os.path.join(_ROOT, "data", "series",
                                   "optimization_report.json")

# Ограничения, которые сервис умеет вытаскивать из свободного текста.
GENRE_ALIASES = {
    "криминал": ["криминал", "детектив", "полицейск", "гангстер"],
    "комедия": ["комеди", "ситком", "смешн", "юмор"],
    "драма": ["драм"],
    "триллер": ["триллер"],
    "фантастика": ["фантаст", "фантастик", "сай-фай", " sci-fi"],
    "фэнтези": ["фэнтези", "фентези"],
    "ужасы": ["ужас", "хоррор"],
    "мелодрама": ["мелодрам", "романтич", "романтик", "любовн"],
    "история": ["историческ", "история"],
    "боевик": ["боевик", "экшен", "action"],
    "приключения": ["приключен"],
    "детский": ["детск", "подростков", "юношеск"],
    "анимация": ["анимац", "мульт"],
    "документальный": ["документальн"],
    "семейный": ["семейн"],
    "военный": ["военн"],
    "мюзикл": ["мюзикл", "музыкальн"],
    "биография": ["биографич"],
    "спорт": ["спорт"],
    "медицина": ["медицин"],
    "юридический": ["юридическ", "судебн"],
}

# Известные страны каталога (для разбора запроса). Первое значение —
# каноническое имя в каталоге, остальные — как страну называют в
# запросе («российский» вместо «Россия»).
COUNTRIES: list[tuple[str, tuple[str, ...]]] = [
    ("США", ("сша", "американск", "америк")),
    ("Великобритания", ("великобритани", "британск", "английск", " англи")),
    ("Россия", ("росси", "русск")),
    ("Германия", ("германи", "немецк")),
    ("Франция", ("франци", "французск")),
    ("Турция", ("турци", "турецк")),
    ("Индия", ("инди", "индийск")),
    ("Япония", ("япони", "японск")),
    ("Республика Корея", ("коре", "корейск")),
    ("Испания", ("испани", "испанск")),
    ("Италия", ("итали", "итальянск")),
    ("Канада", ("канад",)),
    ("Австралия", ("австрали",)),
    ("Польша", ("польш", "польск")),
    ("Чехия", ("чехи", "чешск")),
    ("Швеция", ("швеци", "шведск")),
    ("Дания", ("дани", "датск")),
    ("Норвегия", ("норвеги", "норвежск")),
    ("Нидерланды", ("нидерланд", "голландск")),
    ("Бразилия", ("бразили", "бразильск")),
    ("Мексика", ("мексик",)),
    ("Китай", ("кита", "китайск")),
    ("Украина", ("украин",)),
]


class SeriesError(Exception):
    """Ошибка сервиса поиска сериалов (RU-текст для 4xx/502)."""


# ---------------------------------------------------------------------------
# Каталог
# ---------------------------------------------------------------------------

def load_catalog(path: str | None = None) -> list[dict]:
    """Прочитать каталог сериалов (пустой список, если файла нет)."""
    p = path or DEFAULT_CATALOG_PATH
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def catalog_stats(catalog: list[dict]) -> dict:
    """Сводка по каталогу (для UI/роута)."""
    years = [r["year"] for r in catalog if r.get("year")]
    genres: dict[str, int] = {}
    countries: dict[str, int] = {}
    for r in catalog:
        for g in r.get("genres") or []:
            genres[g] = genres.get(g, 0) + 1
        c = r.get("country")
        if c:
            countries[c] = countries.get(c, 0) + 1
    return {
        "total": len(catalog),
        "years": [min(years), max(years)] if years else None,
        "genres": dict(sorted(genres.items(), key=lambda kv: -kv[1])),
        "countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])),
    }


# ---------------------------------------------------------------------------
# Разбор запроса → ограничения
# ---------------------------------------------------------------------------

def parse_query(query: str) -> dict:
    """Вытащить ограничения из свободного текста запроса.

    Возвращает `{"genres": [...], "country": str|None, "min_year": int|None,
    "max_year": int|None}`. Разбор намеренно простой и предсказуемый: он
    нужен для детерминированного отбора кандидатов, а не для понимания
    смысла (понимание — задача LLM).
    """
    q = (query or "").lower()
    genres = [g for g, alias in GENRE_ALIASES.items()
              if any(a in q for a in alias)]
    country = None
    for canon, aliases in COUNTRIES:
        if any(a in q for a in aliases):
            country = canon
            break

    min_year = max_year = None
    # «после 2015», «не старше 2019», «2019 года или новее», «с 2020»
    m = re.search(r"(?:после|позже|новее|свежее|начиная с|с)\s+(\d{4})", q)
    if m:
        min_year = int(m.group(1)) + (1 if "после" in q[:m.start() + 6] else 0)
    m = re.search(r"не\s+старше\s+(\d{4})", q)
    if m:
        max_year = int(m.group(1))
    m = re.search(r"(\d{4})\s*(?:года?)?\s*(?:или\s+)?(?:новее|позже|свежее|\+)", q)
    if m:
        min_year = int(m.group(1))
    m = re.search(r"(?:ровно\s+)?(\d{4})\s*год", q)
    if m and min_year is None and max_year is None:
        min_year = max_year = int(m.group(1))
    m = re.search(r"за\s+(\d{4})", q)
    if m and min_year is None:
        min_year = max_year = int(m.group(1))
    return {"genres": genres, "country": country,
            "min_year": min_year, "max_year": max_year}


def _matches(rec: dict, constraints: dict) -> bool:
    """Запись удовлетворяет ограничениям запроса."""
    genres = constraints.get("genres") or []
    if genres:
        rec_genres = set(rec.get("genres") or [])
        if not any(g in rec_genres for g in genres):
            return False
    c = constraints.get("country")
    if c:
        countries = rec.get("countries") or ([rec["country"]] if rec.get("country") else [])
        if c not in countries:
            return False
    y = rec.get("year")
    if constraints.get("min_year") and (y is None or y < constraints["min_year"]):
        return False
    if constraints.get("max_year") and (y is None or y > constraints["max_year"]):
        return False
    return True


def select_candidates(catalog: list[dict], query: str,
                      k: int = CANDIDATES_K) -> list[dict]:
    """Кандидаты для промпта: подходящие первыми, затем «похожие».

    Кандидатов намеренно БОЛЬШЕ, чем подходящих: среди них есть
    «ловушки» (не подходящие по ограничениям), иначе задача выбора
    тривиальна и метрика соблюдения ограничений ничего не измеряет.
    """
    constraints = parse_query(query)
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 3]

    def score(rec: dict) -> tuple:
        ok = _matches(rec, constraints)
        genre_hits = sum(1 for g in (rec.get("genres") or [])
                         if any(w in g for w in words))
        title_hit = 1 if any(w in (rec.get("title") or "").lower()
                             for w in words) else 0
        return (1 if ok else 0, genre_hits, title_hit, rec.get("year") or 0)

    return sorted(catalog, key=score, reverse=True)[:k]


# ---------------------------------------------------------------------------
# Вызов модели
# ---------------------------------------------------------------------------

class SeriesFinder:
    """Поиск сериалов: каталог + профиль + вызов локальной LLM.

    `client` — httpx.Client (DI для тестов). `ollama_url` — БАЗА БЕЗ
    `/v1` (нативный API): из `OLLAMA_BASE_URL` убираем хвост `/v1`.
    """

    def __init__(self, catalog: list[dict] | None = None,
                 ollama_base_url: str | None = None,
                 api_key: str | None = None,
                 client: "httpx.Client | None" = None,
                 catalog_path: str | None = None,
                 default_model: str | None = None):
        self.catalog = catalog if catalog is not None else load_catalog(catalog_path)
        raw = (ollama_base_url or os.environ.get("OLLAMA_BASE_URL")
               or "http://localhost:11434/v1").rstrip("/")
        # Нативный API живёт на корне (без /v1).
        self.ollama_url = re.sub(r"/v1$", "", raw)
        self.api_key = api_key if api_key is not None else os.environ.get(
            "OLLAMA_API_KEY", "ollama")
        # Модель для профилей, которые её не задают (`studio` намеренно
        # берёт модель из общего конфига Студии). Без явной модели Ollama
        # отвечает 400 «model is required».
        self.default_model = default_model
        self._client = client

    def _resolve_model(self, profile, model: str | None) -> str:
        """Модель запроса: явная → модель профиля → модель конфига.

        Порядок важен: профиль может не задавать модель (studio), тогда
        берётся текущая модель приложения. Если её тоже нет — последний
        рубеж, чтобы не отправить запрос без модели (Ollama вернёт 400).
        """
        if model:
            return model
        if profile.model:
            return profile.model
        if self.default_model:
            return self.default_model
        return DEFAULT_SERIES_MODEL

    def _http(self):
        if self._client is not None:
            return self._client
        if httpx is None:  # pragma: no cover
            raise SeriesError("httpx не установлен")
        return httpx.Client(timeout=300)

    def recommend(self, query: str, profile_name: str = "series",
                  limit: int = TOP_K, model: str | None = None) -> dict:
        """Рекомендации по запросу активным профилем.

        Возвращает dict: `query`, `profile`, `model`, `constraints`,
        `recommendations` (с подставленными из каталога полями),
        `picks_raw`, `note`, `parsed`, `metrics`, `request_body`,
        `candidates`.
        """
        if not query or not query.strip():
            raise SeriesError("Запрос не может быть пустым")
        if not self.catalog:
            raise SeriesError("Каталог сериалов пуст: соберите его скриптом "
                              "scripts/build_series_catalog.py")
        profile = profile_for(profile_name)
        cands = select_candidates(self.catalog, query)
        # id в промпте = порядковый номер (1..K) и совпадает с id в ответе.
        prompt_cands = []
        id_map: dict[str, dict] = {}
        for i, rec in enumerate(cands, 1):
            cc = dict(rec)
            cc["id"] = str(i)
            prompt_cands.append(cc)
            id_map[str(i)] = rec

        user = build_series_user_prompt(query, prompt_cands, limit=limit)
        t0 = time.time()
        text, usage, err = self._call(profile, user, model=model)
        sec = time.time() - t0
        if err:
            raise SeriesError(err)

        parsed = parse_series_answer(text)
        recs, hallucinated = [], []
        for p in parsed["picks"]:
            rec = id_map.get(p["id"])
            if rec is None:
                hallucinated.append(p)
                continue
            recs.append({
                "id": p["id"],
                "imdb": rec.get("imdb"),
                "title": rec.get("title"),
                "year": rec.get("year"),
                "genres": rec.get("genres"),
                "country": rec.get("country"),
                "why": p.get("why") or "",
                "satisfies": _matches(rec, parse_query(query)),
            })

        constraints = parse_query(query)
        comp = (usage or {}).get("completion_tokens") or 0
        metrics = {
            "latency_sec": round(sec, 2),
            "prompt_tokens": (usage or {}).get("prompt_tokens") or 0,
            "completion_tokens": comp,
            "tokens_per_sec": round(comp / sec, 1) if comp and sec > 0 else None,
            "num_ctx": profile.num_ctx,
            "parsed": parsed["parsed"],
            "hallucination_rate": (round(len(hallucinated) / len(parsed["picks"]), 4)
                                   if parsed["picks"] else 0.0),
            "constraint_satisfaction": (
                round(sum(1 for r in recs if r["satisfies"]) / len(recs), 4)
                if recs else 0.0),
        }
        return {
            "query": query,
            "profile": profile.name,
            "model": self._resolve_model(profile, model),
            "constraints": constraints,
            "recommendations": recs,
            "hallucinated": hallucinated,
            "note": parsed["note"],
            "parsed": parsed["parsed"],
            "metrics": metrics,
            "candidates": prompt_cands,
            "request_body": self._request_preview(profile, user, model),
        }

    def _request_preview(self, profile, user: str, model: str | None) -> dict:
        """Тело запроса без system-промпта (для панели «Запрос» в UI)."""
        body = profile.sampling_body(model=self._resolve_model(profile, model))
        body["messages"] = [
            {"role": "system", "content": profile.system_prompt[:400] + "…"},
            {"role": "user", "content": user[:1200]},
        ]
        body["stream"] = False
        return body

    def _call(self, profile, user: str, model: str | None = None,
              timeout: int = 300) -> tuple[str, dict, str | None]:
        """Вызов модели. Возвращает (текст, usage, error).

        Если профиль задаёт `num_ctx`, идём НАТИВНЫМ `/api/chat` (только
        он понимает `options.num_ctx`); иначе — привычным
        OpenAI-совместимым `/chat/completions`.
        """
        m = self._resolve_model(profile, model)
        client = self._http()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if profile.num_ctx is not None:
            url = self.ollama_url + "/api/chat"
            options: dict[str, Any] = {
                "temperature": profile.temperature,
                "top_p": profile.top_p,
                "num_predict": profile.num_predict,
                "num_ctx": profile.num_ctx,
            }
            if profile.seed is not None:
                options["seed"] = profile.seed
            if profile.stop:
                options["stop"] = list(profile.stop)
            body: dict[str, Any] = {
                "model": m,
                "messages": [{"role": "system", "content": profile.system_prompt},
                             {"role": "user", "content": user}],
                "stream": False,
                "options": options,
            }
            # Reasoning-модели пишут цепочку рассуждений в отдельное поле
            # `thinking`, и при исчерпанном num_predict `content` остаётся
            # пустым (замер: glm-4.7-flash давал 0 рекомендаций на 20 из
            # 20 кейсов). think=False возвращает обычный ответ.
            if profile.think is not None:
                body["think"] = profile.think
            if profile.format_json:
                body["format"] = "json"
            try:
                resp = client.post(url, json=body, headers=headers, timeout=timeout)
            except Exception as e:  # noqa: BLE001
                return "", {}, f"Не удалось обратиться к Ollama: {e}"[:300]
            if resp.status_code != 200:
                return "", {}, (f"Ollama вернула HTTP {resp.status_code}: "
                                f"{resp.text[:200]}")
            j = resp.json()
            text = ((j.get("message") or {}).get("content") or "").strip()
            usage = {
                "prompt_tokens": j.get("prompt_eval_count") or 0,
                "completion_tokens": j.get("eval_count") or 0,
                "total_tokens": (j.get("prompt_eval_count") or 0)
                                + (j.get("eval_count") or 0),
            }
            if not text:
                return "", usage, "Модель вернула пустой ответ"
            return text, usage, None

        # Путь дней 11–28: OpenAI-совместимый API.
        url = self.ollama_url + "/v1/chat/completions"
        body = profile.sampling_body(model=m)
        body["messages"] = [{"role": "system", "content": profile.system_prompt},
                            {"role": "user", "content": user}]
        body["stream"] = False
        try:
            resp = client.post(url, json=body, headers=headers, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return "", {}, f"Не удалось обратиться к Ollama: {e}"[:300]
        if resp.status_code != 200:
            return "", {}, f"Ollama вернула HTTP {resp.status_code}: {resp.text[:200]}"
        j = resp.json()
        choice = (j.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            return "", j.get("usage") or {}, "Модель вернула пустой ответ"
        return text, j.get("usage") or {}, None

    def compare(self, queries: list[str], profiles: list[str],
                limit: int = TOP_K) -> dict:
        """Прогнать набор запросов по нескольким профилям и сравнить.

        Возвращает `{"profiles": {name: {...метрики...}}, "rows": [...]}`.
        Один упавший запрос не ломает сравнение: в строке остаётся error.
        """
        rows = []
        agg: dict[str, list[dict]] = {p: [] for p in profiles}
        for q in queries:
            for p in profiles:
                try:
                    res = self.recommend(q, profile_name=p, limit=limit)
                    row = {"query": q, "profile": p, "ok": True,
                           **res["metrics"]}
                    agg[p].append(res["metrics"])
                except SeriesError as e:
                    row = {"query": q, "profile": p, "ok": False,
                           "error": str(e)}
                rows.append(row)

        summary = {}
        for p, ms in agg.items():
            if not ms:
                summary[p] = {"ok_cases": 0}
                continue
            n = len(ms)
            lat = sorted(m["latency_sec"] for m in ms)
            tps = [m["tokens_per_sec"] for m in ms if m["tokens_per_sec"]]
            summary[p] = {
                "ok_cases": n,
                "format_validity": round(sum(1 for m in ms if m["parsed"]) / n, 4),
                "hallucination_rate": round(
                    sum(m["hallucination_rate"] for m in ms) / n, 4),
                "constraint_satisfaction": round(
                    sum(m["constraint_satisfaction"] for m in ms) / n, 4),
                "latency_p50": lat[len(lat) // 2],
                "tokens_per_sec": round(sum(tps) / len(tps), 1) if tps else None,
                "num_ctx": ms[0].get("num_ctx"),
            }
        return {"profiles": summary, "rows": rows}


def load_report(path: str | None = None) -> dict:
    """Последний отчёт оптимизации (пустой dict, если файла нет)."""
    p = path or DEFAULT_REPORT_PATH
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}
