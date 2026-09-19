"""Тесты MemoryStore: ST/диалоги, WM, LT, build_memory_blocks, layer_stats."""
import json
import math

import pytest

from memory import MemoryStore


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
    assert set(d) == {"id", "title", "created", "messages"}
    assert d["messages"] == []
    assert store.active_id() == d["id"]


def test_list_dialogues_order_and_shape(store):
    a = store.new_dialogue()
    b = store.new_dialogue()
    store.append_message(a["id"], "user", "привет")
    lst = store.list_dialogues()
    assert [x["id"] for x in lst] == [a["id"], b["id"]]  # порядок создания
    assert lst[0] == {"id": a["id"], "title": a["title"],
                      "created": a["created"], "message_count": 1}
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
