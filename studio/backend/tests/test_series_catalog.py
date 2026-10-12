"""День 29: тесты подготовки каталога сериалов (офлайн, без сети).

Проверяют `scripts/build_series_catalog.py` на синтетических строках
WDQS: склейку дублей по IMDb id, канонизацию грязных жанровых меток,
отсев не-сюжетного и записей с QID вместо названия, окно годов и
стабильность id.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))


def _load():
    """Импорт build_series_catalog по пути (лежит в scripts/)."""
    spec = importlib.util.spec_from_file_location(
        "bsc", REPO / "scripts" / "build_series_catalog.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bsc = _load()


def row(title, year, imdb="tt1", genre=None, country=None, lang=None,
        seasons=None, episodes=None, xml_lang="ru"):
    """Строка ответа WDQS в формате SPARQL-JSON."""
    r = {
        "imdb": {"type": "literal", "value": imdb},
        "year": {"datatype": "http://www.w3.org/2001/XMLSchema#integer",
                 "type": "literal", "value": str(year)},
        "seriesLabel": {"xml:lang": xml_lang, "type": "literal", "value": title},
    }
    if genre is not None:
        r["genreLabel"] = {"xml:lang": "ru", "type": "literal", "value": genre}
    if country is not None:
        r["countryLabel"] = {"xml:lang": "ru", "type": "literal", "value": country}
    if lang is not None:
        r["langLabel"] = {"xml:lang": "ru", "type": "literal", "value": lang}
    if seasons is not None:
        r["seasons"] = {"type": "literal", "value": str(seasons)}
    if episodes is not None:
        r["episodes"] = {"type": "literal", "value": str(episodes)}
    return r


class TestCanonGenres:
    @pytest.mark.parametrize("label,expected", [
        ("драматический сериал", "драма"),
        ("драматический фильм", "драма"),
        ("драма", "драма"),
        ("криминальный телесериал", "криминал"),
        ("детективный сериал", "криминал"),
        ("комедийный телесериал", "комедия"),
        ("ситком", "комедия"),
        ("телесериал-триллер", "триллер"),
        ("фэнтезийный телесериал", "фэнтези"),
        ("научно-фантастический сериал", "фантастика"),
        ("историческая драма", "история"),
        ("мелодрама", "мелодрама"),
        ("теленовелла", "мелодрама"),
        ("анимационный сериал", "анимация"),
        ("документальная телепередача", "документальный"),
    ])
    def test_canonicalisation(self, label, expected):
        genres, _ = bsc.canon_genres([label])
        assert expected in genres

    def test_multiple_labels_deduplicated_and_sorted(self):
        genres, _ = bsc.canon_genres(["драма", "драматический сериал",
                                      "криминальный сериал"])
        assert genres == ["драма", "криминал"]   # сорт. + уникальные

    def test_non_genre_labels_become_tags(self):
        genres, tags = bsc.canon_genres(["сериал, связанный с ЛГБТ",
                                         "биографический фильм"])
        assert genres == []
        assert len(tags) == 2

    def test_empty_input(self):
        assert bsc.canon_genres([]) == ([], [])
        assert bsc.canon_genres([None, ""]) == ([], [])


class TestNormalize:
    def test_dedup_by_imdb_merges_rows(self):
        """Один сериал = много строк (жанры × страны) → одна запись."""
        rows = [
            row("Тест", 2020, "tt9", "драма", "США"),
            row("Тест", 2020, "tt9", "криминал", "Канада"),
        ]
        out = bsc.normalize(rows, 2010, 2025)
        assert len(out) == 1
        assert out[0]["genres"] == ["драма", "криминал"]
        assert out[0]["countries"] == ["Канада", "США"]
        assert out[0]["country"] == "Канада"      # первый по алфавиту

    def test_imdb_id_kept(self):
        out = bsc.normalize([row("A", 2020, "tt1234567", "драма")], 2010, 2025)
        assert out[0]["imdb"] == "tt1234567"

    @pytest.mark.parametrize("label", [
        "реалити-телевидение", "ток-шоу", "новости", "спортивный сериал",
        "кулинарное шоу", "телепередача", "музыкальное шоу",
    ])
    def test_non_scripted_dropped(self, label):
        assert bsc.normalize([row("Шоу", 2020, "tt1", label)], 2010, 2025) == []

    def test_qid_title_dropped(self):
        """Русская метка не нашлась → голый QID вместо названия."""
        assert bsc.normalize([row("Q104907097", 2020, "tt1", "драма")],
                             2010, 2025) == []

    def test_english_title_kept_when_ru_is_qid(self):
        rows = [row("Q123", 2020, "tt1", "драма", xml_lang="ru"),
                row("Real Title", 2020, "tt1", "драма", xml_lang="en")]
        out = bsc.normalize(rows, 2010, 2025)
        assert out[0]["title"] == "Real Title"

    def test_year_window_filter(self):
        rows = [row("Old", 2005, "tt1", "драма"),
                row("New", 2020, "tt2", "драма")]
        out = bsc.normalize(rows, 2010, 2025)
        assert [r["title"] for r in out] == ["New"]

    def test_missing_year_dropped(self):
        r = row("X", 2020, "tt1", "драма")
        del r["year"]
        assert bsc.normalize([r], 2010, 2025) == []

    def test_missing_genre_dropped(self):
        assert bsc.normalize([row("X", 2020, "tt1")], 2010, 2025) == []

    def test_sorted_newest_first(self):
        rows = [row("A", 2015, "tt1", "драма"), row("B", 2022, "tt2", "драма")]
        out = bsc.normalize(rows, 2010, 2025)
        assert [r["year"] for r in out] == [2022, 2015]

    def test_seasons_episodes_parsed(self):
        out = bsc.normalize([row("A", 2020, "tt1", "драма",
                                 seasons=3, episodes=24)], 2010, 2025)
        assert out[0]["seasons"] == 3 and out[0]["episodes"] == 24

    def test_empty_rows(self):
        assert bsc.normalize([], 2010, 2025) == []


class TestAssignIds:
    def test_ids_are_one_based_and_sequential(self):
        cat = [{"title": "a"}, {"title": "b"}, {"title": "c"}]
        bsc.assign_ids(cat)
        assert [r["id"] for r in cat] == ["1", "2", "3"]

    def test_ids_are_strings(self):
        cat = [{"title": "a"}]
        bsc.assign_ids(cat)
        assert isinstance(cat[0]["id"], str)


class TestWriteTxt:
    def test_txt_contains_key_fields(self, tmp_path):
        cat = [{"id": "1", "title": "Мрачный", "year": 2020,
                "genres": ["криминал"], "country": "Россия",
                "seasons": 2, "episodes": 16}]
        p = tmp_path / "c.txt"
        bsc.write_txt(cat, str(p))
        txt = p.read_text(encoding="utf-8")
        assert "[1] Мрачный (2020)" in txt
        assert "криминал" in txt and "Россия" in txt

    def test_txt_handles_missing_optional_fields(self, tmp_path):
        cat = [{"id": "1", "title": "X", "year": 2020, "genres": ["драма"],
                "country": None, "seasons": None, "episodes": None}]
        p = tmp_path / "c.txt"
        bsc.write_txt(cat, str(p))
        assert "X (2020)" in p.read_text(encoding="utf-8")


class TestRealCatalog:
    """Собранный каталог дня 29 (если есть) — валиден и пригоден."""

    def _catalog(self):
        p = REPO / "data" / "series" / "catalog.json"
        if not p.exists():
            pytest.skip("каталог не собран")
        return json.loads(p.read_text(encoding="utf-8"))

    def test_structure(self):
        cat = self._catalog()
        assert cat, "каталог пуст"
        for r in cat[:20]:
            assert r["id"] and r["title"] and isinstance(r["year"], int)
            assert isinstance(r["genres"], list) and r["genres"]

    def test_ids_unique_and_sequential(self):
        cat = self._catalog()
        ids = [r["id"] for r in cat]
        assert len(ids) == len(set(ids)), "дубли id"
        assert ids == [str(i) for i in range(1, len(cat) + 1)], "id не по порядку"

    def test_no_qid_titles(self):
        cat = self._catalog()
        bad = [r["title"] for r in cat
               if r["title"].startswith("Q") and r["title"][1:].isdigit()]
        assert not bad, f"QID вместо названия: {bad[:3]}"

    def test_no_non_scripted(self):
        cat = self._catalog()
        import re
        bad = [r["title"] for r in cat
               if any(re.search(r"реалити|ток-шоу|новост", g)
                      for g in r["genres"])]
        assert not bad
