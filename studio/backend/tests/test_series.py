"""День 29: тесты сервиса «Поиск сериалов» (офлайн, без сети).

Проверяют `series.py`: каталог/статистику, разбор запроса в
ограничения, детерминированный отбор кандидатов, вызов модели через
НАТИВНЫЙ `/api/chat` (когда профиль задаёт `num_ctx`) и через
OpenAI-совместимый API, подстановку полей из каталога, метрики и
сравнение профилей.

Сеть не нужна: `httpx.MockTransport` подменяет Ollama.
"""
import json

import httpx
import pytest

from series import (CANDIDATES_K, DEFAULT_SERIES_MODEL, SeriesError,
                    SeriesFinder, catalog_stats, load_catalog, load_report,
                    parse_query, select_candidates)

CATALOG = [
    {"id": 1, "imdb": "tt1", "title": "Мрачный детектив", "year": 2018,
     "genres": ["криминал", "драма"], "tags": [], "country": "Россия",
     "countries": ["Россия"], "languages": ["русский"],
     "seasons": 2, "episodes": 16},
    {"id": 2, "imdb": "tt2", "title": "Весёлая комедия", "year": 2021,
     "genres": ["комедия"], "tags": [], "country": "США",
     "countries": ["США"], "languages": ["английский"],
     "seasons": 3, "episodes": 30},
    {"id": 3, "imdb": "tt3", "title": "Старый криминал", "year": 2005,
     "genres": ["криминал"], "tags": [], "country": "США",
     "countries": ["США"], "languages": ["английский"],
     "seasons": None, "episodes": None},
    {"id": 4, "imdb": "tt4", "title": "Британская драма", "year": 2020,
     "genres": ["драма"], "tags": [], "country": "Великобритания",
     "countries": ["Великобритания"], "languages": ["английский"],
     "seasons": 1, "episodes": 8},
]

ANSWER = json.dumps({
    "picks": [{"id": "1", "title": "Мрачный детектив", "why": "подходит"}],
    "note": "отобрал по жанру",
}, ensure_ascii=False)


def _ochat_handler(captured: list):
    """Мок нативного /api/chat."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={
            "message": {"role": "assistant", "content": ANSWER},
            "prompt_eval_count": 120, "eval_count": 40,
            "done": True, "done_reason": "stop",
        })
    return handler


def _oai_handler(captured: list):
    """Мок OpenAI-совместимого /v1/chat/completions."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": ANSWER},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 40,
                      "total_tokens": 160},
        })
    return handler


def finder_with(handler, **kw) -> tuple[SeriesFinder, list]:
    captured: list = []
    client = httpx.Client(transport=httpx.MockTransport(handler(captured)))
    f = SeriesFinder(catalog=CATALOG, client=client, **kw)
    return f, captured


