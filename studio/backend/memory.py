"""Хранилище памяти приложения «Студия».

Три слоя:
- ST (короткая память) — диалоги и их сообщения: `dialogues.json`;
- WM (оперативная/рабочая память) — заметки по каждому диалогу: `working.json`;
- LT (долговременная память) — глобальные заметки: `longterm.json`.

Все мутации выполняются под единым threading.Lock, запись атомарная
(tempfile.mkstemp + os.replace), чтение устойчиво к битым/отсутствующим файлам.
"""
import json
import math
import os
import tempfile
import threading
import uuid
from datetime import datetime


def atomic_write_json(path: str, obj) -> None:
    """Атомарно записать JSON-объект в файл (ensure_ascii=False, indent=2)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path: str, default):
    """Прочитать JSON; при OSError/ValueError (битый файл) вернуть default."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _now() -> str:
    """Текущее время в формате 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tok_est(text: str) -> int:
    """Оценка токенов: ceil(сумма символов / 4)."""
    return math.ceil(len(text) / 4)


class MemoryStore:
    """Хранилище трёх слоёв памяти с файловым персистентным бэкендом."""

    # Слои памяти, которые можно включать/отключать (порядок не важен —
    # любое сочетание работает независимо).
    TOGGLE_LAYERS = ("st", "wm", "lt")

    def __init__(self, data_dir: str):
        """Создаёт store поверх каталога data_dir (каталог создаётся при первой записи)."""
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._p_dialogues = os.path.join(data_dir, "dialogues.json")
        self._p_working = os.path.join(data_dir, "working.json")
        self._p_longterm = os.path.join(data_dir, "longterm.json")
        self._p_toggles = os.path.join(data_dir, "toggles.json")

    # ---------- внутреннее чтение/запись ----------

    def _read_dialogues(self) -> dict:
        """Текущее состояние dialogues.json; битый файл -> дефолт."""
        d = read_json(self._p_dialogues, None)
        if not isinstance(d, dict):
            d = {}
        if not isinstance(d.get("dialogues"), list):
            d["dialogues"] = []
        if "active_id" not in d:
            d["active_id"] = None
        return d

    def _write_dialogues(self, d: dict) -> None:
        atomic_write_json(self._p_dialogues, d)

    def _read_working(self) -> dict:
        w = read_json(self._p_working, None)
        return w if isinstance(w, dict) else {}

    def _write_working(self, w: dict) -> None:
        atomic_write_json(self._p_working, w)

    def _read_longterm(self) -> dict:
        l = read_json(self._p_longterm, None)
        return l if isinstance(l, dict) else {}

    def _write_longterm(self, l: dict) -> None:
        atomic_write_json(self._p_longterm, l)

    def _find(self, data: dict, dialogue_id: str) -> dict | None:
        """Найти диалог по id в структуре dialogues.json."""
        for d in data["dialogues"]:
            if d.get("id") == dialogue_id:
                return d
        return None

    # ---------- ST / диалоги ----------

    def new_dialogue(self) -> dict:
        """Создать новый диалог (автоматически становится активным) и вернуть его."""
        with self._lock:
            data = self._read_dialogues()
            d = {"id": uuid.uuid4().hex, "title": "Новый диалог",
                 "created": _now(), "messages": []}
            data["dialogues"].append(d)
            data["active_id"] = d["id"]
            self._write_dialogues(data)
            return d

    def list_dialogues(self) -> list:
        """Список диалогов в порядке создания: {id,title,created,message_count}."""
        with self._lock:
            data = self._read_dialogues()
            return [{"id": d["id"], "title": d.get("title", ""),
                     "created": d.get("created", ""),
                     "message_count": len(d.get("messages", []))}
                    for d in data["dialogues"]]

    def get_dialogue(self, dialogue_id: str) -> dict | None:
        """Полный диалог с сообщениями или None."""
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                return None
            return {"id": d["id"], "title": d.get("title", ""),
                    "created": d.get("created", ""),
                    "messages": list(d.get("messages", []))}

    def get_messages(self, dialogue_id: str) -> list:
        """Сообщения диалога [{role,content}] ([] если диалог не найден)."""
        d = self.get_dialogue(dialogue_id)
        return d["messages"] if d else []

    def active_id(self) -> str | None:
        """Id активного диалога или None."""
        with self._lock:
            return self._read_dialogues()["active_id"]

    def activate(self, dialogue_id: str) -> None:
        """Сделать диалог активным; ValueError, если диалог не существует."""
        with self._lock:
            data = self._read_dialogues()
            if self._find(data, dialogue_id) is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            data["active_id"] = dialogue_id
            self._write_dialogues(data)

    def delete_dialogue(self, dialogue_id: str) -> None:
        """Удалить диалог и его WM; если был активным — активным
        становится последний оставшийся диалог (или None)."""
        with self._lock:
            data = self._read_dialogues()
            data["dialogues"] = [d for d in data["dialogues"] if d.get("id") != dialogue_id]
            if data["active_id"] == dialogue_id:
                data["active_id"] = data["dialogues"][-1]["id"] if data["dialogues"] else None
            self._write_dialogues(data)
            w = self._read_working()
            w.pop(dialogue_id, None)
            self._write_working(w)

    def rename_dialogue(self, dialogue_id: str, title: str) -> None:
        """Сменить заголовок диалога; ValueError, если диалог не существует
        или title пуст (после strip)."""
        with self._lock:
            if not isinstance(title, str) or not title.strip():
                raise ValueError("Заголовок не может быть пустым")
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            d["title"] = title.strip()
            self._write_dialogues(data)

    def append_message(self, dialogue_id: str, role: str, content: str,
                       model: str | None = None) -> None:
        """Добавить сообщение в диалог; ValueError, если диалог не существует.

        model — необязательная метка (используется для assistant-сообщений:
        какой моделью выполнен запрос).
        """
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            msg = {"role": role, "content": content}
            if model is not None:
                msg["model"] = model
            d.setdefault("messages", []).append(msg)
            self._write_dialogues(data)

    def clear_st(self, dialogue_id: str) -> None:
        """Очистить сообщения диалога (сам диалог остаётся)."""
        with self._lock:
            data = self._read_dialogues()
            d = self._find(data, dialogue_id)
            if d is None:
                return
            d["messages"] = []
            self._write_dialogues(data)

    # ---------- WM (рабочая память, per-dialogue) ----------

    def wm_set(self, dialogue_id: str, key: str, value: str) -> None:
        """Поставить заметку в WM диалога; ValueError, если диалог не существует."""
        with self._lock:
            data = self._read_dialogues()
            if self._find(data, dialogue_id) is None:
                raise ValueError(f"Диалог «{dialogue_id}» не найден")
            w = self._read_working()
            w.setdefault(dialogue_id, {})[key] = value
            self._write_working(w)

    def wm_items(self, dialogue_id: str) -> dict:
        """Заметки WM диалога {key: value} ({} если нет)."""
        with self._lock:
            w = self._read_working()
            v = w.get(dialogue_id)
            return v if isinstance(v, dict) else {}

    def wm_remove(self, dialogue_id: str, key: str) -> bool:
        """Удалить заметку из WM диалога; True если ключ был."""
        with self._lock:
            w = self._read_working()
            items = w.get(dialogue_id)
            if isinstance(items, dict) and key in items:
                del items[key]
                self._write_working(w)
                return True
            return False

    def wm_clear(self, dialogue_id: str) -> None:
        """Очистить WM диалога."""
        with self._lock:
            w = self._read_working()
            if dialogue_id in w:
                w[dialogue_id] = {}
                self._write_working(w)

    # ---------- LT (долговременная память, глобальная) ----------

    def lt_set(self, key: str, value: str) -> None:
        """Поставить/обновить глобальную заметку."""
        with self._lock:
            l = self._read_longterm()
            l[key] = value
            self._write_longterm(l)

    def lt_items(self) -> dict:
        """Все глобальные заметки {key: value}."""
        with self._lock:
            return self._read_longterm()

    def lt_remove(self, key: str) -> bool:
        """Удалить глобальную заметку; True если ключ был."""
        with self._lock:
            l = self._read_longterm()
            if key in l:
                del l[key]
                self._write_longterm(l)
                return True
            return False

    def lt_clear(self) -> None:
        """Очистить все глобальные заметки."""
        with self._lock:
            self._write_longterm({})

    # ---------- тумблеры слоёв памяти (toggles.json) ----------

    def _read_toggles(self) -> dict:
        """Тумблеры {st,wm,lt: bool}; битый/отсутствующий файл — всё включено."""
        t = read_json(self._p_toggles, None)
        if not isinstance(t, dict):
            t = {}
        return {layer: bool(t.get(layer, True)) for layer in self.TOGGLE_LAYERS}

    def get_toggles(self) -> dict:
        """Текущие тумблеры слоёв (все True по умолчанию)."""
        with self._lock:
            return self._read_toggles()

    def set_toggle(self, layer: str, enabled: bool) -> dict:
        """Включить/отключить слой; ValueError на неизвестный слой или не-bool.
        Возвращает актуальные тумблеры."""
        if layer not in self.TOGGLE_LAYERS:
            raise ValueError(f"Неизвестный слой памяти: {layer}")
        if not isinstance(enabled, bool):
            raise ValueError("Флаг должен быть bool (true/false)")
        with self._lock:
            t = self._read_toggles()
            t[layer] = enabled
            atomic_write_json(self._p_toggles, t)
            return t

    # ---------- сборка блока памяти и статистика ----------

    def build_memory_blocks(self, dialogue_id: str) -> str:
        """Текст блоков памяти для системного промпта.

        Пустые и ОТКЛЮЧЁННЫЕ (тумблеры) слои пропускаются; порядок WM → LT:
        «\\n\\nТекущая задача:\\n- key: value» и/или
        «\\n\\nДолговременная память:\\n- key: value».
        """
        with self._lock:
            t = self._read_toggles()
            w = self._read_working()
            wm = w.get(dialogue_id)
            wm = wm if isinstance(wm, dict) else {}
            lt = self._read_longterm()
        parts = []
        if t["wm"] and wm:
            parts.append("\n\nТекущая задача:\n" + "\n".join(f"- {k}: {v}" for k, v in wm.items()))
        if t["lt"] and lt:
            parts.append("\n\nДолговременная память:\n" + "\n".join(f"- {k}: {v}" for k, v in lt.items()))
        return "".join(parts)

    def layer_stats(self) -> dict:
        """Статистика слоёв по АКТИВНОМУ диалогу (нет активного — нули по ST/WM).

        tokens_est = ceil(сумма символов / 4).
        """
        with self._lock:
            data = self._read_dialogues()
            active = data["active_id"]
            d = self._find(data, active) if active else None
            w = self._read_working()
            wm_raw = w.get(active) if active else None
            wm = wm_raw if isinstance(wm_raw, dict) else {}
            lt = self._read_longterm()
        msgs = d.get("messages", []) if d else []
        return {
            "dialogue": {"message_count": len(msgs),
                         "tokens_est": _tok_est("".join(m.get("content", "") for m in msgs))},
            "working": {"entries": len(wm),
                        "tokens_est": _tok_est("".join(k + v for k, v in wm.items())),
                        "items": wm},
            "long_term": {"entries": len(lt),
                          "tokens_est": _tok_est("".join(k + v for k, v in lt.items())),
                          "items": lt},
        }
