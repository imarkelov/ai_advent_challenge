"""День 29: тесты профилей оптимизации (офлайн, без сети).

Проверяют `tuning.py`: реестр профилей, тело запроса (в т.ч. что
`num_ctx` уходит только когда задан), промпт-шаблон под кейс
«поиск сериалов», устойчивый разбор JSON и валидацию параметров.
"""
import pytest

from tuning import (DEFAULT_PROFILE, PROFILES, SERIES_SYSTEM_PROMPT,
                    NUM_CTX_MAX, NUM_CTX_MIN, TaskProfile,
                    build_series_user_prompt, extract_json,
                    parse_series_answer, profile_for, profile_names,
                    validate_profile_params, with_overrides)

CANDIDATES = [
    {"id": 1, "title": "Dark", "year": 2017,
     "genres": ["фантастика", "триллер"], "country": "Германия",
     "rating": 8.7},
    {"id": 2, "title": "The Bear", "year": 2022,
     "genres": ["драма", "комедия"], "country": "США", "rating": 8.6},
]


class TestRegistry:
    def test_expected_profiles_present(self):
        for name in ("studio", "series", "series_q8", "series_q4",
                     "series_json"):
            assert name in PROFILES

    def test_quant_pair_is_same_model_family(self):
        """A/B квантования: series_q8 и series_q4 — ОДНА модель, разные
        кванты; иначе отличие в цифрах объяснялось бы сменой модели."""
        q8, q4 = PROFILES["series_q8"], PROFILES["series_q4"]
        assert q8.model == "gemma3:4b-it-q8_0"
        assert q4.model == "gemma3:4b"
        # всё остальное в паре совпадает — отличается только квант
        assert q8.temperature == q4.temperature
        assert q8.num_predict == q4.num_predict
        assert q8.num_ctx == q4.num_ctx
        assert q8.seed == q4.seed
        assert q8.system_prompt == q4.system_prompt

    def test_names_order_and_default(self):
        assert profile_names()[0] == "studio"
        assert DEFAULT_PROFILE == "studio"

    def test_profile_for_known(self):
        assert profile_for("series").name == "series"

    @pytest.mark.parametrize("bad", [None, "", "nope"])
    def test_profile_for_unknown_falls_back_to_studio(self, bad):
        assert profile_for(bad).name == "studio"

    def test_studio_profile_preserves_legacy_behaviour(self):
        """Регрессия: дефолтный профиль = поведение дней 11–28."""
        p = profile_for("studio")
        assert p.temperature == 0.7
        assert p.num_predict == 2048
        assert p.num_ctx is None          # окно не переопределяется
        assert p.format_json is False

    def test_series_profile_is_tuned(self):
        """Кейс дня 29: ниже temperature, меньше бюджет, окно задано."""
        p = profile_for("series")
        assert p.temperature < profile_for("studio").temperature
        assert p.num_predict < profile_for("studio").num_predict
        assert p.num_ctx is not None
        assert p.system_prompt == SERIES_SYSTEM_PROMPT

    def test_profiles_are_frozen(self):
        with pytest.raises(Exception):
            profile_for("series").temperature = 1.5

    def test_with_overrides_returns_copy(self):
        p = profile_for("series")
        q = with_overrides(p, temperature=0.9)
        assert q.temperature == 0.9 and p.temperature == 0.2
        assert q.name == p.name

    def test_to_dict_is_json_ready(self):
        d = profile_for("series").to_dict()
        assert d["stop"] == []            # tuple → list
        assert isinstance(d["system_prompt"], str)
        assert d["num_ctx"] == 8192