class TestCatalog:
    def test_load_missing_file_returns_empty(self, tmp_path):
        assert load_catalog(str(tmp_path / "nope.json")) == []

    def test_load_broken_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert load_catalog(str(p)) == []

    def test_load_real_file(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        assert len(load_catalog(str(p))) == 4

    def test_stats(self):
        s = catalog_stats(CATALOG)
        assert s["total"] == 4
        assert s["years"] == [2005, 2021]
        assert s["genres"]["драма"] == 2
        assert s["countries"]["США"] == 2

    def test_stats_empty(self):
        assert catalog_stats([])["years"] is None

    def test_real_catalog_loads_if_present(self):
        """Собранный каталог дня 29 (если есть) — валидный список."""
        cat = load_catalog()
        if cat:
            assert all("id" in r and "title" in r for r in cat[:10])


class TestParseQuery:
    def test_genre(self):
        assert "криминал" in parse_query("хочу криминальный сериал")["genres"]

    def test_genre_alias_detective(self):
        assert "криминал" in parse_query("посоветуй детектив")["genres"]

    def test_country_ru_adjective(self):
        assert parse_query("российская драма")["country"] == "Россия"

    def test_country_british(self):
        assert parse_query("британский детектив")["country"] == "Великобритания"

    def test_country_korea(self):
        assert parse_query("корейский сериал")["country"] == "Республика Корея"

    def test_min_year_after(self):
        assert parse_query("после 2015")["min_year"] == 2016

    def test_min_year_or_newer(self):
        assert parse_query("2019 года или новее")["min_year"] == 2019

    def test_max_year_not_older(self):
        assert parse_query("не старше 2019")["max_year"] == 2019

    def test_exact_year(self):
        c = parse_query("сериал 2010 года")
        assert (c["min_year"], c["max_year"]) == (2010, 2010)

    def test_no_constraints(self):
        c = parse_query("просто что-нибудь")
        assert c == {"genres": [], "country": None,
                     "min_year": None, "max_year": None}


class TestSelectCandidates:
    def test_matching_first(self):
        cands = select_candidates(CATALOG, "криминальный сериал после 2015")
        # «Мрачный детектив» (2018) подходит, «Старый криминал» (2005) — нет.
        assert cands[0]["title"] == "Мрачный детектив"

    def test_returns_up_to_k(self):
        assert len(select_candidates(CATALOG, "что угодно")) <= CANDIDATES_K

    def test_empty_catalog(self):
        assert select_candidates([], "что угодно") == []

    def test_country_filter_ranks_first(self):
        cands = select_candidates(CATALOG, "сериал из Великобритании")
        assert cands[0]["title"] == "Британская драма"


class TestRecommend:
    def test_native_endpoint_used_when_num_ctx_set(self):
        """Ключевая проверка дня 29: num_ctx → нативный /api/chat."""
        f, cap = finder_with(_ochat_handler)
        res = f.recommend("криминальный сериал после 2015", profile_name="series")
        assert cap[0]["options"]["num_ctx"] == 8192
        assert cap[0]["options"]["temperature"] == 0.2
        assert cap[0]["options"]["num_predict"] == 512
        assert res["metrics"]["num_ctx"] == 8192

    def test_openai_endpoint_when_no_num_ctx(self):
        """Профиль studio (num_ctx=None) идёт прежним путём."""
        f, cap = finder_with(_oai_handler)
        res = f.recommend("что-нибудь", profile_name="studio")
        assert "num_ctx" not in cap[0]
        assert cap[0]["max_tokens"] == 2048
        assert res["metrics"]["num_ctx"] is None

    def test_native_path_hits_native_url(self):
        urls = []
        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(200, json={
                "message": {"content": ANSWER}, "prompt_eval_count": 1,
                "eval_count": 1})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        SeriesFinder(catalog=CATALOG, client=client).recommend(
            "криминал", profile_name="series")
        assert urls[0].endswith("/api/chat")

    def test_openai_path_hits_v1_url(self):
        urls = []
        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(200, json={
                "choices": [{"message": {"content": ANSWER}}], "usage": {}})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        SeriesFinder(catalog=CATALOG, client=client).recommend(
            "криминал", profile_name="studio")
        assert urls[0].endswith("/v1/chat/completions")

    def test_recommendation_enriched_from_catalog(self):
        """Название/год/жанры берутся ИЗ КАТАЛОГА, а не из ответа модели."""
        f, _ = finder_with(_ochat_handler)
        res = f.recommend("криминальный сериал после 2015", profile_name="series")
        rec = res["recommendations"][0]
        assert rec["title"] == "Мрачный детектив"
        assert rec["year"] == 2018
        assert rec["imdb"] == "tt1"
        assert rec["satisfies"] is True

    def test_metrics_computed(self):
        f, _ = finder_with(_ochat_handler)
        m = f.recommend("криминальный сериал после 2015",
                        profile_name="series")["metrics"]
        assert m["parsed"] is True
        assert m["completion_tokens"] == 40
        assert m["prompt_tokens"] == 120
        assert m["hallucination_rate"] == 0.0
        assert m["constraint_satisfaction"] == 1.0
        assert m["tokens_per_sec"] is not None

    def test_hallucinated_id_detected(self):
        """id вне каталога → в hallucinated и в метрике."""
        def handler(request):
            body = json.dumps({"picks": [{"id": "999", "title": "Выдумка"}],
                               "note": ""}, ensure_ascii=False)
            return httpx.Response(200, json={
                "message": {"content": body}, "prompt_eval_count": 1,
                "eval_count": 1})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        res = SeriesFinder(catalog=CATALOG, client=client).recommend(
            "криминал", profile_name="series")
        assert res["recommendations"] == []
        assert len(res["hallucinated"]) == 1
        assert res["metrics"]["hallucination_rate"] == 1.0

    def test_unsatisfying_pick_flagged(self):
        """Рекомендация не по ограничениям → satisfies=False, метрика < 1."""
        def handler(request):
            # id=2 — комедия 2021, запрос требует криминал после 2015.
            body = json.dumps({"picks": [{"id": "2", "title": "x"}]},
                              ensure_ascii=False)
            return httpx.Response(200, json={
                "message": {"content": body}, "prompt_eval_count": 1,
                "eval_count": 1})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        res = SeriesFinder(catalog=CATALOG, client=client).recommend(
            "криминальный сериал после 2015", profile_name="series")
        assert res["recommendations"][0]["satisfies"] is False
        assert res["metrics"]["constraint_satisfaction"] == 0.0

    def test_empty_query_rejected(self):
        f, _ = finder_with(_ochat_handler)
        with pytest.raises(SeriesError, match="пустым"):
            f.recommend("   ", profile_name="series")

    def test_empty_catalog_rejected(self):
        client = httpx.Client(transport=httpx.MockTransport(_ochat_handler([])))
        with pytest.raises(SeriesError, match="Каталог"):
            SeriesFinder(catalog=[], client=client).recommend(
                "криминал", profile_name="series")

    def test_http_error_becomes_series_error(self):
        def handler(request):
            return httpx.Response(500, text="boom")
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(SeriesError, match="HTTP 500"):
            SeriesFinder(catalog=CATALOG, client=client).recommend(
                "криминал", profile_name="series")

    def test_transport_error_becomes_series_error(self):
        def handler(request):
            raise httpx.ConnectError("нет соединения")
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(SeriesError, match="Ollama"):
            SeriesFinder(catalog=CATALOG, client=client).recommend(
                "криминал", profile_name="series")

    def test_empty_model_answer_is_error(self):
        def handler(request):
            return httpx.Response(200, json={"message": {"content": ""},
                                             "prompt_eval_count": 0,
                                             "eval_count": 0})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(SeriesError, match="пустой ответ"):
            SeriesFinder(catalog=CATALOG, client=client).recommend(
                "криминал", profile_name="series")

    def test_candidates_numbered_from_one(self):
        f, _ = finder_with(_ochat_handler)
        res = f.recommend("криминал", profile_name="series")
        assert [c["id"] for c in res["candidates"]] == \
            [str(i) for i in range(1, len(res["candidates"]) + 1)]

    def test_request_preview_hides_full_system_prompt(self):
        f, _ = finder_with(_ochat_handler)
        body = f.recommend("криминал", profile_name="series")["request_body"]
        assert body["messages"][0]["content"].endswith("…")

    def test_seed_passed_in_native_options(self):
        f, cap = finder_with(_ochat_handler)
        f.recommend("криминал", profile_name="series")
        assert cap[0]["options"]["seed"] == 29

    def test_format_json_only_for_json_profile(self):
        f, cap = finder_with(_ochat_handler)
        f.recommend("криминал", profile_name="series")
        assert "format" not in cap[0]
        f2, cap2 = finder_with(_ochat_handler)
        f2.recommend("криминал", profile_name="series_json")
        assert cap2[0]["format"] == "json"

    def test_unknown_profile_falls_back(self):
        """Неизвестный профиль → studio (OpenAI-путь, num_ctx не задан)."""
        f, cap = finder_with(_oai_handler)
        res = f.recommend("криминал", profile_name="нет-такого")
        assert res["profile"] == "studio"
        assert "num_ctx" not in cap[0]


class TestCompare:
    def test_compare_summarises_profiles(self):
        f, _ = finder_with(_ochat_handler)
        out = f.compare(["криминал после 2015"], ["series", "series_json"])
        assert set(out["profiles"]) == {"series", "series_json"}
        assert out["profiles"]["series"]["format_validity"] == 1.0
        assert out["profiles"]["series"]["num_ctx"] == 8192
        assert len(out["rows"]) == 2

    def test_compare_isolates_failures(self):
        """Падение одного запроса не ломает сравнение."""
        def handler(request):
            return httpx.Response(500, text="boom")
        client = httpx.Client(transport=httpx.MockTransport(handler))
        out = SeriesFinder(catalog=CATALOG, client=client).compare(
            ["криминал"], ["series"])
        assert out["rows"][0]["ok"] is False
        assert out["profiles"]["series"]["ok_cases"] == 0


class TestReport:
    def test_missing_report(self, tmp_path):
        assert load_report(str(tmp_path / "nope.json")) == {}

    def test_broken_report(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text("nope", encoding="utf-8")
        assert load_report(str(p)) == {}

    def test_valid_report(self, tmp_path):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"a": 1}), encoding="utf-8")
        assert load_report(str(p)) == {"a": 1}


