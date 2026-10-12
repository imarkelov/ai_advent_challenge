#!/usr/bin/env python3
"""День 29: измерение качества оптимизированной локальной LLM.

Оценивает профиль (см. `studio/backend/tuning.py`) на золотом наборе
(`series_gold.py`) по каталогу (`build_series_catalog.py`):

Метрики (все — машинно-проверяемые):

* **format_validity** — доля ответов, разобранных как JSON
  (`parse_series_answer().parsed`). Провал = модель не послушалась
  формата.
* **catalog_fidelity** — доля рекомендованных id, которых **нет** в
  каталоге (антигаллюцинация). Главная метрика кейса: выдуманный
  сериал — худшая ошибка.
* **constraint_satisfaction** — доля рекомендаций, удовлетворяющих
  ВСЕМ ограничениям запроса (жанр/год/страна).
* **coverage** — доля кейсов, где модель вернула >= `min_picks`
  подходящих рекомендаций.
* **empty_honesty** — доля «пустых» кейсов, где модель корректно
  сказала «нет» вместо выдумки.
* **latency_p50/p95**, **tokens_per_sec**, **completion_tokens** —
  стоимость ответа.

Провайдер — Ollama (OpenAI-совместимый `/chat/completions`), как в
днях 26/28. Кандидаты для промпта берутся **из каталога
детерминированно** (лексический отбор по ограничениям запроса), чтобы
измерять именно LLM, а не retrieval: у всех профилей вход одинаковый.

Запуск (нужна запущенная Ollama):
    python scripts/series_eval.py --profiles series,series_json
    python scripts/series_eval.py --profiles series --limit 20
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "studio", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from series_gold import GOLD, check_constraints  # noqa: E402
from tuning import (PROFILES, build_series_user_prompt,  # noqa: E402
                    parse_series_answer, profile_for)

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL",
                                 "http://localhost:11434/v1").rstrip("/")
# Нативный API (без /v1): нужен для num_ctx и think.
OLLAMA_NATIVE = (OLLAMA_BASE_URL[:-3]
                 if OLLAMA_BASE_URL.endswith("/v1") else OLLAMA_BASE_URL)
CATALOG_PATH = os.path.join(ROOT, "data", "series", "catalog.json")
REPORT_PATH = os.path.join(ROOT, "data", "series", "optimization_report.json")

# Сколько кандидатов показывать модели. Компромисс: больше кандидатов —
# выше шанс найти подходящее, но длиннее промпт (и дороже по num_ctx).
CANDIDATES_K = 24
# Сколько рекомендаций просить.
TOP_K = 5

CONFIG_PATH = os.path.join(ROOT, "studio", "data", "config.json")


def _config_model() -> str:
    """Модель из конфига Студии (нужна профилям без своей модели).

    Профиль `studio` намеренно не задаёт модель — в приложении берётся
    текущая модель диалога. В автономном замере такой модели нет, и
    Ollama отвечает 400 «model is required», поэтому подставляем
    значение из `studio/data/config.json` (или дефолт дня 26).
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            m = json.load(f).get("model")
        if isinstance(m, str) and m.strip():
            return m
    except (OSError, ValueError):
        pass
    return "qwen3-coder:30b"


# ---------------------------------------------------------------------------
# Отбор кандидатов (детерминированный, БЕЗ LLM — вход для всех профилей)
# ---------------------------------------------------------------------------

def _lexical_score(rec: dict, query: str, case: dict) -> tuple:
    """Оценка кандидата для запроса: сначала соответствие ограничениям,
    затем совпадение слов запроса с жанрами/названием/страной.

    Возвращает сортировочный ключ (больше — лучше). Детерминирован:
    одинаковый вход → одинаковый список кандидатов у всех профилей.
    """
    ok, _ = check_constraints(rec, case)
    q = query.lower()
    genre_hits = sum(1 for g in (rec.get("genres") or []) if g in q)
    title_hit = 1 if any(w in rec["title"].lower() for w in q.split()
                         if len(w) > 3) else 0
    country_hit = 1 if rec.get("country") and rec["country"].lower() in q else 0
    return (1 if ok else 0, genre_hits, country_hit, title_hit, rec["year"] or 0)


