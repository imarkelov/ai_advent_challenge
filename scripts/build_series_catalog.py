#!/usr/bin/env python3
"""День 29: нормализация реального каталога сериалов (Wikidata → каталог).

Читает `data/series/catalog_raw.json` (выход `fetch_series.py`), чистит и
собирает компактный каталог для рекомендательной задачи:

* **дедупликация по IMDb id** — у одного сериала много строк (жанры ×
  страны), склеиваем их в одну запись;
* **нормализация жанров** — Wikidata даёт «драматический сериал»,
  «драматический фильм», «драма» как разные метки; сводим к
  каноническому набору (`драма`, `комедия`, `криминал`, …). Жанры
  нужны как рабочее ограничение запроса, поэтому мусорные/служебные
  метки («сериал, связанный с ЛГБТ», «исторический фильм») уходят в
  отдельный `tags`, а не в `genres`;
* **отсев не-сериалов** — реалити-шоу, ток-шоу, новости, спорт,
  музыкальные шоу: рекомендательная задача про сюжетные сериалы;
* **отсев неполных** — без года или без названия запись бесполезна;
* **стабильные числовые id** — по возрастанию IMDb id, чтобы `[n]` в
  промпте и `id` в ответе модели совпадали и были воспроизводимы.

Результат: `catalog.json` (для API/оценки) и `catalog.txt`
(человекочитаемый, для индексации в RAG).

Запуск:
    python scripts/build_series_catalog.py [--min-year 2010] [--limit 400]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "data", "series")
RAW_PATH = os.path.join(SRC_DIR, "catalog_raw.json")
OUT_JSON = os.path.join(SRC_DIR, "catalog.json")
OUT_TXT = os.path.join(SRC_DIR, "catalog.txt")

# ---------------------------------------------------------------------------
# Нормализация жанров
# ---------------------------------------------------------------------------

# Порядок важен: первое совпадение выигрывает, поэтому специфичные
# шаблоны («криминальный») стоят раньше общих («драма»).
GENRE_RULES: list[tuple[str, str]] = [
    (r"криминал|детектив|полицейск|гангстер", "криминал"),
    (r"триллер|саспенс", "триллер"),
    (r"ужас|хоррор", "ужасы"),
    (r"фантаст|научн\w* ?фантаст|\bсай-фай\b|\bфантастик", "фантастика"),
    (r"фэнтези|фентези", "фэнтези"),
    (r"историческ", "история"),
    (r"военн", "военный"),
    (r"вестерн", "вестерн"),
    (r"приключен", "приключения"),
    (r"боевик|экшен|action", "боевик"),
    (r"шпионск", "шпионский"),
    (r"мистик|сверхъестественн|паранормальн", "мистика"),
    (r"комедий|комедия|ситком|юмор", "комедия"),
    (r"мелодрам|романтическ|романтик|любовн", "мелодрама"),
    (r"теленовелла|мыльн\w* опера", "мелодрама"),
    (r"драм", "драма"),
    (r"мюзикл|музыкальн", "мюзикл"),
    (r"биографич|биографи", "биография"),
    (r"спорт", "спорт"),
    (r"медицин", "медицина"),
    (r"юридическ|судебн", "юридический"),
    (r"семейн", "семейный"),
    (r"детск|юношеск|подростков", "детский"),
    (r"анимац|мульт", "анимация"),
    (r"документальн|докудрам", "документальный"),
]

# Метки, по которым запись считается НЕ сюжетным сериалом (реалити,
# ток-шоу, новости, спорт, церемонии) — такие не рекомендуем.
NON_SCRIPTED = re.compile(
    r"реалити|ток-шоу|ток шоу|новост|спортивн|спорт|"
    r"телепередач|телешоу|игровое шоу|викторин|кулинарн|"
    r"музыкальн\w* шоу|церемони|преми|конкурс|интервью|"
    r"религиозн|телеигр|утренн\w* шоу|вечерн\w* шоу|стендап|"
    r"развлекательн|ток-передач|публицистик|программа",
    re.I)

# Метки-«не жанры»: попадают в tags, не в genres.
TAG_PATTERNS = re.compile(r"лгбт|биографи|экранизац|ремейк|"
                          r"основан\w* на|адаптац", re.I)


def canon_genres(labels: list[str]) -> tuple[list[str], list[str]]:
    """Метки Wikidata → (канонические жанры, прочие теги).

    Возвращает уникальные значения в устойчивом порядке (по алфавиту,
    чтобы каталог не зависел от порядка строк ответа WDQS).
    """
    genres, tags = set(), set()
    for lab in labels:
        if not lab:
            continue
        low = lab.lower().strip()
        if TAG_PATTERNS.search(low):
            tags.add(lab.strip())
            continue
        for pat, canon in GENRE_RULES:
            if re.search(pat, low):
                genres.add(canon)
                break
    return sorted(genres), sorted(tags)


# Русская метка не нашлась — Wikidata отдаёт голый QID («Q104907097»)
# вместо названия. Такая запись бесполезна для рекомендаций.
QID_ONLY = re.compile(r"^Q\d+$")


def _val(row: dict, key: str):
    """Значение поля SPARQL-строки (или None)."""
    cell = row.get(key)
    if isinstance(cell, dict):
        return cell.get("value")
    return None


def _num(row: dict, key: str):
    """Числовое поле SPARQL-строки (или None)."""
    v = _val(row, key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else round(f, 1)


def normalize(rows: list[dict], min_year: int, max_year: int) -> list[dict]:
    """Склейка строк WDQS в записи каталога (по IMDb id)."""
    by_imdb: dict[str, dict] = {}
    for r in rows:
        imdb = _val(r, "imdb")
        title = _val(r, "seriesLabel")
        year = _num(r, "year")
        if not imdb or not title or not year:
            continue
        if QID_ONLY.match(title.strip()):
            continue
        if not (min_year <= year <= max_year):
            continue
        rec = by_imdb.get(imdb)
        if rec is None:
            rec = {"imdb": imdb, "title": title, "year": year,
                   "_genres": [], "_tags": [], "_countries": [],
                   "languages": [], "seasons": None, "episodes": None}
            by_imdb[imdb] = rec
        rec["_genres"].append(_val(r, "genreLabel"))
        rec["_tags"].append(_val(r, "genreLabel"))
        c = _val(r, "countryLabel")
        if c:
            rec["_countries"].append(c)
        lang = _val(r, "langLabel")
        if lang:
            rec["languages"].append(lang)
        if rec["seasons"] is None:
            rec["seasons"] = _num(r, "seasons")
        if rec["episodes"] is None:
            rec["episodes"] = _num(r, "episodes")
        # Русская метка предпочтительнее английской, если пришла позже —
        # но не в том случае, когда «русской меткой» оказался голый QID
        # (тогда лучше оставить английское название).
        if (isinstance(r.get("seriesLabel"), dict)
                and r["seriesLabel"].get("xml:lang") == "ru"
                and not QID_ONLY.match(title.strip())):
            rec["title"] = title

    out = []
    for rec in by_imdb.values():
        raw_labels = [g for g in rec["_genres"] if g]
        # Отсев не-сюжетного: если ЛЮБАЯ метка — реалити/ток-шоу/новости,
        # запись не рекомендуем (у сюжетных сериалов таких меток нет).
        if any(NON_SCRIPTED.search(g) for g in raw_labels):
            continue
        genres, tags = canon_genres(raw_labels)
        if not genres:
            continue
        countries = sorted({c for c in rec["_countries"] if c})
        out.append({
            "imdb": rec["imdb"],
            "title": rec["title"].strip(),
            "year": rec["year"],
            "genres": genres,
            "tags": tags,
            "country": countries[0] if countries else None,
            "countries": countries,
            "languages": sorted(set(rec["languages"])),
            "seasons": rec["seasons"],
            "episodes": rec["episodes"],
        })
    # Стабильный порядок: свежие сериалы первыми, затем по IMDb id.
    out.sort(key=lambda r: (-r["year"], r["imdb"]))
    return out


def make_id(i: int) -> str:
    """Числовой id для промпта/ответа (1-based, стабильный)."""
    return str(i)


def assign_ids(catalog: list[dict]) -> None:
    """Проставить `id` = порядковый номер (1-based)."""
    for i, rec in enumerate(catalog, 1):
        rec["id"] = make_id(i)


def write_txt(catalog: list[dict], path: str) -> None:
    """Человекочитаемый каталог — источник для RAG-индекса."""
    lines = ["# Каталог сериалов (Wikidata, открытые данные)", ""]
    for rec in catalog:
        bits = [f"[{rec['id']}] {rec['title']} ({rec['year']})"]
        if rec["genres"]:
            bits.append("Жанры: " + ", ".join(rec["genres"]) + ".")
        if rec["country"]:
            bits.append(f"Страна: {rec['country']}.")
        if rec["seasons"]:
            bits.append(f"Сезонов: {rec['seasons']}.")
        if rec["episodes"]:
            bits.append(f"Эпизодов: {rec['episodes']}.")
        lines.append(" ".join(bits))
    lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-year", type=int, default=2010)
    ap.add_argument("--max-year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=400,
                    help="сколько записей оставить (свежие первыми)")
    args = ap.parse_args()

    if not os.path.exists(RAW_PATH):
        print(f"Нет {RAW_PATH} — сначала запустите scripts/fetch_series.py",
              file=sys.stderr)
        return 1

    rows = json.load(open(RAW_PATH, encoding="utf-8"))
    catalog = normalize(rows, args.min_year, args.max_year)
    print(f"После нормализации: {len(catalog)} записей "
          f"({args.min_year}–{args.max_year})")
    catalog = catalog[:args.limit]
    assign_ids(catalog)

    genres = Counter(g for r in catalog for g in r["genres"])
    countries = Counter(r["country"] for r in catalog if r["country"])
    print(f"Оставлено: {len(catalog)}; жанров: {len(genres)}; "
          f"стран: {len(countries)}")
    print("Топ-жанры:", ", ".join(f"{g}={n}" for g, n in genres.most_common(12)))
    print("Топ-страны:", ", ".join(f"{c}={n}" for c, n in countries.most_common(8)))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=1)
    write_txt(catalog, OUT_TXT)
    print(f"Сохранено: {OUT_JSON}, {OUT_TXT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
