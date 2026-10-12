"""Тесты дня 29: роуты оптимизации локальной LLM и поиска сериалов.

Офлайн: каталог-фикстура, `httpx.MockTransport` вместо Ollama,
tmp-каталог данных (реальный `studio/data` не трогается).
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main as main_mod
from agent import StudioAgent
from kb import KnowledgeBase
from series import SeriesFinder

CATALOG = [
    {"id": 1, "imdb": "tt1", "title": "Мрачный детектив", "year": 2018,
     "genres": ["криминал"], "tags": [], "country": "Россия",
     "countries": ["Россия"], "languages": ["русский"],
     "seasons": 2, "episodes": 16},
    {"id": 2, "imdb": "tt2", "title": "Весёлая комедия", "year": 2021,
     "genres": ["комедия"], "tags": [], "country": "США",
     "countries": ["США"], "languages": ["английский"],
     "seasons": 3, "episodes": 30},
]

ANSWER = json.dumps({"picks": [{"id": "1", "title": "Мрачный детектив",
                                "why": "подходит"}],
                     "note": "по жанру"}, ensure_ascii=False)


def _native_handler(captured=None):
    """Мок Ollama: отвечает и на нативный /api/chat, и на
    OpenAI-совместимый /v1/chat/completions (профиль studio идёт вторым
    путём — без num_ctx)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content.decode()))
        if str(request.url).endswith("/api/chat"):
            return httpx.Response(200, json={
                "message": {"role": "assistant", "content": ANSWER},
                "prompt_eval_count": 100, "eval_count": 30, "done": True,
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": ANSWER},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                      "total_tokens": 130},
        })
    return handler


@pytest.fixture
def client(tmp_path):
    """TestClient с изолированным agent/kb/series (каталог-фикстура)."""
    agent = StudioAgent(str(tmp_path))
    kb = KnowledgeBase(str(tmp_path / "kb"))
    http = httpx.Client(transport=httpx.MockTransport(_native_handler()))
    finder = SeriesFinder(catalog=CATALOG, client=http)
    app = main_mod.create_app(agent=agent, kb=kb, series=finder)
    return TestClient(app)


class TestProfilesRoute:
    def test_list_profiles(self, client):
        r = client.get("/api/tuning/profiles")
        assert r.status_code == 200
        j = r.json()
        names = [p["name"] for p in j["profiles"]]
        # studio — дефолт; series — кейс дня; series_q8/series_q4 —
        # A/B-пара для замера квантования; series_gemma/series_glm —
        # другие семейства на том же кванте; series_json — контроль.
        assert names == ["studio", "series", "series_q8", "series_q4",
                         "series_gemma", "series_glm", "series_json"]
        assert j["active"] == "studio"

    def test_profile_payload_has_tuning_fields(self, client):
        j = client.get("/api/tuning/profiles").json()
        series = next(p for p in j["profiles"] if p["name"] == "series")
        assert series["num_ctx"] == 8192
        assert series["temperature"] == 0.2
        assert series["num_predict"] == 512
        assert "каталог" in series["system_prompt"].lower()

    def test_set_profile(self, client):
        r = client.post("/api/tuning/profile", json={"profile": "series"})
        assert r.status_code == 200
        assert r.json()["active"] == "series"
        assert client.get("/api/tuning/profiles").json()["active"] == "series"

    def test_set_unknown_profile_400(self, client):
        r = client.post("/api/tuning/profile", json={"profile": "nope"})
        assert r.status_code == 400
        assert "Неизвестный профиль" in r.json()["detail"]

    def test_set_empty_profile_400(self, client):
        assert client.post("/api/tuning/profile",
                           json={"profile": "  "}).status_code == 400

    def test_set_non_string_profile_400(self, client):
        assert client.post("/api/tuning/profile",
                           json={"profile": 5}).status_code == 400

    def test_active_profile_persists_across_requests(self, client):
        client.post("/api/tuning/profile", json={"profile": "series_json"})
        assert client.get("/api/tuning/profiles").json()["active"] == "series_json"


class TestCatalogRoute:
    def test_catalog_stats(self, client):
        r = client.get("/api/series/catalog")
        assert r.status_code == 200
        j = r.json()
        assert j["ready"] is True
        assert j["stats"]["total"] == 2
        assert j["stats"]["genres"]["криминал"] == 1
        assert len(j["sample"]) == 2