class TestSamplingBody:
    def test_series_body_has_tuned_values(self):
        body = profile_for("series").sampling_body()
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 512
        assert body["num_ctx"] == 8192
        assert body["seed"] == 29

    def test_studio_body_omits_num_ctx(self):
        """Регрессия: без num_ctx запрос уходит как раньше."""
        body = profile_for("studio").sampling_body()
        assert "num_ctx" not in body
        assert "seed" not in body

    def test_model_override(self):
        body = profile_for("series").sampling_body(model="gemma3:27b")
        assert body["model"] == "gemma3:27b"

    def test_json_profile_sets_response_format(self):
        assert (profile_for("series_json").sampling_body()["response_format"]
                == {"type": "json_object"})
        assert "response_format" not in profile_for("series").sampling_body()

    def test_stop_only_when_set(self):
        p = with_overrides(profile_for("series"), stop=("</s>",))
        assert p.sampling_body()["stop"] == ["</s>"]

    def test_default_num_ctx_is_none(self):
        assert TaskProfile(name="x", title="x", description="x").num_ctx is None


class TestSeriesPrompt:
    def test_user_prompt_contains_query_and_catalog(self):
        txt = build_series_user_prompt("мрачный детектив", CANDIDATES)
        assert "мрачный детектив" in txt
        assert "[1] Dark" in txt and "[2] The Bear" in txt
        assert "2017" in txt and "Германия" in txt and "IMDb 8.7" in txt

    def test_user_prompt_marks_empty_catalog(self):
        txt = build_series_user_prompt("что-нибудь", [])
        assert "каталог пуст" in txt

    def test_user_prompt_respects_limit(self):
        assert "до 3 рекомендаций" in build_series_user_prompt("x", CANDIDATES,
                                                               limit=3)

    def test_system_prompt_forbids_invention(self):
        assert "ТОЛЬКО сериалы из каталога" in SERIES_SYSTEM_PROMPT
        assert "Запрещено придумывать" in SERIES_SYSTEM_PROMPT

    def test_system_prompt_asks_for_json_and_has_example(self):
        assert "JSON" in SERIES_SYSTEM_PROMPT
        assert '"picks"' in SERIES_SYSTEM_PROMPT
        assert "ПРИМЕР" in SERIES_SYSTEM_PROMPT

    def test_system_prompt_differs_from_studio(self):
        assert SERIES_SYSTEM_PROMPT != profile_for("studio").system_prompt


