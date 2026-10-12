#!/usr/bin/env python3
"""День 29: загрузка реального каталога сериалов из открытого источника.

Источник — Wikidata Query Service (SPARQL, публичный, без ключей).
Данные: сериалы (P31=Q5398426) с годом премьеры (P577), IMDb id (P345),
жанрами (P136), страной (P495), языком (P364), числом сезонов (P2437)
и серий (P1113).

Особенность источника: во время аварии WDQS включено жёсткое
ограничение «1 запрос в минуту» (HTTP 429). Поэтому скрипт делает ОДИН
большой запрос и терпеливо повторяет его при 429, выжидая паузу.
Данные кэшируются в `data/series/catalog_raw.json` — повторный запуск
без сети берёт кэш.

Только stdlib. Запуск:
    python scripts/fetch_series.py [--limit 3000] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WDQS = "https://query.wikidata.org/sparql"
UA = "ai-advent-challenge-day29/1.0 (educational project; local run)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "series")
RAW_PATH = os.path.join(OUT_DIR, "catalog_raw.json")

# Один запрос: чем шире выборка, тем лучше покрытие жанров и годов.
QUERY = """
SELECT ?series ?seriesLabel ?year ?imdb ?genreLabel ?countryLabel
       ?langLabel ?seasons ?episodes WHERE {
  ?series wdt:P31 wd:Q5398426 ;
          wdt:P577 ?date ;
          wdt:P345 ?imdb .
  OPTIONAL { ?series wdt:P136  ?genre }
  OPTIONAL { ?series wdt:P495  ?country }
  OPTIONAL { ?series wdt:P364  ?lang }
  OPTIONAL { ?series wdt:P2437 ?seasons }
  OPTIONAL { ?series wdt:P1113 ?episodes }
  BIND(YEAR(?date) AS ?year)
  FILTER(?year >= 2005)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru,en". }
}
LIMIT %d
"""


def fetch(limit: int, max_attempts: int = 6, wait: int = 70) -> list[dict]:
    """Запрос к WDQS с терпеливым повтором на 429 (лимит 1 req/min)."""
    url = WDQS + "?" + urllib.parse.urlencode(
        {"query": QUERY % limit, "format": "json"})
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/sparql-results+json",
    })
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                payload = json.loads(r.read().decode())
            return payload["results"]["bindings"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_attempts:
                print(f"  [{attempt}/{max_attempts}] 429 — ждём {wait} с "
                      f"(лимит WDQS 1 запрос/мин)")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_attempts:
                print(f"  [{attempt}/{max_attempts}] сеть: {e} — ждём {wait} с")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("не удалось получить данные от WDQS")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--force", action="store_true", help="игнорировать кэш")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    if os.path.exists(RAW_PATH) and not args.force:
        rows = json.load(open(RAW_PATH, encoding="utf-8"))
        print(f"Кэш: {RAW_PATH} ({len(rows)} строк). --force для перезагрузки.")
        return 0

    print(f"Запрос к Wikidata (LIMIT {args.limit})…")
    rows = fetch(args.limit)
    print(f"Получено строк: {len(rows)}")
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"Сохранено: {RAW_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
