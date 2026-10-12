#!/usr/bin/env python3
"""День 29: золотой набор запросов для оценки «поиска сериалов».

Запросы сформулированы так, чтобы ограничения были **проверяемы
машиной** по каталогу: жанр, год, страна, длина. Это превращает
«качество рекомендаций» из вкусовщины в метрику.

Каждый кейс:
  * `query`     — текст запроса пользователя (RU);
  * `genre`     — обязательный жанр (проверяется по `genres` записи);
  * `min_year` / `max_year` — окно года премьеры;
  * `country`   — обязательная страна (если задана);
  * `min_picks` — сколько рекомендаций ждём минимум;
  * `kind`      — `satisfiable` (в каталоге есть подходящие) или
                  `empty` (подходящих нет — модель обязана сказать
                  «нет», а не выдумывать).

Только stdlib. Импортируется скриптом оценки.
"""
from __future__ import annotations

# Золотой набор: 20 кейсов — 17 выполнимых + 3 «пустых» (проверка
# честного отказа). Пустые кейсы — это и есть главный тест на
# галлюцинации: соблазн «что-нибудь придумать» максимален.
GOLD: list[dict] = [
    {"id": "g01", "query": "Посоветуй мрачный криминальный сериал после 2015 года",
     "genre": "криминал", "min_year": 2016, "min_picks": 3, "kind": "satisfiable"},
    {"id": "g02", "query": "Хочу комедию не старше 2018 года, чтобы отвлечься",
     "genre": "комедия", "min_year": 2018, "min_picks": 3, "kind": "satisfiable"},
    {"id": "g03", "query": "Нужна фантастика или фэнтези, выпущенная после 2016",
     "genre": "фантастика", "min_year": 2017, "min_picks": 2, "kind": "satisfiable"},
    {"id": "g04", "query": "Посоветуй триллер 2019 года или новее",
     "genre": "триллер", "min_year": 2019, "min_picks": 2, "kind": "satisfiable"},
    {"id": "g05", "query": "Хочу драму из Великобритании после 2015",
     "genre": "драма", "min_year": 2016, "country": "Великобритания",
     "min_picks": 2, "kind": "satisfiable"},
    {"id": "g06", "query": "Посоветуй российский сериал 2018 года или новее",
     "min_year": 2018, "country": "Россия", "min_picks": 2,
     "kind": "satisfiable"},
    {"id": "g07", "query": "Что посмотреть из ужасов после 2018?",
     "genre": "ужасы", "min_year": 2019, "min_picks": 1, "kind": "satisfiable"},
    {"id": "g08", "query": "Хочу мелодраму 2020 года или свежее",
     "genre": "мелодрама", "min_year": 2020, "min_picks": 2, "kind": "satisfiable"},
    {"id": "g09", "query": "Посоветуй комедию из США после 2019",
     "genre": "комедия", "min_year": 2020, "country": "США",
     "min_picks": 1, "kind": "satisfiable"},
    {"id": "g10", "query": "Нужен исторический сериал после 2016",
     "genre": "история", "min_year": 2017, "min_picks": 1, "kind": "satisfiable"},
    {"id": "g11", "query": "Хочу боевик 2018 года или новее",
     "genre": "боевик", "min_year": 2018, "min_picks": 1, "kind": "satisfiable"},
    {"id": "g12", "query": "Посоветуй турецкий сериал после 2018",
     "min_year": 2019, "country": "Турция", "min_picks": 2, "kind": "satisfiable"},
    {"id": "g13", "query": "Интересует драма из Германии не старше 2019",
     "genre": "драма", "min_year": 2019, "country": "Германия",
     "min_picks": 1, "kind": "satisfiable"},
    {"id": "g14", "query": "Посоветуй приключенческий сериал после 2015",
     "genre": "приключения", "min_year": 2016, "min_picks": 1,
     "kind": "satisfiable"},
    {"id": "g15", "query": "Хочу фэнтези 2019 года или свежее",
     "genre": "фэнтези", "min_year": 2019, "min_picks": 1, "kind": "satisfiable"},
    {"id": "g16", "query": "Посоветуй комедию из Великобритании после 2017",
     "genre": "комедия", "min_year": 2018, "country": "Великобритания",
     "min_picks": 1, "kind": "satisfiable"},
    {"id": "g17", "query": "Нужна драма из Республики Корея после 2019",
     "genre": "драма", "min_year": 2020, "country": "Республика Корея",
     "min_picks": 1, "kind": "satisfiable"},
    # --- «Пустые» кейсы: подходящего в каталоге заведомо нет ---
    {"id": "g18", "query": "Посоветуй вестерн 2025 года",
     "genre": "вестерн", "min_year": 2025, "min_picks": 1, "kind": "empty"},
    {"id": "g19", "query": "Хочу аниме-сериал из Исландии после 2020",
     "genre": "анимация", "min_year": 2021, "country": "Исландия",
     "min_picks": 1, "kind": "empty"},
    {"id": "g20", "query": "Посоветуй сериал в жанре вестерн из Японии 2010 года",
     "genre": "вестерн", "min_year": 2010, "max_year": 2010,
     "country": "Япония", "min_picks": 1, "kind": "empty"},
]


def check_constraints(rec: dict, case: dict) -> tuple[bool, list[str]]:
    """Проверить запись каталога на соответствие ограничениям кейса.

    Возвращает (подходит, список_нарушений). Пустой список нарушений —
    запись удовлетворяет всем ограничениям запроса.
    """
    problems: list[str] = []
    genre = case.get("genre")
    if genre and genre not in (rec.get("genres") or []):
        problems.append(f"жанр: нужен {genre}, есть {rec.get('genres')}")
    year = rec.get("year")
    if case.get("min_year") and (year is None or year < case["min_year"]):
        problems.append(f"год: нужен >= {case['min_year']}, есть {year}")
    if case.get("max_year") and (year is None or year > case["max_year"]):
        problems.append(f"год: нужен <= {case['max_year']}, есть {year}")
    country = case.get("country")
    if country:
        countries = rec.get("countries") or ([rec["country"]] if rec.get("country") else [])
        if country not in countries:
            problems.append(f"страна: нужна {country}, есть {countries}")
    return (not problems), problems


def satisfiable_count(catalog: list[dict], case: dict) -> int:
    """Сколько записей каталога удовлетворяют кейсу (для валидации набора)."""
    return sum(1 for r in catalog if check_constraints(r, case)[0])


def load_gold() -> list[dict]:
    """Копия золотого набора (чтобы вызывающий не мутировал константу)."""
    return [dict(c) for c in GOLD]