class TestExtractJson:
    def test_plain_object(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_block(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_unlabelled(self):
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_text_around_object(self):
        assert extract_json('Вот ответ:\n{"a": 1}\nГотово.') == {"a": 1}

    def test_array(self):
        assert extract_json('Итог: [{"a": 1}]') == [{"a": 1}]

    def test_nested_braces(self):
        assert extract_json('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}

    def test_brace_inside_string_not_confused(self):
        assert extract_json('{"a": "} not the end"}') == {"a": "} not the end"}

    def test_escaped_quote_inside_string(self):
        assert extract_json(r'{"a": "say \"hi\""}') == {"a": 'say "hi"'}

    @pytest.mark.parametrize("bad", ["", "нет json", "{broken", None])
    def test_garbage_returns_none(self, bad):
        assert extract_json(bad) is None


class TestParseSeriesAnswer:
    def test_wellformed(self):
        raw = ('{"picks": [{"id": "1", "title": "Dark", "why": "подходит"}],'
               ' "note": "отобрал по жанру"}')
        out = parse_series_answer(raw)
        assert out["parsed"] is True
        assert out["picks"] == [{"id": "1", "title": "Dark", "why": "подходит"}]
        assert out["note"] == "отобрал по жанру"

    def test_numeric_id_becomes_string(self):
        out = parse_series_answer('{"picks": [{"id": 2, "title": "T"}]}')
        assert out["picks"][0]["id"] == "2"

    def test_fenced_answer(self):
        out = parse_series_answer('```json\n{"picks": [{"id": "1"}]}\n```')
        assert out["parsed"] is True and out["picks"][0]["id"] == "1"

    def test_bare_list_accepted(self):
        out = parse_series_answer('[{"id": "7", "title": "X"}]')
        assert out["parsed"] is True and out["picks"][0]["id"] == "7"

    def test_single_object_accepted(self):
        out = parse_series_answer('{"id": "3", "title": "Solo"}')
        assert out["parsed"] is True and out["picks"][0]["id"] == "3"

    def test_empty_picks_valid(self):
        out = parse_series_answer('{"picks": [], "note": "нет подходящих"}')
        assert out["parsed"] is True and out["picks"] == []

    def test_garbage_is_not_parsed(self):
        out = parse_series_answer("Не могу подобрать, извините.")
        assert out["parsed"] is False and out["picks"] == []
        assert out["raw"].startswith("Не могу")

    def test_pick_without_id_skipped(self):
        out = parse_series_answer('{"picks": [{"title": "no id"}, '
                                  '{"id": "1", "title": "ok"}]}')
        assert [p["id"] for p in out["picks"]] == ["1"]

    def test_missing_fields_default_empty(self):
        out = parse_series_answer('{"picks": [{"id": "1"}]}')
        assert out["picks"][0] == {"id": "1", "title": "", "why": ""}

    def test_never_raises_on_none(self):
        assert parse_series_answer(None)["parsed"] is False


class TestValidateProfileParams:
    def test_valid_passes_through(self):
        out = validate_profile_params(temperature=0.2, top_p=0.9,
                                      num_predict=512, num_ctx=8192, seed=29)
        assert out == {"temperature": 0.2, "top_p": 0.9, "num_predict": 512,
                       "num_ctx": 8192, "seed": 29}

    def test_only_provided_keys_returned(self):
        assert validate_profile_params(temperature=0.5) == {"temperature": 0.5}

    @pytest.mark.parametrize("bad", [-0.1, 2.1, "hot", True])
    def test_bad_temperature(self, bad):
        with pytest.raises(ValueError, match="temperature"):
            validate_profile_params(temperature=bad)

    @pytest.mark.parametrize("bad", [0, 1.5, "x", True])
    def test_bad_top_p(self, bad):
        with pytest.raises(ValueError, match="top_p"):
            validate_profile_params(top_p=bad)

    @pytest.mark.parametrize("bad", [0, -5, 1.5, "x"])
    def test_bad_num_predict(self, bad):
        with pytest.raises(ValueError, match="num_predict"):
            validate_profile_params(num_predict=bad)

    def test_num_predict_upper_bound(self):
        with pytest.raises(ValueError, match="num_predict"):
            validate_profile_params(num_predict=32_769)

    @pytest.mark.parametrize("bad", [0, 100, NUM_CTX_MAX + 1, "big"])
    def test_bad_num_ctx(self, bad):
        with pytest.raises(ValueError, match="num_ctx"):
            validate_profile_params(num_ctx=bad)

    def test_num_ctx_bounds_accepted(self):
        assert validate_profile_params(num_ctx=NUM_CTX_MIN)["num_ctx"] == NUM_CTX_MIN
        assert validate_profile_params(num_ctx=NUM_CTX_MAX)["num_ctx"] == NUM_CTX_MAX

    def test_bad_seed(self):
        with pytest.raises(ValueError, match="seed"):
            validate_profile_params(seed="x")

    def test_seed_zero_is_valid(self):
        assert validate_profile_params(seed=0) == {"seed": 0}


class TestThinkFlag:
    """`think=False` для reasoning-моделей (находка дня 29).

    glm-4.7-flash пишет рассуждения в отдельное поле `thinking`, и при
    исчерпанном num_predict `content` пуст: в замере — 0 рекомендаций на
    20/20 кейсов. Отключение размышления возвращает ответ.
    """

    def test_glm_profile_disables_thinking(self):
        assert PROFILES["series_glm"].think is False

    def test_default_is_none(self):
        """Прочие профили не трогают параметр (поведение модели)."""
        assert PROFILES["series"].think is None
        assert PROFILES["studio"].think is None

    def test_think_not_in_sampling_body(self):
        """think — нативный ключ; в OpenAI-совместимое тело не попадает."""
        assert "think" not in PROFILES["series_glm"].sampling_body()

    def test_think_in_profile_dict(self):
        assert PROFILES["series_glm"].to_dict()["think"] is False