class TestThinkFlagWiring:
    """День 29: think=False уходит в НАТИВНЫЙ запрос для reasoning-моделей."""

    def test_think_false_sent_in_native_body(self):
        f, cap = finder_with(_ochat_handler)
        f.recommend("криминал", profile_name="series_glm")
        assert cap[0]["think"] is False

    def test_think_absent_when_none(self):
        f, cap = finder_with(_ochat_handler)
        f.recommend("криминал", profile_name="series")
        assert "think" not in cap[0]


class TestModelResolution:
    """Регрессия: профиль без модели не должен уходить без `model`.

    Профиль `studio` намеренно не задаёт модель (в приложении берётся
    текущая модель диалога). Запрос без модели Ollama отвергает с 400
    «model is required» — раньше это давало 502 на роуте поиска.
    """

    def test_default_model_used_when_profile_has_none(self):
        cap: list = []
        client = httpx.Client(transport=httpx.MockTransport(_oai_handler(cap)))
        f = SeriesFinder(catalog=CATALOG, client=client,
                         default_model="my-config-model")
        f.recommend("криминал", profile_name="studio")
        assert cap[0]["model"] == "my-config-model"

    def test_profile_model_wins_over_default(self):
        cap: list = []
        client = httpx.Client(transport=httpx.MockTransport(_ochat_handler(cap)))
        f = SeriesFinder(catalog=CATALOG, client=client,
                         default_model="my-config-model")
        f.recommend("криминал", profile_name="series")
        assert cap[0]["model"] == "qwen3-coder:30b"

    def test_explicit_model_wins_over_everything(self):
        cap: list = []
        client = httpx.Client(transport=httpx.MockTransport(_ochat_handler(cap)))
        f = SeriesFinder(catalog=CATALOG, client=client,
                         default_model="my-config-model")
        f.recommend("криминал", profile_name="series", model="override")
        assert cap[0]["model"] == "override"

    def test_fallback_when_nothing_set(self):
        """Даже без конфига модель не пустая (иначе 400 от Ollama)."""
        cap: list = []
        client = httpx.Client(transport=httpx.MockTransport(_oai_handler(cap)))
        SeriesFinder(catalog=CATALOG, client=client).recommend(
            "криминал", profile_name="studio")
        assert cap[0]["model"] == DEFAULT_SERIES_MODEL

    def test_never_returns_empty_model(self):
        from tuning import profile_for
        f = SeriesFinder(catalog=CATALOG)
        for name in ("studio", "series"):
            assert f._resolve_model(profile_for(name), None), name
