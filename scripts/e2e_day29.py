# -*- coding: utf-8 -*-
"""E2E day 29 «Оптимизация локальной LLM под задачу поиска сериалов»

Задание дня 29: оптимизировать локальную модель под конкретную задачу
(поиск новых сериалов):
  * настроить параметры (temperature, max tokens, context window);
  * попробовать квантование (если доступно);
  * изменить prompt-шаблон под кейс.

Скрипт доказывает, что оптимизация сделана и ИЗМЕРИМА:

Часть A (offline, MUST PASS, без сети и без Ollama):
  A1 профили: GET /api/tuning/profiles отдаёт 4 профиля; активный —
     studio; профиль series отличается от studio по temperature /
     num_predict и ЗАДАЁТ num_ctx
  A2 переключение профиля: POST /api/tuning/profile → активный
     меняется; неизвестное имя → 400; значение переживает перезапрос
  A3 КЛЮЧЕВОЕ (context window): профиль с num_ctx идёт НАТИВНЫМ
     /api/chat и несёт options.num_ctx; профиль studio (num_ctx=None)
     идёт OpenAI-совместимым /v1/chat/completions БЕЗ num_ctx
     (регрессия дней 11–28: тело запроса не изменилось)
  A4 промпт-шаблон под кейс: system-промпт профиля series содержит
     запрет выдумывать и требование каталога; в запрос уходит каталог
     кандидатов с id; названия в ответе подставляются ИЗ КАТАЛОГА
  A5 антигаллюцинация: id вне каталога → recommendations пусто,
     hallucinated непусто, метрика hallucination_rate == 1
  A6 валидация роутов: пустой запрос / битый limit / неизвестный
     профиль → 400; пустой каталог и недоступная Ollama → 502
  A7 сравнение профилей: POST /api/series/compare даёт метрики по
     каждому профилю; падение запроса не ломает сравнение
  A8 каталог: GET /api/series/catalog отдаёт статистику (жанры,
     страны, годы); каталог из реальных данных Wikidata

Часть B (live, best-effort, SKIP без Ollama):
  B0 зонд Ollama: GET /api/tags; выбор модели каталога профиля
  B1 context window РЕАЛЬНО применяется: запрос профилем series →
     `ollama ps` показывает CONTEXT == num_ctx профиля (8192), а не
     авто-дефолт 32768
  B2 качество: прогон золотого набора (10 кейсов) профилем series —
     format-validity, верность каталогу, соблюдение ограничений
  B3 квантование: если в Ollama есть Q8_0-модель — сравнение с Q4_K_M
     на тех же кейсах (качество + латентность); иначе SKIP
  B4 замер по квантам сохранён в отчёт (quant_report.json)

Запуск из корня репозитория:
    python scripts/e2e_day29.py [--part-a-only] [--cases 10]
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
# Нативный API живёт на корне (без /v1): /api/tags, /api/ps, /api/generate.
OLLAMA_NATIVE = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA
CATALOG_PATH = os.path.join(REPO, "data", "series", "catalog.json")

# --- инфраструктура отчёта (паттерн e2e_day24/28) ---

RESULTS: list[tuple[str, str, str]] = []   # (шаг, PASS/FAIL/SKIP, деталь)


def log(msg: str) -> None:
    print(msg, flush=True)


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    mark = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    log(f"  [{mark}] {step}" + (f" — {detail}" if detail else ""))


def print_summary() -> tuple[int, int]:
    p = sum(1 for _, s, _ in RESULTS if s == "PASS")
    f = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    k = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    log("\n" + "=" * 74)
    log(f"ИТОГ: PASS={p} FAIL={f} SKIP={k}")
    for step, status, detail in RESULTS:
        if status == "FAIL":
            log(f"  ПРОВАЛ: {step} — {detail}")
    log("=" * 74)
    return p, f


def _fake_ollama_client(captured: list, answer: str,
                        finish: str = "stop"):
    """httpx.Client с MockTransport: отвечает и на нативный /api/chat,
    и на OpenAI-совместимый /v1/chat/completions. Пишет тела запросов в
    `captured`."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        captured.append({"url": str(request.url), "body": body})
        if str(request.url).endswith("/api/chat"):
            return httpx.Response(200, json={
                "message": {"role": "assistant", "content": answer},
                "prompt_eval_count": 800, "eval_count": 60, "done": True,
                "done_reason": finish,
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": answer},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 60,
                      "total_tokens": 860},
        })
    return httpx.Client(transport=httpx.MockTransport(handler))