class TestSearchRoute:
    def test_search_ok(self, client):
        r = client.post("/api/series/search", json={"query": "криминал после 2015"})
        assert r.status_code == 200
        j = r.json()
        assert j["recommendations"][0]["title"] == "Мрачный детектив"
        assert j["parsed"] is True
        assert j["metrics"]["hallucination_rate"] == 0.0

    def test_search_uses_active_profile(self, client):
        client.post("/api/tuning/profile", json={"profile": "series"})
        j = client.post("/api/series/search", json={"query": "криминал"}).json()
        assert j["profile"] == "series"
        assert j["metrics"]["num_ctx"] == 8192

    def test_explicit_profile_overrides_active(self, client):
        client.post("/api/tuning/profile", json={"profile": "studio"})
        j = client.post("/api/series/search",
                        json={"query": "криминал",
                              "profile": "series"}).json()
        assert j["profile"] == "series"

    def test_empty_query_400(self, client):
        assert client.post("/api/series/search",
                           json={"query": "   "}).status_code == 400

    def test_missing_query_400(self, client):
        assert client.post("/api/series/search", json={}).status_code == 400

    def test_unknown_profile_400(self, client):
        r = client.post("/api/series/search",
                        json={"query": "криминал", "profile": "nope"})
        assert r.status_code == 400
        assert "Неизвестный профиль" in r.json()["detail"]

    @pytest.mark.parametrize("bad", [0, 11, "3", True, 2.5])
    def test_bad_limit_400(self, client, bad):
        r = client.post("/api/series/search",
                        json={"query": "криминал", "limit": bad})
        assert r.status_code == 400

    def test_ollama_failure_502(self, tmp_path):
        def boom(request):
            raise httpx.ConnectError("нет соединения")
        agent = StudioAgent(str(tmp_path))
        kb = KnowledgeBase(str(tmp_path / "kb"))
        finder = SeriesFinder(
            catalog=CATALOG,
            client=httpx.Client(transport=httpx.MockTransport(boom)))
        c = TestClient(main_mod.create_app(agent=agent, kb=kb, series=finder))
        r = c.post("/api/series/search", json={"query": "криминал"})
        assert r.status_code == 502

    def test_empty_catalog_502(self, tmp_path):
        agent = StudioAgent(str(tmp_path))
        kb = KnowledgeBase(str(tmp_path / "kb"))
        finder = SeriesFinder(
            catalog=[],
            client=httpx.Client(
                transport=httpx.MockTransport(_native_handler())))
        c = TestClient(main_mod.create_app(agent=agent, kb=kb, series=finder))
        r = c.post("/api/series/search", json={"query": "криминал"})
        assert r.status_code == 502
        assert "Каталог" in r.json()["detail"]


class TestCompareRoute:
    def test_compare_ok(self, client):
        r = client.post("/api/series/compare",
                        json={"queries": ["криминал"], "profiles": ["series"]})
        assert r.status_code == 200
        assert "series" in r.json()["profiles"]

    def test_compare_defaults_profiles(self, client):
        r = client.post("/api/series/compare", json={"queries": ["криминал"]})
        assert r.status_code == 200
        assert set(r.json()["profiles"]) == {"series", "series_json"}

    def test_empty_queries_400(self, client):
        assert client.post("/api/series/compare",
                           json={"queries": []}).status_code == 400

    def test_non_list_queries_400(self, client):
        assert client.post("/api/series/compare",
                           json={"queries": "криминал"}).status_code == 400

    def test_blank_query_in_list_400(self, client):
        assert client.post("/api/series/compare",
                           json={"queries": ["ок", "  "]}).status_code == 400

    def test_unknown_profile_400(self, client):
        r = client.post("/api/series/compare",
                        json={"queries": ["x"], "profiles": ["nope"]})
        assert r.status_code == 400


class TestReportRoute:
    def test_report_empty_when_absent(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "load_report", lambda *a, **k: {})
        assert client.get("/api/tuning/report").json() == {}

    def test_report_returned(self, client, monkeypatch):
        monkeypatch.setattr(main_mod, "load_report",
                            lambda *a, **k: {"profiles": {"series": {}}})
        assert client.get("/api/tuning/report").json()["profiles"]["series"] == {}