def select_candidates(catalog: list[dict], case: dict, k: int = CANDIDATES_K,
                      seed: int = 29) -> list[dict]:
    """Кандидаты для промпта: подходящие первыми, затем близкие.

    Важно: кандидатов **больше**, чем подходящих, и в списке есть
    «ловушки» (не подходящие по ограничениям) — иначе задача выбора
    тривиальна и метрика constraint_satisfaction ничего не измеряет.
    """
    scored = sorted(catalog, key=lambda r: _lexical_score(r, case["query"], case),
                    reverse=True)
    import random
    rng = random.Random(f"{seed}:{case['id']}")
    rng.shuffle(scored)  # устойчиво перемешать внутри равных ключей
    scored.sort(key=lambda r: _lexical_score(r, case["query"], case), reverse=True)
    return scored[:k]


# ---------------------------------------------------------------------------
# Вызов Ollama
# ---------------------------------------------------------------------------

def call_llm(profile, system: str, user: str, model: str | None = None,
             timeout: int = 300) -> dict:
    """Один вызов /chat/completions. Возвращает текст + метрики.

    Никогда не бросает: ошибка транспорта → ok=False, error=<текст>
    (в отчёте такие кейсы считаются провалом качества, но не ломают
    прогон).
    """
    # Профиль может не задавать модель (studio: берётся из общего
    # конфига). Без явной модели Ollama отвечает 400 «model is required»,
    # поэтому подставляем дефолтную модель конфига Студии.
    resolved = model or profile.model or _config_model()
    # Профиль с num_ctx (или think) идёт НАТИВНЫМ путём — как в
    # series.py: OpenAI-совместимый API не понимает num_ctx, а think
    # там вообще отсутствует. Замер обязан повторять боевой путь,
    # иначе цифры не про то, что работает в приложении.
    native = profile.num_ctx is not None or profile.think is not None
    if native:
        body = {
            "model": resolved,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {
                "temperature": profile.temperature,
                "top_p": profile.top_p,
                "num_predict": profile.num_predict,
                "num_ctx": profile.num_ctx,
            },
        }
        if profile.seed is not None:
            body["options"]["seed"] = profile.seed
        if profile.think is not None:
            body["think"] = profile.think
        if profile.format_json:
            body["format"] = "json"
        url = OLLAMA_NATIVE + "/api/chat"
    else:
        body = profile.sampling_body(model=resolved)
        body["messages"] = [{"role": "system", "content": system},
                            {"role": "user", "content": user}]
        body["stream"] = False
        url = OLLAMA_BASE_URL + "/chat/completions"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + os.environ.get("OLLAMA_API_KEY",
                                                              "ollama")})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:300]
        except Exception:
            pass
        return {"ok": False, "text": "", "sec": time.time() - t0,
                "error": f"HTTP {e.code}: {detail}", "usage": {}}
    except Exception as e:  # noqa: BLE001 — сеть/таймаут/битый JSON
        return {"ok": False, "text": "", "sec": time.time() - t0,
                "error": f"{type(e).__name__}: {e}"[:300], "usage": {}}

    sec = time.time() - t0
    if native:
        # Нативный формат: content в message, usage разложен по полям.
        msg = payload.get("message") or {}
        text = (msg.get("content") or "").strip()
        comp = payload.get("eval_count") or 0
        usage = {"prompt_tokens": payload.get("prompt_eval_count") or 0,
                 "completion_tokens": comp,
                 "total_tokens": (payload.get("prompt_eval_count") or 0) + comp}
        finish = payload.get("done_reason")
    else:
        choice = (payload.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        usage = payload.get("usage") or {}
        comp = usage.get("completion_tokens") or 0
        finish = choice.get("finish_reason")
    return {"ok": bool(text), "text": text, "sec": sec, "usage": usage,
            "error": None if text else "пустой ответ",
            "finish": finish,
            "tok_per_sec": (comp / sec) if (comp and sec > 0) else None}


# ---------------------------------------------------------------------------
# Оценка одного кейса
# ---------------------------------------------------------------------------

def eval_case(profile, case: dict, catalog: list[dict],
              by_id: dict[str, dict], limit: int = TOP_K) -> dict:
    """Прогнать один кейс: LLM → разбор → проверки по каталогу."""
    cands = select_candidates(catalog, case, seed=profile.seed or 29)
    # Нумеруем кандидатов 1..K — id в промпте и в ответе совпадают.
    for i, c in enumerate(cands, 1):
        c = dict(c)
    prompt_cands = []
    id_map: dict[str, dict] = {}
    for i, c in enumerate(cands, 1):
        cc = dict(c)
        cc["id"] = str(i)
        prompt_cands.append(cc)
        id_map[str(i)] = c   # id из промпта → реальная запись каталога

    user = build_series_user_prompt(case["query"], prompt_cands, limit=limit)
    resp = call_llm(profile, profile.system_prompt, user)
    parsed = parse_series_answer(resp.get("text", ""))

    picks = parsed["picks"]
    in_catalog, out_catalog, satisfying, viol = 0, 0, 0, []
    for p in picks:
        rec = id_map.get(p["id"])
        if rec is None:
            # Модель сослалась на id вне предложенного каталога —
            # это и есть галлюцинация (или неверный id).
            out_catalog += 1
            continue
        in_catalog += 1
        ok, problems = check_constraints(rec, case)
        if ok:
            satisfying += 1
        else:
            viol.append({"id": p["id"], "title": rec.get("title"),
                         "problems": problems})

    return {
        "case": case["id"],
        "kind": case["kind"],
        "query": case["query"],
        "ok": resp["ok"],
        "error": resp.get("error"),
        "sec": round(resp["sec"], 2),
        "tokens": (resp.get("usage") or {}).get("completion_tokens") or 0,
        "prompt_tokens": (resp.get("usage") or {}).get("prompt_tokens") or 0,
        "tok_per_sec": (round(resp["tok_per_sec"], 1)
                        if resp.get("tok_per_sec") else None),
        "parsed": parsed["parsed"],
        "n_picks": len(picks),
        "in_catalog": in_catalog,
        "out_catalog": out_catalog,
        "satisfying": satisfying,
        "violations": viol,
        "note": parsed["note"],
        "n_candidates": len(prompt_cands),
        "raw": (resp.get("text") or "")[:1500],
    }


# ---------------------------------------------------------------------------
# Агрегация
# ---------------------------------------------------------------------------

def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def aggregate(rows: list[dict]) -> dict:
    """Сводные метрики по всем кейсам."""
    n = len(rows)
    sat = [r for r in rows if r["kind"] == "satisfiable"]
    emp = [r for r in rows if r["kind"] == "empty"]

    total_picks = sum(r["n_picks"] for r in rows)
    total_out = sum(r["out_catalog"] for r in rows)
    total_in = sum(r["in_catalog"] for r in rows)
    total_sat = sum(r["satisfying"] for r in rows)

    secs = sorted(r["sec"] for r in rows if r["ok"])
    tps = [r["tok_per_sec"] for r in rows if r.get("tok_per_sec")]

    # Покрытие: кейс зачтён, если дал >= min_picks ПОДХОДЯЩИХ рекомендаций.
    covered = 0
    for r in sat:
        case = next(c for c in GOLD if c["id"] == r["case"])
        if r["satisfying"] >= case.get("min_picks", 1):
            covered += 1

    # Честность на пустых: 0 рекомендаций = корректное «нет».
    honest = sum(1 for r in emp if r["n_picks"] == 0)

    return {
        "cases": n,
        "format_validity": _rate(sum(1 for r in rows if r["parsed"]), n),
        "api_success": _rate(sum(1 for r in rows if r["ok"]), n),
        "hallucination_rate": _rate(total_out, total_picks),
        "catalog_fidelity": _rate(total_in, total_picks),
        "constraint_satisfaction": _rate(total_sat, total_in),
        "coverage": _rate(covered, len(sat)),
        "empty_honesty": _rate(honest, len(emp)),
        "avg_picks": round(total_picks / n, 2) if n else 0.0,
        "latency_p50": round(statistics.median(secs), 2) if secs else None,
        "latency_p95": (round(sorted(secs)[max(0, int(len(secs) * 0.95) - 1)], 2)
                        if secs else None),
        "tokens_per_sec": round(statistics.median(tps), 1) if tps else None,
        "total_completion_tokens": sum(r["tokens"] for r in rows),
        "satisfiable_cases": len(sat),
        "empty_cases": len(emp),
    }


def run_profile(profile_name: str, catalog: list[dict], limit: int,
                verbose: bool = True) -> dict:
    """Прогнать один профиль по всему золотому набору."""
    profile = profile_for(profile_name)
    by_id = {r["id"]: r for r in catalog}
    cases = GOLD[:limit] if limit else GOLD
    print(f"\n{'=' * 74}")
    print(f"ПРОФИЛЬ: {profile.name} — {profile.title}")
    print(f"  модель={profile.model} T={profile.temperature} "
          f"top_p={profile.top_p} num_predict={profile.num_predict} "
          f"num_ctx={profile.num_ctx} seed={profile.seed} "
          f"format_json={profile.format_json}")
    print(f"{'=' * 74}")

    rows = []
    for case in cases:
        r = eval_case(profile, case, catalog, by_id, limit=min(limit or 5, 5))
        rows.append(r)
        flag = "OK " if (r["parsed"] and r["out_catalog"] == 0) else "!! "
        if verbose:
            print(f"  {flag}{r['case']} {r['sec']:6.1f}s "
                  f"picks={r['n_picks']} in={r['in_catalog']} "
                  f"out={r['out_catalog']} sat={r['satisfying']} "
                  f"parsed={r['parsed']}")
            if r["error"]:
                print(f"       ошибка: {r['error']}")
            elif r["violations"]:
                print(f"       нарушения: {r['violations'][:2]}")

    agg = aggregate(rows)
    print(f"  --- {profile.name}: format={agg['format_validity']:.0%} "
          f"halluc={agg['hallucination_rate']:.1%} "
          f"constraint={agg['constraint_satisfaction']:.0%} "
          f"coverage={agg['coverage']:.0%} "
          f"empty_honesty={agg['empty_honesty']:.0%} "
          f"p50={agg['latency_p50']}s tok/s={agg['tokens_per_sec']}")
    return {"profile": profile.to_dict(), "metrics": agg, "cases": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default="studio,series,series_json",
                    help="через запятую; имена из tuning.PROFILES")
    ap.add_argument("--limit", type=int, default=0,
                    help="ограничить число кейсов (0 = весь набор)")
    ap.add_argument("--out", default=REPORT_PATH)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    catalog = json.load(open(CATALOG_PATH, encoding="utf-8"))
    print(f"Каталог: {len(catalog)} сериалов; золотой набор: {len(GOLD)} кейсов")

    names = [n.strip() for n in args.profiles.split(",") if n.strip()]
    for n in names:
        if n not in PROFILES:
            print(f"Неизвестный профиль: {n}", file=sys.stderr)
            return 2

    results = {}
    for n in names:
        results[n] = run_profile(n, catalog, args.limit, verbose=not args.quiet)

    # Сравнительная таблица
    print(f"\n{'=' * 74}\nСРАВНЕНИЕ ПРОФИЛЕЙ\n{'=' * 74}")
    hdr = (f"{'профиль':14} {'format':>7} {'halluc':>7} {'constr':>7} "
           f"{'cover':>6} {'empty':>6} {'p50,с':>7} {'tok/с':>6}")
    print(hdr)
    for n, res in results.items():
        m = res["metrics"]
        print(f"{n:14} {m['format_validity']:>7.0%} "
              f"{m['hallucination_rate']:>7.1%} "
              f"{m['constraint_satisfaction']:>7.0%} "
              f"{m['coverage']:>6.0%} {m['empty_honesty']:>6.0%} "
              f"{str(m['latency_p50']):>7} {str(m['tokens_per_sec']):>6}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"profiles": results,
                   "comparison": {n: r["metrics"] for n, r in results.items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"\nОтчёт: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
