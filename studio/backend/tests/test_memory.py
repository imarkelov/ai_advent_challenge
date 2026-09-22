"""Тесты MemoryStore: ST/диалоги, WM, LT, build_memory_blocks, layer_stats."""
import json
import math

import pytest

from memory import MemoryStore, new_profile, new_task


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def store(data_dir):
    return MemoryStore(str(data_dir))


# ---------- диалоги (ST) ----------

def test_new_dialogue_auto_activate(store):
    d = store.new_dialogue()
    assert set(d) == {"id", "title", "created", "messages", "profile"}
    assert d["messages"] == []
    assert store.active_id() == d["id"]


def test_list_dialogues_order_and_shape(store):
    a = store.new_dialogue()
    b = store.new_dialogue()
    store.append_message(a["id"], "user", "привет")
    lst = store.list_dialogues()
    assert [x["id"] for x in lst] == [a["id"], b["id"]]  # порядок создания
    assert lst[0] == {"id": a["id"], "title": a["title"],
                      "created": a["created"], "message_count": 1,
                      "used_task": False,
                      "profile": new_profile(), "task": new_task()}
    assert lst[1]["message_count"] == 0


def test_get_dialogue_and_messages(store):
    d = store.new_dialogue()
    store.append_message(d["id"], "user", "привет")
    store.append_message(d["id"], "assistant", "здравствуйте")
    got = store.get_dialogue(d["id"])
    assert got["id"] == d["id"]
    assert store.get_messages(d["id"]) == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "здравствуйте"},
    ]
    assert store.get_dialogue("nope") is None
    assert store.get_messages("nope") == []


def test_activate_existing_and_fallback_active(store):
    a = store.new_dialogue()
    b = store.new_dialogue()
    assert store.active_id() == b["id"]
    store.activate(a["id"])
    assert store.active_id() == a["id"]


def test_activate_missing_raises(store):
    with pytest.raises(ValueError):
        store.activate("nope")


def test_append_message_missing_dialogue_raises(store):
    with pytest.raises(ValueError):
        store.append_message("nope", "user", "x")


def test_delete_dialogue_removes_wm_and_active_fallback(store):
    a = store.new_dialogue()
    b = store.new_dialogue()
    store.wm_set(a["id"], "k", "v")
    store.delete_dialogue(a["id"])
    assert store.get_dialogue(a["id"]) is None
    assert store.wm_items(a["id"]) == {}
    assert store.active_id() == b["id"]  # активным стал последний оставшийся


def test_delete_last_dialogue_active_none(store):
    a = store.new_dialogue()
    store.delete_dialogue(a["id"])
    assert store.active_id() is None
    assert store.list_dialogues() == []


def test_delete_missing_dialogue_is_noop(store):
    store.delete_dialogue("nope")  # не бросает
    store.clear_st("nope")  # не бросает


def test_rename_dialogue(store):
    d = store.new_dialogue()
    store.rename_dialogue(d["id"], "Мой диалог")
    assert store.get_dialogue(d["id"])["title"] == "Мой диалог"


def test_rename_dialogue_missing_raises(store):
    with pytest.raises(ValueError):
        store.rename_dialogue("nope", "x")


def test_rename_dialogue_empty_title_raises(store):
    d = store.new_dialogue()
    with pytest.raises(ValueError):
        store.rename_dialogue(d["id"], "   ")


def test_clear_st(store):
    d = store.new_dialogue()
    store.append_message(d["id"], "user", "x")
    store.clear_st(d["id"])
    assert store.get_messages(d["id"]) == []


# ---------- WM (per-dialogue) ----------