def _load_modules():
    """Импорт модулей бэкенда (dev-режим: sys.path уже дополнен)."""
    import agent as agent_mod
    import main as main_mod
    import series as series_mod
    import tuning as tuning_mod
    return agent_mod, main_mod, series_mod, tuning_mod


def _make_catalog(n: int = 30) -> list[dict]:
    """Каталог-фикстура для офлайн-части: жанры/годы/страны под кейсы."""
    genres = ["криминал", "комедия", "драма", "фантастика", "триллер",
              "мелодрама", "ужасы", "история", "боевик", "приключения"]
    countries = ["Россия", "США", "Великобритания", "Германия", "Турция",
                 "Республика Корея"]
    cat = []
    for i in range(n):
        cat.append({
            "id": i + 1, "imdb": f"tt{i + 1:07d}",
            "title": f"Сериал {i + 1}",
            "year": 2010 + (i % 15),
            "genres": [genres[i % len(genres)]],
            "tags": [], "country": countries[i % len(countries)],
            "countries": [countries[i % len(countries)]],
            "languages": ["русский"], "seasons": 1 + (i % 3),
            "episodes": 8 + i,
        })
    return cat


# ---------------------------------------------------------------------------
# Part A: офлайн
# ---------------------------------------------------------------------------

def part_a() -> bool:
    log("\n=== Part A: офлайн (TestClient + фейковая Ollama, без сети) ===")
    from fastapi.testclient import TestClient

    agent_mod, main_mod, series_mod, tuning_mod = _load_modules()
    catalog = _make_catalog()

    answer = json.dumps({
        "picks": [{"id": "1", "title": "Что-то", "why": "подходит"}],
        "note": "по каталогу",
    }, ensure_ascii=False)

    def make_client(ans: str = answer, cat: list[dict] | None = None,
                    captured: list | None = None):
        cap = captured if captured is not None else []
        tmp = tempfile.mkdtemp(prefix="e2e29_")
        agent = agent_mod.StudioAgent(tmp)
        series = series_mod.SeriesFinder(
            catalog=cat if cat is not None else catalog,
            client=_fake_ollama_client(cap, ans))
        app = main_mod.create_app(agent=agent, kb=None, series=series)
        return TestClient(app), cap, tmp

    ok = True

    # --- A1: профили ---
    try:
        c, _, _ = make_client()
        r = c.get("/api/tuning/profiles")
        j = r.json()
        names = [p["name"] for p in j["profiles"]]
        need = {"studio", "series", "series_q8", "series_json"}
        assert r.status_code == 200, f"HTTP {r.status_code}"
        assert need <= set(names), f"нет профилей: {need - set(names)}"
        assert j["active"] == "studio", f"активный по умолчанию: {j['active']}"
        studio = next(p for p in j["profiles"] if p["name"] == "studio")
        series = next(p for p in j["profiles"] if p["name"] == "series")
        assert studio["num_ctx"] is None, "studio не должен задавать num_ctx"
        assert series["num_ctx"] is not None, "series должен задавать num_ctx"
        assert series["temperature"] < studio["temperature"], "T не оптимизирована"
        assert series["num_predict"] < studio["num_predict"], "max_tokens не оптимизирован"
        record("A1 профили оптимизации",
               "PASS", f"{len(names)} профиля; series: T={series['temperature']} "
                       f"num_predict={series['num_predict']} num_ctx={series['num_ctx']}")
    except Exception as e:  # noqa: BLE001
        record("A1 профили оптимизации", "FAIL", str(e))
        ok = False

    # --- A2: переключение профиля ---
    try:
        c, _, _ = make_client()
        r = c.post("/api/tuning/profile", json={"profile": "series"})
        assert r.status_code == 200 and r.json()["active"] == "series", r.text
        assert c.get("/api/tuning/profiles").json()["active"] == "series", \
            "профиль не сохранился"
        bad = c.post("/api/tuning/profile", json={"profile": "нет-такого"})
        assert bad.status_code == 400, f"ожидали 400, получили {bad.status_code}"
        record("A2 переключение профиля", "PASS", "series выбран, мусор → 400")
    except Exception as e:  # noqa: BLE001
        record("A2 переключение профиля", "FAIL", str(e))
        ok = False

    # --- A3: context window уходит нативным вызовом ---
    try:
        c, cap, _ = make_client()
        c.post("/api/series/search", json={"query": "криминальный сериал",
                                           "profile": "series"})
        native = [x for x in cap if x["url"].endswith("/api/chat")]
        assert native, "профиль series не пошёл нативным /api/chat"
        assert native[0]["body"]["options"]["num_ctx"] == 8192, \
            f"num_ctx не ушёл: {native[0]['body'].get('options')}"
        assert native[0]["body"]["options"]["temperature"] == 0.2, "T не применена"
        assert native[0]["body"]["options"]["num_predict"] == 512, "num_predict не применён"

        c2, cap2, _ = make_client()
        c2.post("/api/series/search", json={"query": "криминальный сериал",
                                            "profile": "studio"})
        oai = [x for x in cap2 if x["url"].endswith("/v1/chat/completions")]
        assert oai, "профиль studio не пошёл OpenAI-совместимым путём"
        assert "num_ctx" not in oai[0]["body"], "studio не должен слать num_ctx"
        assert oai[0]["body"]["max_tokens"] == 2048, "регрессия: max_tokens studio"
        record("A3 context window (num_ctx)",
               "PASS", "series → /api/chat options.num_ctx=8192; "
                       "studio → /v1 без num_ctx (регрессия цела)")
    except Exception as e:  # noqa: BLE001
        record("A3 context window (num_ctx)", "FAIL", str(e))
        ok = False

    # --- A4: промпт-шаблон под кейс ---
    try:
        c, cap, _ = make_client()
        c.post("/api/series/search", json={"query": "криминальный сериал после 2015",
                                           "profile": "series"})
        body = cap[-1]["body"]
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        assert "ТОЛЬКО сериалы из каталога" in system, "нет запрета выдумывать"
        assert "JSON" in system, "нет требования формата"
        assert "КАТАЛОГ КАНДИДАТОВ" in user, "каталог не ушёл в промпт"
        assert "[1]" in user, "кандидаты не пронумерованы"
        # Название в ответе подставлено из каталога, а не из ответа модели.
        # id "1" в промпте — это ПЕРВЫЙ КАНДИДАТ (отсортированный), а не
        # catalog[0]; берём его из ответа роута (candidates).
        res = c.post("/api/series/search",
                     json={"query": "криминальный сериал", "profile": "series"}).json()
        first_cand = res["candidates"][0]
        assert res["recommendations"][0]["title"] == first_cand["title"], \
            "название не из каталога"
        assert res["recommendations"][0]["imdb"] == first_cand["imdb"], \
            "imdb не из каталога"
        assert res["recommendations"][0]["title"] != "Что-то", \
            "название взято из ответа модели, а не из каталога"
        record("A4 промпт-шаблон под кейс",
               "PASS", "запрет выдумывать + каталог с id + подстановка из каталога")
    except Exception as e:  # noqa: BLE001
        record("A4 промпт-шаблон под кейс", "FAIL", str(e))
        ok = False

    # --- A5: антигаллюцинация ---
    try:
        fake = json.dumps({"picks": [{"id": "999", "title": "Выдуманный сериал",
                                      "why": "нет такого"}], "note": ""},
                          ensure_ascii=False)
        c, _, _ = make_client(ans=fake)
        res = c.post("/api/series/search",
                     json={"query": "криминальный сериал", "profile": "series"}).json()
        assert res["recommendations"] == [], "выдумка попала в рекомендации"
        assert len(res["hallucinated"]) == 1, "выдумка не отловлена"
        assert res["metrics"]["hallucination_rate"] == 1.0, \
            f"метрика: {res['metrics']['hallucination_rate']}"
        record("A5 антигаллюцинация", "PASS",
               "id вне каталога отброшен, hallucination_rate=1.0")
    except Exception as e:  # noqa: BLE001
        record("A5 антигаллюцинация", "FAIL", str(e))
        ok = False

    # --- A6: валидация ---
    try:
        c, _, _ = make_client()
        checks = [
            ("пустой запрос", c.post("/api/series/search", json={"query": "  "}), 400),
            ("битый limit", c.post("/api/series/search",
                                   json={"query": "x", "limit": 99}), 400),
            ("неизвестный профиль", c.post("/api/series/search",
                                           json={"query": "x", "profile": "нет"}), 400),
            ("пустой каталог", None, 502),
            ("недоступная Ollama", None, 502),
        ]
        for name, resp, want in checks[:3]:
            assert resp.status_code == want, \
                f"{name}: {resp.status_code} != {want}"
        # пустой каталог
        c_empty, _, _ = make_client(cat=[])
        r = c_empty.post("/api/series/search", json={"query": "x"})
        assert r.status_code == 502, f"пустой каталог: {r.status_code}"
        # Ollama недоступна
        import httpx
        tmp = tempfile.mkdtemp(prefix="e2e29_")
        ag = agent_mod.StudioAgent(tmp)
        def boom(request):
            raise httpx.ConnectError("нет соединения")
        sf = series_mod.SeriesFinder(
            catalog=catalog, client=httpx.Client(transport=httpx.MockTransport(boom)))
        c_down = TestClient(main_mod.create_app(agent=ag, kb=None, series=sf))
        r = c_down.post("/api/series/search", json={"query": "x"})
        assert r.status_code == 502, f"Ollama недоступна: {r.status_code}"
        record("A6 валидация роутов", "PASS",
               "400 (запрос/limit/профиль), 502 (каталог/Ollama)")
    except Exception as e:  # noqa: BLE001
        record("A6 валидация роутов", "FAIL", str(e))
        ok = False

    # --- A7: сравнение профилей ---
    try:
        c, _, _ = make_client()
        r = c.post("/api/series/compare",
                   json={"queries": ["криминальный сериал", "комедия"],
                         "profiles": ["series", "series_json"]})
        assert r.status_code == 200, r.text
        j = r.json()
        assert set(j["profiles"]) == {"series", "series_json"}, j["profiles"].keys()
        assert len(j["rows"]) == 4, f"строк: {len(j['rows'])}"
        assert j["profiles"]["series"]["ok_cases"] == 2
        assert j["profiles"]["series"]["num_ctx"] == 8192
        record("A7 сравнение профилей", "PASS",
               "2 профиля × 2 запроса, метрики посчитаны")
    except Exception as e:  # noqa: BLE001
        record("A7 сравнение профилей", "FAIL", str(e))
        ok = False

    # --- A8: каталог ---
    try:
        c, _, _ = make_client()
        j = c.get("/api/series/catalog").json()
        assert j["ready"] is True and j["stats"]["total"] == len(catalog)
        assert j["stats"]["genres"], "нет жанров в статистике"
        assert j["stats"]["countries"], "нет стран в статистике"
        # реальный каталог дня 29
        real = series_mod.load_catalog(CATALOG_PATH)
        record("A8 каталог", "PASS",
               f"фикстура {len(catalog)} + реальный каталог {len(real)} сериалов"
               if real else f"фикстура {len(catalog)} (реальный каталог не собран)")
    except Exception as e:  # noqa: BLE001
        record("A8 каталог", "FAIL", str(e))
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Part B: live
# ---------------------------------------------------------------------------