def test_append_message_with_model(store):
    """Assistant-сообщение хранится с model; user — без."""
    d = store.new_dialogue()
    store.append_message(d["id"], "assistant", "ok", model="qwen3.8-27b")
    store.append_message(d["id"], "user", "hi")
    msgs = store.get_messages(d["id"])
    assert msgs[0] == {"role": "assistant", "content": "ok", "model": "qwen3.8-27b"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_wm_isolation_between_dialogues(store):
    a = store.new_dialogue()
    b = store.new_dialogue()
    store.wm_set(a["id"], "task", "A")
    store.wm_set(b["id"], "task", "B")
    store.wm_set(b["id"], "other", "B2")
    assert store.wm_items(a["id"]) == {"task": "A"}
    assert store.wm_items(b["id"]) == {"task": "B", "other": "B2"}
    assert store.wm_items("nope") == {}


def test_wm_remove_and_clear(store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "k", "v")
    assert store.wm_remove(d["id"], "k") is True
    assert store.wm_remove(d["id"], "k") is False
    store.wm_set(d["id"], "k2", "v2")
    store.wm_clear(d["id"])
    assert store.wm_items(d["id"]) == {}


# ---------- LT (глобальная, персистентность) ----------

def test_lt_crud(store):
    store.lt_set("k1", "v1")
    store.lt_set("k2", "v2")
    assert store.lt_items() == {"k1": "v1", "k2": "v2"}
    assert store.lt_remove("k1") is True
    assert store.lt_remove("k1") is False
    assert store.lt_items() == {"k2": "v2"}
    store.lt_clear()
    assert store.lt_items() == {}


def test_lt_persistence_across_instances(data_dir):
    s1 = MemoryStore(str(data_dir))
    s1.lt_set("user", "морг")
    d = s1.new_dialogue()
    s1.append_message(d["id"], "user", "сообщение")
    s1.wm_set(d["id"], "task", "задача")
    # НОВЫЙ экземпляр в том же dir видит всё
    s2 = MemoryStore(str(data_dir))
    assert s2.lt_items() == {"user": "морг"}
    assert s2.get_messages(d["id"]) == [{"role": "user", "content": "сообщение"}]
    assert s2.wm_items(d["id"]) == {"task": "задача"}
    assert s2.active_id() == d["id"]


def test_broken_json_files_give_defaults(data_dir):
    (data_dir / "dialogues.json").write_text("{битый json", encoding="utf-8")
    (data_dir / "working.json").write_text("][", encoding="utf-8")
    (data_dir / "longterm.json").write_text("null и мусор", encoding="utf-8")
    s = MemoryStore(str(data_dir))
    assert s.list_dialogues() == []
    assert s.active_id() is None
    assert s.lt_items() == {}
    assert s.wm_items("any") == {}
    # после чтения битых файлов мутации работают
    d = s.new_dialogue()
    s.lt_set("k", "v")
    assert s.active_id() == d["id"]
    assert s.lt_items() == {"k": "v"}


def test_dialogues_json_shape(data_dir, store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "k", "v")
    store.lt_clear()  # создать longterm.json
    raw = json.loads((data_dir / "dialogues.json").read_text(encoding="utf-8"))
    assert raw["active_id"] == d["id"]
    assert raw["dialogues"][0]["id"] == d["id"]
    assert raw["dialogues"][0]["messages"] == []
    raw_wm = json.loads((data_dir / "working.json").read_text(encoding="utf-8"))
    assert raw_wm == {d["id"]: {"k": "v"}}
    raw_lt = json.loads((data_dir / "longterm.json").read_text(encoding="utf-8"))
    assert raw_lt == {}


# ---------- build_memory_blocks ----------

def test_blocks_both_empty(store):
    d = store.new_dialogue()
    assert store.build_memory_blocks(d["id"]) == ""


def test_blocks_wm_only(store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "a", "1")
    store.wm_set(d["id"], "b", "2")
    assert store.build_memory_blocks(d["id"]) == "\n\nТекущая задача:\n- a: 1\n- b: 2"


def test_blocks_lt_only(store):
    d = store.new_dialogue()
    store.lt_set("x", "y")
    assert store.build_memory_blocks(d["id"]) == "\n\nДолговременная память:\n- x: y"


def test_blocks_wm_then_lt_order(store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "t", "задача")
    store.lt_set("u", "юзер")
    expected = ("\n\nТекущая задача:\n- t: задача"
                "\n\nДолговременная память:\n- u: юзер")
    assert store.build_memory_blocks(d["id"]) == expected


# ---------- layer_stats ----------

def test_layer_stats_full(store):
    d = store.new_dialogue()
    store.append_message(d["id"], "user", "abcd")          # 4 символа
    store.append_message(d["id"], "assistant", "efghij")   # 6 символов
    store.wm_set(d["id"], "k", "v")                        # 2 символа
    store.lt_set("lk", "lv")                               # 2 символа
    stats = store.layer_stats()
    assert stats["dialogue"] == {"message_count": 2, "tokens_est": math.ceil(10 / 4)}
    assert stats["working"] == {"entries": 1, "tokens_est": 1, "items": {"k": "v"}}
    assert stats["long_term"] == {"entries": 1, "tokens_est": 1, "items": {"lk": "lv"}}


def test_layer_stats_no_active_dialogue(store, data_dir):
    store.lt_set("k", "v")
    stats = store.layer_stats()
    assert stats["dialogue"] == {"message_count": 0, "tokens_est": 0}
    assert stats["working"] == {"entries": 0, "tokens_est": 0, "items": {}}
    assert stats["long_term"]["entries"] == 1


# ---------- тумблеры слоёв (toggles.json) ----------

def test_toggles_default_all_on(store):
    assert store.get_toggles() == {"st": True, "wm": True, "lt": True}


def test_toggles_set_and_persist(store, data_dir):
    store.set_toggle("wm", False)
    store.set_toggle("st", False)
    # любое сочетание: второй store видит то же самое
    s2 = MemoryStore(str(data_dir))
    assert s2.get_toggles() == {"st": False, "wm": False, "lt": True}


def test_toggles_set_back_to_true(store):
    store.set_toggle("lt", False)
    store.set_toggle("lt", True)
    assert store.get_toggles()["lt"] is True


def test_toggles_invalid_layer(store):
    with pytest.raises(ValueError):
        store.set_toggle("nope", True)
    with pytest.raises(ValueError):
        store.set_toggle("wm", "не bool")


def test_toggles_corrupted_file_defaults(data_dir):
    p = data_dir / "toggles.json"
    p.write_text("{битый json", encoding="utf-8")
    store = MemoryStore(str(data_dir))
    assert store.get_toggles() == {"st": True, "wm": True, "lt": True}


def test_blocks_respect_toggles(store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "t", "задача")
    store.lt_set("u", "юзер")
    store.set_toggle("wm", False)
    assert store.build_memory_blocks(d["id"]) == "\n\nДолговременная память:\n- u: юзер"
    store.set_toggle("lt", False)
    assert store.build_memory_blocks(d["id"]) == ""
    # обратно вкл — оба блока
    store.set_toggle("wm", True)
    store.set_toggle("lt", True)
    assert store.build_memory_blocks(d["id"]).startswith("\n\nТекущая задача:")


# ---------- профиль пользователя (день 12, per-диалог) ----------

class TestProfile:
    def setup_method(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.s = MemoryStore(self.d.name)

    def teardown_method(self):
        self.d.cleanup()

    def test_new_dialogue_has_pending_profile(self):
        d = self.s.new_dialogue()
        assert d["profile"] == new_profile()
        p = self.s.profile_get(d["id"])
        assert p["status"] == "pending"
        assert p["interview"] is False
        assert p["name"] == "" and p["role"] == "" and p["tone"] == "" and p["taboos"] == ""

    def test_profile_set_active_on_nonempty(self):
        d = self.s.new_dialogue()
        p = self.s.profile_set(d["id"], "Иван", "backend-разработчик, e-commerce",
                               "дружелюбно и по делу", "мат, политика")
        assert p["status"] == "active"
        assert self.s.profile_get(d["id"])["name"] == "Иван"
        s2 = MemoryStore(self.d.name)  # переживает рестарт
        assert s2.profile_get(d["id"])["status"] == "active"
        assert s2.profile_get(d["id"])["taboos"] == "мат, политика"

    def test_profile_set_all_empty_keeps_pending(self):
        d = self.s.new_dialogue()
        assert self.s.profile_set(d["id"], "", "", "", "")["status"] == "pending"

    def test_profile_set_rejects_non_str(self):
        d = self.s.new_dialogue()
        with pytest.raises(ValueError):
            self.s.profile_set(d["id"], 5, "", "", "")
        assert self.s.profile_get(d["id"])["status"] == "pending"

    def test_profile_set_unknown_dialogue(self):
        with pytest.raises(ValueError):
            self.s.profile_set("nope", "a", "", "", "")

    def test_profile_action_interview_keeps_pending_sets_flag(self):
        d = self.s.new_dialogue()
        p = self.s.profile_action(d["id"], "interview")
        assert p["status"] == "pending" and p["interview"] is True

    def test_profile_action_decline(self):
        d = self.s.new_dialogue()
        p = self.s.profile_action(d["id"], "decline")
        assert p["status"] == "declined" and p["interview"] is False

    def test_profile_action_reset_clears_fields(self):
        d = self.s.new_dialogue()
        self.s.profile_set(d["id"], "Иван", "роль", "тон", "табу")
        assert self.s.profile_action(d["id"], "reset") == new_profile()

    def test_profile_action_unknown(self):
        d = self.s.new_dialogue()
        with pytest.raises(ValueError):
            self.s.profile_action(d["id"], "bogus")

    def test_profile_isolated_per_dialogue(self):
        a = self.s.new_dialogue()["id"]
        b = self.s.new_dialogue()["id"]
        self.s.profile_set(a, "Иван", "", "", "")
        assert self.s.profile_get(b)["status"] == "pending"
        assert self.s.profile_get(b)["name"] == ""

    def test_profile_removed_with_dialogue(self):
        d = self.s.new_dialogue()
        self.s.profile_set(d["id"], "Иван", "", "", "")
        self.s.delete_dialogue(d["id"])
        with pytest.raises(ValueError):
            self.s.profile_get(d["id"])

    def test_old_dialogue_without_profile_field_is_pending(self):
        """Бэкворд-совместимость: запись диалога без поля profile."""
        d = self.s.new_dialogue()
        import os
        path = os.path.join(self.d.name, "dialogues.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for dd in data["dialogues"]:
            dd.pop("profile", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        assert self.s.profile_get(d["id"])["status"] == "pending"
        assert self.s.get_dialogue(d["id"])["profile"]["status"] == "pending"


class TestTaskStorage13b:
    def setup_method(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.s = MemoryStore(self.d.name)
        self.did = self.s.new_dialogue()["id"]

    def teardown_method(self):
        self.d.cleanup()

    # ---- бэкворд-совместимость ----

    def test_no_task_field_is_inactive(self):
        t = self.s.task_get(self.did)
        assert t["active"] is False
        assert t["task_id"] is None
        assert t["plan"] == []
        assert t["stage"] is None

    def test_old_schema_without_task_id_is_inactive(self):
        # запись дня 13 (без task_id, со stages) читается как неактивная
        with self.s._lock:
            data = self.s._read_dialogues()
            d = self.s._find(data, self.did)
            d["task"] = {"active": True, "stage": "execution", "paused": False,
                         "description": "old", "instruction": "",
                         "stages": {"planning": {"output": "x", "ts": "…", "verdict": None}},
                         "retries": 0, "error": None, "updated": None}
            self.s._write_dialogues(data)
        assert self.s.task_get(self.did)["active"] is False

    # ---- task_new ----

    def test_task_new_creates_planning(self):
        t = self.s.task_new(self.did, "Сделать план")
        assert t["active"] is True
        assert t["stage"] == "planning"
        assert t["current_step"] == 1 and t["total_steps"] == 5
        assert t["expected_action"] == "agent_response"
        assert t["task_id"].startswith("t_")
        assert [e["agent"] for e in t["plan"]] == ["planning", "execution", "validation", "done"]
        assert all(e["status"] == "pending" for e in t["plan"])
        assert t["work_steps"] == []
        assert t["context_snapshot"] is None

    def test_task_new_rejects_active(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_new(self.did, "б")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "уже активна" in str(e)

    # ---- usage / длительность (токены и время работы) ----

    def test_plan_and_work_steps_carry_usage_fields(self):
        self.s.task_new(self.did, "X")
        self.s.task_work_steps_set(self.did, ["A", "B"])
        t = self.s.task_get(self.did)
        assert all(e.get("usage") is None and e.get("duration_s") is None
                   for e in t["plan"])
        assert all(w.get("start_ts") is None and w.get("usage") is None
                   and w.get("duration_s") is None for w in t["work_steps"])

    def test_stage_done_stores_usage_and_duration(self):
        self.s.task_new(self.did, "X")
        self.s.task_spawn_stage(self.did, "planning")
        t = self.s.task_stage_done(
            self.did, "planning", "план",
            usage={"prompt": 1, "completion": 2, "total": 3})
        pe = t["plan"][0]
        assert pe["usage"] == {"prompt": 1, "completion": 2, "total": 3}
        assert isinstance(pe["duration_s"], int) and pe["duration_s"] >= 0

    def test_work_step_start_ts_usage_duration(self):
        self.s.task_new(self.did, "X")
        self.s.task_work_steps_set(self.did, ["A"])
        t1 = self.s.task_work_step_set(self.did, 0, "in_progress")
        assert t1["work_steps"][0]["start_ts"]
        t2 = self.s.task_work_step_set(
            self.did, 0, "completed", "результат",
            usage={"prompt": 4, "completion": 5, "total": 9})
        ws = t2["work_steps"][0]
        assert ws["usage"] == {"prompt": 4, "completion": 5, "total": 9}
        assert isinstance(ws["duration_s"], int) and ws["duration_s"] >= 0

    def test_append_message_task_usage_markers(self):
        self.s.append_message(
            self.did, "assistant", "вывод", model="m", task_id="t_1",
            task_stage="planning",
            task_usage={"prompt": 1, "completion": 2, "total": 3},
            task_duration=7)
        msg = self.s.get_messages(self.did)[0]
        assert msg["task_usage"] == {"prompt": 1, "completion": 2, "total": 3}
        assert msg["task_duration"] == 7
        # без параметров — маркеры не добавляются
        self.s.append_message(self.did, "user", "привет")
        assert "task_usage" not in self.s.get_messages(self.did)[1]

    # ---- used_task: персистентный флаг «задача использовалась» ----

    def _raw_dialogue(self, did):
        with self.s._lock:
            data = self.s._read_dialogues()
            return self.s._find(data, did)

    def test_task_new_sets_used_task_flag(self):
        self.s.task_new(self.did, "X")
        assert self._raw_dialogue(self.did).get("used_task") is True

    def test_used_task_survives_task_reset(self):
        self.s.task_new(self.did, "X")
        self.s.task_reset(self.did)
        assert self._raw_dialogue(self.did).get("used_task") is True
        # при этом сама задача сброшена
        assert self.s.task_get(self.did)["active"] is False

    def test_dialogue_without_task_has_no_used_task(self):
        assert "used_task" not in self._raw_dialogue(self.did)

    def test_list_dialogues_includes_used_task(self):
        d2 = self.s.new_dialogue()["id"]
        self.s.task_new(self.did, "X")
        lst = {x["id"]: x for x in self.s.list_dialogues()}
        assert lst[self.did]["used_task"] is True
        assert lst[d2]["used_task"] is False

    def test_used_task_backfill_legacy_record(self):
        """Legacy: задача создана до появления флага (в записи used_task
        нет, но есть task_id) — в list и get флаг True (бэклокфилл)."""
        with self.s._lock:
            data = self.s._read_dialogues()
            d = self.s._find(data, self.did)
            d["task"] = {"active": True, "task_id": "t_abc123",
                         "stage": "done", "current_step": 4,
                         "total_steps": 4, "expected_action": None,
                         "plan": [], "work_steps": [],
                         "context_snapshot": None, "description": "x",
                         "instruction": "", "retries": 0, "error": None,
                         "updated": None}
            self.s._write_dialogues(data)
        assert "used_task" not in self._raw_dialogue(self.did)  # флаг не записан
        lst = next(x for x in self.s.list_dialogues() if x["id"] == self.did)
        assert lst["used_task"] is True
        assert self.s.get_dialogue(self.did)["used_task"] is True

    def test_task_new_after_done_is_new_task(self):
        t1 = self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "план")
        self.s.task_approve(self.did)
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_stage_done(self.did, "execution", "работа")
        self.s.task_spawn_stage(self.did, "validation")
        self.s.task_stage_done(self.did, "validation", "ок", "pass")
        self.s.task_spawn_stage(self.did, "done")
        self.s.task_stage_done(self.did, "done", "итог")
        assert self.s.task_get(self.did)["stage"] == "done"
        t2 = self.s.task_new(self.did, "б")
        assert t2["task_id"] != t1["task_id"]
        assert t2["stage"] == "planning" and t2["description"] == "б"

    def test_task_new_after_failed_is_new_task(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_set_failed(self.did, "сбой")
        t2 = self.s.task_new(self.did, "б")
        assert t2["active"] is True and t2["stage"] == "planning"

    def test_task_new_unknown_dialogue(self):
        try:
            self.s.task_new("nope", "а")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "не найден" in str(e)

    # ---- spawn / stage_done ----

    def test_spawn_marks_in_progress(self):
        self.s.task_new(self.did, "а")
        t = self.s.task_spawn_stage(self.did, "planning")
        e = t["plan"][0]
        assert e["status"] == "in_progress" and e["spawn_ts"] is not None
        assert t["stage"] == "planning" and t["expected_action"] == "agent_response"

    def test_spawn_wrong_stage_rejected(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_spawn_stage(self.did, "validation")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "не совпадает" in str(e)

    def test_stage_done_advances(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        t = self.s.task_stage_done(self.did, "planning", "план")
        assert t["plan"][0]["status"] == "completed" and t["plan"][0]["output"] == "план"
        assert t["stage"] == "plan_review" and t["current_step"] == 2
        assert t["expected_action"] == "human_input"

    def test_stage_done_wrong_stage_rejected(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_stage_done(self.did, "execution", "х")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_stage_done_terminal(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        for st, out in (("execution", "р"), ("validation", "в")):
            self.s.task_spawn_stage(self.did, st)
            self.s.task_stage_done(self.did, st, out)
        self.s.task_spawn_stage(self.did, "done")
        t = self.s.task_stage_done(self.did, "done", "итог")
        assert t["stage"] == "done" and t["current_step"] == 5

    def test_verdict_stored_on_validation(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_stage_done(self.did, "execution", "р")
        self.s.task_spawn_stage(self.did, "validation")
        t = self.s.task_stage_done(self.did, "validation", "в", "fail")
        assert t["plan"][2]["verdict"] == "fail"

    # ---- work_steps ----

    def test_work_steps_set(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "[1,2]")
        t = self.s.task_work_steps_set(self.did, ["Шаг A", "Шаг B"])
        assert [w["name"] for w in t["work_steps"]] == ["Шаг A", "Шаг B"]
        assert all(w["status"] == "pending" for w in t["work_steps"])

    def test_work_step_progress(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        t = self.s.task_work_step_set(self.did, 0, "in_progress")
        assert t["work_steps"][0]["status"] == "in_progress"
        t = self.s.task_work_step_set(self.did, 0, "completed", "выполнено")
        assert t["work_steps"][0]["output"] == "выполнено"
        assert t["work_steps"][0]["ts"] is not None

    def test_work_step_bad_index(self):
        self.s.task_new(self.did, "а")
        try:
            self.s.task_work_step_set(self.did, 3, "in_progress")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    # ---- retry ----

    def test_retry_execution_resets_steps(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_work_step_set(self.did, 0, "completed", "A-ок")
        self.s.task_stage_done(self.did, "execution", "раб")
        self.s.task_spawn_stage(self.did, "validation")
        t = self.s.task_retry_execution(self.did, "плохо", "fail")
        assert t["stage"] == "execution" and t["retries"] == 1
        assert t["plan"][2]["verdict"] == "fail"
        assert all(w["status"] == "pending" for w in t["work_steps"])
        assert t["plan"][1]["status"] == "pending"

    def test_retry_rejected_outside_validation(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_spawn_stage(self.did, "execution")
        try:
            self.s.task_retry_execution(self.did, "x", "fail")
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    # ---- pause / resume / failed ----

    def test_pause_sets_stage_and_snapshot(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_work_steps_set(self.did, ["A", "B"])
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_work_step_set(self.did, 0, "completed", "A-ок")
        t = self.s.task_pause(self.did)
        assert t["stage"] == "paused" and t["expected_action"] == "resume_wait"
        assert t["context_snapshot"]["description"] == "а"
        assert len(t["context_snapshot"]["work_steps"]) == 2

    def test_pause_rejected_on_done_and_failed(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_set_failed(self.did, "сбой")
        try:
            self.s.task_pause(self.did)
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_resume_restores_first_incomplete(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_spawn_stage(self.did, "execution")
        self.s.task_pause(self.did)
        t = self.s.task_resume(self.did)
        assert t["stage"] == "execution" and t["expected_action"] == "agent_response"

    def test_resume_without_pause_rejected(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_resume(self.did)
            assert False, "ожидался ValueError"
        except ValueError:
            pass

    def test_failed_and_retry_via_resume(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        self.s.task_stage_done(self.did, "planning", "п")
        self.s.task_approve(self.did)
        self.s.task_spawn_stage(self.did, "execution")
        t = self.s.task_set_failed(self.did, "модель не ответила")
        assert t["stage"] == "failed" and t["error"] == "модель не ответила"
        assert t["expected_action"] == "resume_wait"
        t = self.s.task_resume(self.did)
        assert t["stage"] == "execution" and t["error"] is None

    def test_instruction_only_on_pause(self):
        self.s.task_new(self.did, "а")
        self.s.task_spawn_stage(self.did, "planning")
        try:
            self.s.task_set_instruction(self.did, "текст")
            assert False, "ожидался ValueError"
        except ValueError as e:
            assert "только на паузе" in str(e)
        self.s.task_pause(self.did)
        t = self.s.task_set_instruction(self.did, "текст")
        assert t["instruction"] == "текст" and t["expected_action"] == "human_input"
        assert self.s.task_instruction_take(self.did) == "текст"
        assert self.s.task_get(self.did)["instruction"] == ""

    # ---- reset / выдача ----

    def test_reset(self):
        self.s.task_new(self.did, "а")
        t = self.s.task_reset(self.did)
        assert t["active"] is False and t["task_id"] is None

    def test_task_in_dialogue_outputs(self):
        self.s.task_new(self.did, "а")
        assert self.s.list_dialogues()[0]["task"]["active"] is True
        assert self.s.get_dialogue(self.did)["task"]["active"] is True

    # ---- маркеры сообщений ----

    def test_append_message_task_markers(self):
        m = self.s
        m.append_message(self.did, "user", "запрос", task_id="t_1")
        m.append_message(self.did, "assistant", "план", model="m", task_id="t_1", task_stage="planning")
        m.append_message(self.did, "assistant", "шаг", model="m", task_id="t_1",
                          task_stage="execution", task_step="Шаг A")
        msgs = m.get_messages(self.did)
        assert msgs[0]["task_id"] == "t_1" and "task_stage" not in msgs[0]
        assert msgs[1]["task_stage"] == "planning" and "task_step" not in msgs[1]
        assert msgs[2]["task_step"] == "Шаг A"


# ---------- инварианты (день 14, глобальные; схема {id, title, description,
#            forbidden[], is_active}) ----------

def test_invariants_set_and_items(store):
    r1 = store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    r2 = store.invariants_set("Архитектура", "монолит")
    assert r1["id"].startswith("inv_") and r2["id"].startswith("inv_")
    assert r1["id"] != r2["id"]
    assert store.invariants_items() == {
        r1["id"]: {"title": "Стек", "description": "Kotlin",
                   "forbidden": ["python"], "is_active": True},
        r2["id"]: {"title": "Архитектура", "description": "монолит",
                   "forbidden": [], "is_active": True}}


def test_invariants_update_by_title_keeps_id(store):
    r1 = store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    r2 = store.invariants_set("Стек", "Java", forbidden=["python", "go"],
                              is_active=False)
    assert r2["id"] == r1["id"]
    assert store.invariants_items() == {
        r1["id"]: {"title": "Стек", "description": "Java",
                   "forbidden": ["python", "go"], "is_active": False}}


def test_invariants_forbidden_strips_empty_and_none_is_empty_list(store):
    r1 = store.invariants_set("Стек", "Kotlin",
                              forbidden=[" python ", "", "   ", "go"])
    assert r1["forbidden"] == ["python", "go"]
    r2 = store.invariants_set("Архитектура", "монолит")
    assert r2["forbidden"] == []
    assert store.invariants_items()[r1["id"]]["forbidden"] == ["python", "go"]


def test_invariants_set_active_toggle(store):
    r1 = store.invariants_set("Стек", "Kotlin")
    rec = store.invariants_set_active(r1["id"], False)
    assert rec == {"id": r1["id"], "title": "Стек", "description": "Kotlin",
                   "forbidden": [], "is_active": False}
    assert store.invariants_set_active(r1["id"], True)["is_active"] is True
    assert store.invariants_set_active("nope", True) is None
    with pytest.raises(ValueError):
        store.invariants_set_active(r1["id"], "yes")
    with pytest.raises(ValueError):
        store.invariants_set_active(r1["id"], 1)


def test_invariants_remove_and_clear(store):
    r1 = store.invariants_set("t1", "d1")
    r2 = store.invariants_set("t2", "d2")
    assert store.invariants_remove(r1["id"]) is True
    assert store.invariants_remove(r1["id"]) is False
    assert store.invariants_remove("nope") is False
    assert set(store.invariants_items()) == {r2["id"]}
    store.invariants_clear()
    assert store.invariants_items() == {}


def test_invariants_missing_and_broken_file(data_dir):
    # отсутствующий файл -> {}
    assert MemoryStore(str(data_dir)).invariants_items() == {}
    # битый файл -> {}, мутации работают
    (data_dir / "invariants.json").write_text("{битый json", encoding="utf-8")
    s = MemoryStore(str(data_dir))
    assert s.invariants_items() == {}
    s.invariants_set("t", "d")
    assert len(s.invariants_items()) == 1
    # частично битый файл: записи без непустого str title/description
    # отбрасываются
    (data_dir / "invariants.json").write_text(
        json.dumps({"inv_1": {"title": "К", "description": "В"},
                    "inv_2": {"title": "", "description": "В"},
                    "inv_3": {"title": "К", "description": None},
                    "inv_4": "мусор"}), encoding="utf-8")
    s2 = MemoryStore(str(data_dir))
    assert s2.invariants_items() == {
        "inv_1": {"title": "К", "description": "В",
                  "forbidden": [], "is_active": True}}


def test_invariants_backcompat_migration_from_key_value(data_dir):
    """Legacy-схема {key, value} мигрируется при чтении в новую схему."""
    (data_dir / "invariants.json").write_text(
        json.dumps({"inv_a": {"key": "Стек", "value": "Kotlin"},
                    "inv_b": {"key": 5, "value": "x"},
                    "inv_c": "мусор"}), encoding="utf-8")
    s = MemoryStore(str(data_dir))
    assert s.invariants_items() == {
        "inv_a": {"title": "Стек", "description": "Kotlin",
                  "forbidden": [], "is_active": True}}


def test_invariants_is_active_string_values(data_dir):
    """Строковые is_active ("false"/"0") трактуются как неактивные (баг №1)."""
    (data_dir / "invariants.json").write_text(
        json.dumps({"inv_a": {"title": "Стек", "description": "Kotlin",
                              "is_active": "false"},
                    "inv_b": {"title": "Арх", "description": "монолит",
                              "is_active": "0"},
                    "inv_c": {"title": "Тон", "description": "кратко",
                              "is_active": "true"},
                    "inv_d": {"title": "Темп", "description": "быстро",
                              "is_active": 1},
                    "inv_e": {"title": "Цвет", "description": "тёмный",
                              "is_active": False},
                    "inv_f": {"title": "Риск", "description": "низкий"}}),
        encoding="utf-8")
    s = MemoryStore(str(data_dir))
    items = s.invariants_items()
    assert items["inv_a"]["is_active"] is False  # строка "false"
    assert items["inv_b"]["is_active"] is False  # строка "0"
    assert items["inv_c"]["is_active"] is True   # строка "true"
    assert items["inv_d"]["is_active"] is True   # число 1
    assert items["inv_e"]["is_active"] is False  # bool False
    assert items["inv_f"]["is_active"] is True   # отсутствие -> default True
    # неактивные исключены из готового блока
    assert "Kotlin" not in s.build_invariants_block()
    assert "монолит" not in s.build_invariants_block()


def test_invariants_set_rejects_empty_and_non_str(store):
    with pytest.raises(ValueError):
        store.invariants_set("", "v")
    with pytest.raises(ValueError):
        store.invariants_set("t", "   ")
    with pytest.raises(ValueError):
        store.invariants_set(5, "v")
    with pytest.raises(ValueError):
        store.invariants_set("t", None)
    with pytest.raises(ValueError):
        store.invariants_set("t", "v", forbidden="python")
    with pytest.raises(ValueError):
        store.invariants_set("t", "v", forbidden=["p", 5])
    assert store.invariants_items() == {}


def test_invariants_block_empty(store):
    assert store.build_invariants_block() == ""


def test_invariants_block_nonempty_and_excludes_inactive(store):
    a = store.invariants_set("Стек", "Kotlin")
    b = store.invariants_set("Тесты", "обязательны")
    assert store.build_invariants_block() == (
        "\n\nИнварианты (неукоснительно):\n- Стек: Kotlin\n- Тесты: обязательны")
    # неактивные инварианты в блок не попадают
    store.invariants_set_active(b["id"], False)
    assert store.build_invariants_block() == (
        "\n\nИнварианты (неукоснительно):\n- Стек: Kotlin")
    store.invariants_set_active(a["id"], False)
    assert store.build_invariants_block() == ""


def test_invariants_isolated_from_wm_lt(store):
    d = store.new_dialogue()
    store.wm_set(d["id"], "Стек", "Python")
    store.lt_set("Стек", "Go")
    store.invariants_set("Стек", "Kotlin")
    assert store.wm_items(d["id"]) == {"Стек": "Python"}
    assert store.lt_items() == {"Стек": "Go"}
    assert len(store.invariants_items()) == 1


def test_invariants_persistence_across_instances(data_dir):
    s1 = MemoryStore(str(data_dir))
    r = s1.invariants_set("Стек", "Kotlin", forbidden=["python"],
                          is_active=False)
    s2 = MemoryStore(str(data_dir))
    assert s2.invariants_items() == {
        r["id"]: {"title": "Стек", "description": "Kotlin",
                  "forbidden": ["python"], "is_active": False}}


def test_invariants_not_affected_by_toggles(store):
    """Инварианты глобальны: тумблеры слоёв памяти на них не действуют."""
    store.invariants_set("Стек", "Kotlin")
    store.set_toggle("wm", False)
    store.set_toggle("lt", False)
    assert store.build_invariants_block() == (
        "\n\nИнварианты (неукоснительно):\n- Стек: Kotlin")


def test_layer_stats_invariants(store):
    store.new_dialogue()
    stats = store.layer_stats()
    assert stats["invariants"] == {"entries": 0, "tokens_est": 0, "items": {}}
    store.invariants_set("Стек", "Kotlin")  # title+description = 10 символов
    inv = store.invariants_items()
    iid = next(iter(inv))
    stats = store.layer_stats()
    assert stats["invariants"]["entries"] == 1
    assert stats["invariants"]["tokens_est"] == math.ceil(10 / 4)
    assert stats["invariants"]["items"] == {
        iid: {"title": "Стек", "description": "Kotlin",
              "forbidden": [], "is_active": True}}

# ---------- реестр MCP-серверов (день 16) ----------

def test_mcp_servers_missing_and_broken_file(data_dir):
    assert MemoryStore(str(data_dir)).mcp_servers_items() == {}
    (data_dir / "mcp_servers.json").write_text("{битый json", encoding="utf-8")
    assert MemoryStore(str(data_dir)).mcp_servers_items() == {}


def test_mcp_servers_crud(store):
    rec = store.mcp_servers_set("mcp_a1", "Context7", "stdio",
                                command=["npx", "-y", "@upstash/context7-mcp"])
    assert rec == {"id": "mcp_a1", "name": "Context7", "type": "stdio",
                   "command": ["npx", "-y", "@upstash/context7-mcp"],
                   "url": "", "env": {}, "enabled": True}
    assert store.mcp_servers_items()["mcp_a1"]["name"] == "Context7"
    # обновление по id сохраняет id
    rec2 = store.mcp_servers_set("mcp_a1", "Context7", "stdio", command=["npx"])
    assert rec2["id"] == "mcp_a1" and rec2["command"] == ["npx"]
    # http-сервер: command пуст, url задан
    rec3 = store.mcp_servers_set("mcp_b2", "Yandex", "http",
                                 url="https://example.com/mcp")
    assert rec3 == {"id": "mcp_b2", "name": "Yandex", "type": "http",
                    "command": [], "url": "https://example.com/mcp",
                    "env": {}, "enabled": True}
    assert store.mcp_servers_remove("mcp_a1") is True
    assert store.mcp_servers_remove("mcp_a1") is False
    store.mcp_servers_clear()
    assert store.mcp_servers_items() == {}


def test_mcp_servers_persistence_across_instances(data_dir):
    s1 = MemoryStore(str(data_dir))
    s1.mcp_servers_set("mcp_a1", "Git", "stdio", command=["npx"])
    s2 = MemoryStore(str(data_dir))
    assert s2.mcp_servers_items()["mcp_a1"]["name"] == "Git"


def test_mcp_servers_validation(store):
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "", "stdio", command=["npx"])
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "tcp", command=["npx"])
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "stdio", command=[])
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "stdio", command=["npx", 42])
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "http", url="ftp://x")
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "http")
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "stdio", command=["npx"],
                              env={"K": "V", "J": 1})
    with pytest.raises(ValueError):
        store.mcp_servers_set("mcp_x", "S", "stdio", command=["npx"],
                              enabled="yes")


def test_mcp_servers_isolation_from_memory(store):
    store.mcp_servers_set("mcp_a1", "Git", "stdio", command=["npx"])
    store.lt_set("k", "v")
    d = store.new_dialogue()
    store.wm_set(d["id"], "w", "x")
    stats = store.layer_stats()
    assert "mcp" not in stats
    assert store.lt_items() == {"k": "v"}