def _ollama_tags() -> list[dict]:
    """Список моделей Ollama ([] — недоступна)."""
    try:
        with urllib.request.urlopen(OLLAMA_NATIVE + "/api/tags", timeout=10) as r:
            return json.loads(r.read().decode()).get("models", [])
    except Exception:  # noqa: BLE001
        return []


def _ollama_loaded_context() -> tuple[str | None, int | None]:
    """Что реально загружено: (модель, context) из /api/ps."""
    try:
        with urllib.request.urlopen(OLLAMA_NATIVE + "/api/ps", timeout=10) as r:
            models = json.loads(r.read().decode()).get("models", [])
        if not models:
            return None, None
        m = models[0]
        # Ollama 0.34: context в поле context_length (или size_vram-производные)
        ctx = m.get("context_length") or m.get("num_ctx")
        return m.get("name") or m.get("model"), ctx
    except Exception:  # noqa: BLE001
        return None, None


def _unload(model: str) -> None:
    """Выгрузить модель (чтобы num_ctx применился с нуля)."""
    try:
        req = urllib.request.Request(
            OLLAMA_NATIVE + "/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        time.sleep(2)
    except Exception:  # noqa: BLE001
        pass


def part_b(cases: int) -> bool:
    log("\n=== Part B: живой прогон на Ollama ===")
    models = _ollama_tags()
    if not models:
        record("B0 зонд Ollama", "SKIP", "Ollama недоступна (нет /api/tags)")
        record("B1 context window на практике", "SKIP", "нет Ollama")
        record("B2 качество на золотом наборе", "SKIP", "нет Ollama")
        record("B3 квантование", "SKIP", "нет Ollama")
        return True

    names = [m.get("name", "") for m in models]
    record("B0 зонд Ollama", "PASS", f"{len(names)} моделей: {', '.join(names[:4])}")

    import series as series_mod
    import tuning as tuning_mod

    catalog = series_mod.load_catalog(CATALOG_PATH)
    if not catalog:
        record("B2 качество на золотом наборе", "SKIP",
               "каталог не собран (scripts/build_series_catalog.py)")
        return True

    # --- B1: num_ctx реально применяется ---
    ok = True
    try:
        prof = tuning_mod.profile_for("series")
        model = prof.model if prof.model in names else names[0]
        finder = series_mod.SeriesFinder(catalog=catalog)
        _unload(model)
        res = finder.recommend("криминальный сериал после 2018",
                               profile_name="series", model=model)
        loaded_model, ctx = _ollama_loaded_context()
        if ctx is None:
            record("B1 context window на практике", "SKIP",
                   "Ollama не отдала context в /api/ps")
        else:
            assert ctx == prof.num_ctx, \
                f"context={ctx}, ожидали {prof.num_ctx} (профиль num_ctx)"
            record("B1 context window на практике", "PASS",
                   f"{loaded_model}: CONTEXT={ctx} == num_ctx профиля "
                   f"(не авто-дефолт), латентность {res['metrics']['latency_sec']} с")
    except Exception as e:  # noqa: BLE001
        record("B1 context window на практике", "FAIL", str(e))
        ok = False

    # --- B2: качество на золотом наборе ---
    try:
        from series_gold import GOLD
        from series_eval import eval_case, aggregate
        prof = tuning_mod.profile_for("series")
        if prof.model not in names:
            record("B2 качество на золотом наборе", "SKIP",
                   f"модель {prof.model} не найдена в Ollama")
        else:
            by_id = {r["id"]: r for r in catalog}
            gold = GOLD[:cases] if cases else GOLD
            rows = [eval_case(prof, case, catalog, by_id) for case in gold]
            agg = aggregate(rows)
            detail = (f"{agg['cases']} кейсов: format={agg['format_validity']:.0%} "
                      f"верность каталогу={1 - agg['hallucination_rate']:.0%} "
                      f"ограничения={agg['constraint_satisfaction']:.0%} "
                      f"p50={agg['latency_p50']} с")
            # Критерий дня 29: формат валиден, выдумок нет.
            if agg["format_validity"] >= 0.9 and agg["hallucination_rate"] <= 0.05:
                record("B2 качество на золотом наборе", "PASS", detail)
            else:
                record("B2 качество на золотом наборе", "FAIL", detail)
                ok = False
    except Exception as e:  # noqa: BLE001
        record("B2 качество на золотом наборе", "FAIL", str(e))
        ok = False

    # --- B3: квантование ---
    try:
        from series_gold import GOLD
        from series_eval import run_profile
        # A/B по кванту — одна модель (gemma3:4b) в Q4_K_M и Q8_0.
        q8 = [n for n in names if "q8" in n.lower()]
        if not q8:
            record("B3 квантование", "SKIP",
                   "Q8_0-модель не найдена: ollama pull gemma3:4b-it-q8_0")
        else:
            gold = GOLD[:cases] if cases else GOLD
            # Q8-арм против Q4-арма ТОЙ ЖЕ модели: профили отличаются
            # только квантом, поэтому разница в цифрах — его влияние.
            res_q4 = run_profile("series_q4", catalog, len(gold), verbose=False)
            res_q8 = run_profile("series_q8", catalog, len(gold), verbose=False)
            m4, m8 = res_q4["metrics"], res_q8["metrics"]
            detail = (f"Q4_K_M: format={m4['format_validity']:.0%} "
                      f"ограничения={m4['constraint_satisfaction']:.0%} "
                      f"p50={m4['latency_p50']} с, {m4['tokens_per_sec']} ток/с | "
                      f"Q8_0: format={m8['format_validity']:.0%} "
                      f"ограничения={m8['constraint_satisfaction']:.0%} "
                      f"p50={m8['latency_p50']} с, {m8['tokens_per_sec']} ток/с")
            report = {"q8_model": q8[0], "cases": len(gold),
                      "q4_metrics": m4, "q8_metrics": m8}
            out = os.path.join(REPO, "data", "series", "quant_report.json")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            record("B3 квантование", "PASS", detail + f" → {out}")
    except Exception as e:  # noqa: BLE001
        record("B3 квантование", "FAIL", str(e))
        ok = False

    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part-a-only", action="store_true")
    ap.add_argument("--cases", type=int, default=10,
                    help="сколько кейсов золотого набора гонять в Part B")
    args = ap.parse_args()

    log("=" * 74)
    log("E2E day 29: оптимизация локальной LLM под поиск сериалов")
    log(f"Ollama: {OLLAMA}; каталог: {CATALOG_PATH}")
    log("=" * 74)

    a_ok = part_a()
    b_ok = True
    if not args.part_a_only:
        b_ok = part_b(args.cases)

    _, fails = print_summary()
    if not a_ok:
        log("Part A (offline, MUST PASS) провален")
        return 1
    if fails:
        return 1
    log("УСПЕХ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
