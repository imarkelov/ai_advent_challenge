"""Агент приложения «Студия».

Собирает запрос к LLM (системный промпт + блоки памяти + история диалога),
стримит ответ по SSE, ведёт журнал запросов (requests.json) и считает
сессионные токены (in-memory, обнуляются при рестарте).
"""
import json
import os
import threading
import time
from datetime import datetime

import httpx

try:  # пакетный режим: studio.backend.agent
    from .memory import MemoryStore, atomic_write_json, read_json
except ImportError:  # dev-режим: импорт из studio/backend
    from memory import MemoryStore, atomic_write_json, read_json

# Ограничения контекста известных моделей; неизвестной — 32768.
CONTEXT_LIMITS = {
    "qwen3.8-27b": 32768,
    "deepseek-v4-flash": 16384,
    "glm-5.3-flash": 16384,
}
DEFAULT_CONTEXT_LIMIT = 32768

# GPustack выдаёт ключу доступ только к «своей» модели, поэтому у моделей —
# отдельные ключи. Модель -> переменная окружения с ключом; неизвестной —
# DEFAULT_KEY_ENV. (Паттерн из agent.py дней 5–11.)
DEFAULT_KEY_ENV = "GPUSTACK_API_KEY"
MODEL_KEY_ENV = {
    "qwen3.8-27b": "GPUSTACK_API_KEY",
    "deepseek-v4-flash": "GPUSTACK_KEY_DEEPSEEK",
    "glm-5.3-flash": "GPUSTACK_KEY_GLM",
}

DEFAULT_CONFIG = {
    "model": "qwen3.8-27b",
    "temperature": 0.7,
    "max_tokens": 2048,
    "system_prompt": ("Ты — ассистент в обучающем приложении «Студия». "
                      "Отвечай по делу и кратко; если пользователь пишет по-русски — "
                      "отвечай на русском."),
}

# Правило разрешения конфликтов «запрос ↔ память»: память — устойчивые
# ограничения с приоритетом над запросами диалога (включая последние), тихое
# подчинение противоречащему запросу запрещено. Перед ответом запрос сверяется
# с каждым пунктом памяти; при противоречии агент вежливо отказывает (с
# лёгким юмором) и сообщает, что принято решение действовать по памяти; сам
# пункт не правит — памятью управляет только пользователь. Добавляется в
# system-сообщение только когда есть записи памяти.
MEMORY_RULE = (
    "\n\nПравило памяти: пункты памяти — устойчивые ограничения пользователя; "
    "у них приоритет над любыми запросами в диалоге, включая последние "
    "сообщения. Перед ответом сверь запрос с каждым пунктом памяти: если "
    "запрос противоречит пункту (просит другое значение для того же предмета, "
    "даже если тема уже обсуждалась в диалоге) — не подчиняйся ему молча: "
    "вежливо откажись выполнить такой запрос, добавь лёгкую доброжелательную "
    "шутку, сошлись на конкретный пункт памяти и сообщи, что принято решение "
    "действовать по памяти. Сам пункт памяти не изменяй и не удаляй — памятью "
    "управляет только пользователь. Даже если пользователь повторяет "
    "противоречащий запрос — откажись снова. Пример: пункт памяти «Стек: "
    "Kotlin», запрос «напиши код на Python» → ты: «Не могу: по пункту "
    "памяти стек — Kotlin, память важнее запроса. Если нужно изменить — "
    "поменяй пункт памяти сам». Если противоречия нет — отвечай как обычно, "
    "учитывая пункты памяти."
)

JOURNAL_CAP = 100  # максимум записей в журнале (FIFO)

# Глаголы действующих запросов для server-side гарда «запрос ↔ память»:
# без глабола гард не срабатывает — вопросы про память («какой источник?»)
# конфликтом не считаются.
CONFLICT_VERBS = (
    "напиши", "сделай", "создай", "сгенерируй", "сформируй", "подготовь",
    "измени", "поменяй", "переделай", "перепиши", "замени", "смени",
    "используй", "включи", "добавь", "составь", "разработай", "оформи",
)

# Заголовок нового диалога; равен ему → авто-название по первому сообщению.
DEFAULT_DIALOGUE_TITLE = "Новый диалог"

# Профиль пользователя (день 12): тексты служебного потока инициализации.
# Детерминированные — LLM в них не участвует (мелкие модели не должны
# управлять служебным потоком).
PROFILE_INVITE_TEXT = (
    "Перед началом работы нужно инициализировать профиль пользователя.\n"
    "Выберите один из вариантов:\n"
    "1) заполнить вручную — откройте вкладку «Профили» в панели «Контекст» "
    "справа и сохраните 4 поля (имя, роль и сфера, тон и стиль, "
    "стоп-слова/табу);\n"
    "2) провести интервью — напишите «интервью», я задам 4 вопроса одним "
    "сообщением;\n"
    "3) отказаться — напишите «отказ»; запросы будут выполняться как обычно, "
    "но без учёта ваших предпочтений.")

PROFILE_INTERVIEW_TEXT = (
    "Давайте интервью для профиля. Ответьте, пожалуйста, одним сообщением "
    "на 4 вопроса:\n"
    "1) Как вас зовут (имя пользователя);\n"
    "2) Ваша профессиональная роль и сфера;\n"
    "3) Какой тон и стиль общения вам подходит;\n"
    "4) Стоп-слова/табу — слова или темы, которых нужно избегать.")

PROFILE_MANUAL_TEXT = (
    "Хорошо, заполните профиль вручную: откройте панель «Контекст» справа, "
    "вкладка «Профили», заполните 4 поля и нажмите «Сохранить». После "
    "сохранения профиль будет применяться к каждому запросу автоматически.")

PROFILE_DECLINED_TEXT = (
    "Хорошо, профиль не заполняем. Запросы буду выполнять как обычно; "
    "подсказка о инициализации останется в интерфейсе — вернуться к ней "
    "можно в любой момент.")

PROFILE_INTERVIEW_MARKERS = ("интерв",)
PROFILE_MANUAL_MARKERS = ("вручную",)
PROFILE_DECLINE_MARKERS = ("отказ", "не надо", "не нужно", "не буду",
                           "не заполня", "не хоч")

# TTL кэша доступности моделей (сек). Зонд — минимальный запрос max_tokens=1;
# GPustack отдаёт 403 «Api key not allowed», если ключу модель не доступна.
MODEL_PROBE_TTL = 600


def _now() -> str:
    """Текущее время в формате 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class StudioAgent:
    """Агент-обёртка над LLM API (OpenAI-совместимое) с памятью и журналом."""

    def __init__(self, data_dir: str, base_url: str = None, api_key: str = None,
                 client=None, env=None):
        """Создаёт агента.

        data_dir — каталог данных (MemoryStore + config.json + requests.json);
        base_url/api_key — по умолчанию из окружения GPUSTACK_BASE_URL/GPUSTACK_API_KEY;
        client — httpx.Client (в тестах — с MockTransport);
        env — словарь окружения для per-model ключей (тесты), по умолчанию os.environ.
        """
        self.store = MemoryStore(data_dir)
        self.base_url = (base_url or os.environ.get("GPUSTACK_BASE_URL",
                        "https://gpustack.data.lmru.tech/v1")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("GPUSTACK_API_KEY", "")
        self._env = os.environ if env is None else env
        self._client = client or httpx.Client(timeout=120)
        self._lock = threading.Lock()  # только для журнала requests.json
        self._session = {"prompt": 0, "completion": 0, "total": 0}  # in-memory
        self._last_usage = None
        self._p_config = os.path.join(data_dir, "config.json")
        self._p_requests = os.path.join(data_dir, "requests.json")
        self._models_cache = None      # кэш доступных моделей (list_models)
        self._models_cache_ts = 0.0

    # ---------- конфиг ----------

    def get_config(self) -> dict:
        """Текущий конфиг (дефолты + сохранённое; битый файл -> дефолты)."""
        c = read_json(self._p_config, None)
        cfg = dict(DEFAULT_CONFIG)
        if isinstance(c, dict):
            for k in DEFAULT_CONFIG:
                if k in c:
                    cfg[k] = c[k]
        return cfg

    def set_config(self, updates: dict) -> dict:
        """Частично обновить конфиг и вернуть актуальный.

        Неизвестный ключ, неверный тип или значение вне диапазона ->
        ValueError (на русском), конфиг не меняется.
        """
        if not isinstance(updates, dict):
            raise ValueError("Конфиг должен быть объектом (dict)")
        cfg = self.get_config()
        for key, value in updates.items():
            if key == "model":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("Поле model должно быть непустой строкой")
                cfg["model"] = value
            elif key == "temperature":
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not (0 <= value <= 2)):
                    raise ValueError("Поле temperature должно быть числом от 0 до 2")
                cfg["temperature"] = value
            elif key == "max_tokens":
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError("Поле max_tokens должно быть целым числом больше 0")
                cfg["max_tokens"] = value
            elif key == "system_prompt":
                if not isinstance(value, str):
                    raise ValueError("Поле system_prompt должно быть строкой")
                cfg["system_prompt"] = value
            else:
                raise ValueError(f"Неизвестное поле конфига: {key}")
        atomic_write_json(self._p_config, cfg)
        return cfg

    # ---------- построение запроса ----------

    def build_profile_block(self, dialogue_id: str) -> str:
        """Блок профиля пользователя для system-промпта (день 12).

        Пусто, если профиль не active или все поля пусты. Порядок в
        build_payload: базовый промпт → профиль → блоки памяти."""
        p = self.store.profile_get(dialogue_id)
        if p["status"] != "active":
            return ""
        lines = []
        if p["name"]:
            lines.append(f"- Имя: {p['name']}")
        if p["role"]:
            lines.append(f"- Роль и сфера: {p['role']}")
        if p["tone"]:
            lines.append(f"- Тон и стиль: {p['tone']}")
        if p["taboos"]:
            lines.append(f"- Стоп-слова/табу: {p['taboos']}")
        if not lines:
            return ""
        block = "\n\nПрофиль пользователя:\n" + "\n".join(lines)
        if p["taboos"]:
            block += f"\nИзбегай: {p['taboos']}"
        return block

    def build_payload(self, dialogue_id: str) -> list:
        """Список сообщений для LLM: [system (промпт + блоки ВКЛЮЧЁННЫХ слоёв
        памяти + правило при наличии памяти)] + история диалога (только
        role/content — служебные поля вроде model в API не уходят).

        Тумблеры слоёв (toggles.json): ст off — в LLM уходит только текущее
        сообщение (история не шлётся, сообщения по-прежнему хранятся);
        wm/lt off — их блоки не добавляются в system-промт."""
        cfg = self.get_config()
        t = self.store.get_toggles()
        st_on = t["st"]
        blocks = self.store.build_memory_blocks(dialogue_id)
        system = (cfg["system_prompt"] + self.build_profile_block(dialogue_id)
                  + blocks + (MEMORY_RULE if blocks else ""))
        # Отключённый непустой слой: его следы могут остаться в истории
        # («по памяти ...» в прошлых ответах) — явно говорим, что слой
        # отключён, иначе модель продолжает «отрабатывать» память из диалога.
        disabled = []
        if not t["wm"] and self.store.wm_items(dialogue_id):
            disabled.append("рабочая память «Текущая задача»")
        if not t["lt"] and self.store.lt_items():
            disabled.append("«Долговременная память»")
        if disabled:
            system += ("\n\nОтключённые слои: " + ", ".join(disabled) +
                       ". Они не применяются к этому запросу: не ссылайся "
                       "на их пункты и не отказывай, ссылаясь на них, даже "
                       "если они упоминались ранее в диалоге.")
        msgs = self.store.get_messages(dialogue_id)
        if st_on:
            history = [{"role": m["role"], "content": m["content"]} for m in msgs]
        else:
            # текущее сообщение — последнее в списке (уже дописано)
            history = [{"role": "user", "content": msgs[-1]["content"]}
                       if msgs else []]
        return [{"role": "system", "content": system}] + history

    # ---------- стриминг ответа ----------

    def ask_stream(self, dialogue_id: str, message: str):
        """Синхронный генератор: отправить сообщение в LLM и стримить события.

        События: {"type": "delta", "text"}, затем {"type": "done", "answer",
        "usage", "request_id"}; при любой ошибке — {"type": "error", "message"}
        (исключение наружу не бросается).
        """
        d = self.store.get_dialogue(dialogue_id)
        need_title = (d is not None and not d.get("messages")
                      and d.get("title") == DEFAULT_DIALOGUE_TITLE)
        self.store.append_message(dialogue_id, "user", message)
        cfg = self.get_config()
        if need_title:
            # Авто-название по первому сообщению (best-effort: ошибка — без названия)
            new_title = self._generate_title(cfg["model"], message)
            if new_title:
                try:
                    self.store.rename_dialogue(dialogue_id, new_title)
                except ValueError:
                    pass
        # День 12: pending-профиль — служебный ход без LLM-стрима
        # (приглашение/интервью/отказ); первый запрос не выполняется.
        profile = self.store.profile_get(dialogue_id)
        if profile["status"] == "pending":
            answer = self._profile_init_turn(dialogue_id, message,
                                             cfg["model"])
            self.store.append_message(dialogue_id, "assistant", answer)
            yield {"type": "done", "answer": answer,
                   "usage": None, "request_id": None}
            return
        messages = self.build_payload(dialogue_id)
        # Server-side гард: мелкие модели при истории диалога подчиняются
        # свежему противоречащему запросу, игнорируя правило в system-промте.
        # Детектированный конфликт → явное system-напоминание в самом конце
        # списка: последние сообщения влияют на ответ сильнее.
        conflicts = self._detect_memory_conflict(dialogue_id, message)
        if conflicts:
            items = "; ".join(f"{k}: {v}" for k, v in conflicts)
            messages.append({
                "role": "system",
                "content": ("⚠️ Конфликт: запрос пользователя противоречит "
                            f"пунктам памяти: {items}. По правилу памяти ты "
                            "обязан вежливо отказаться выполнить "
                            "противоречащую часть запроса (с лёгкой "
                            "доброжелательной шуткой, ссылаясь на конкретный "
                            "пункт) и сообщить, что принято решение "
                            "действовать по памяти."),
            })
        body = {
            "model": cfg["model"],
            "temperature": cfg["temperature"],
            "max_tokens": cfg["max_tokens"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": messages,
        }
        usage = None
        parts = []
        error = None
        try:
            with self._client.stream(
                "POST", self.base_url + "/chat/completions", json=body,
                headers={"Authorization": "Bearer " + self._key_for(cfg["model"])}) as resp:
                if resp.status_code != 200:
                    error = f"Модель вернула ошибку HTTP {resp.status_code}"
                else:
                    for line in resp.iter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        choices = chunk.get("choices") or [{}]
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            parts.append(delta)
                            yield {"type": "delta", "text": delta}
                        if isinstance(chunk.get("usage"), dict):
                            usage = chunk["usage"]
        except httpx.HTTPError as e:
            error = f"Ошибка обращения к модели: {e}"
        except Exception as e:  # битый JSON чанка и пр.
            error = f"Непредвиденная ошибка стрима: {e}"

        if error is not None:
            self._append_request_log(cfg["model"], body, None, error)
            yield {"type": "error", "message": error}
            return

        answer = "".join(parts)
        self.store.append_message(dialogue_id, "assistant", answer,
                                  model=cfg["model"])
        if usage:
            self._session["prompt"] += usage.get("prompt_tokens", 0)
            self._session["completion"] += usage.get("completion_tokens", 0)
            self._session["total"] += usage.get("total_tokens", 0)
            self._last_usage = usage
        rid = self._append_request_log(cfg["model"], body, usage, None)
        yield {"type": "done", "answer": answer, "usage": usage, "request_id": rid}

    # ---------- журнал запросов (requests.json) ----------

    def _append_request_log(self, model: str, body: dict, usage, error) -> int:
        """Добавить запись в журнал (cap 100, FIFO) и вернуть её id."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
            nid = max((e.get("id", 0) for e in entries if isinstance(e, dict)), default=0) + 1
            entries.append({"id": nid, "ts": _now(), "model": model,
                            "request": body, "usage": usage, "error": error})
            if len(entries) > JOURNAL_CAP:
                entries = entries[-JOURNAL_CAP:]
            atomic_write_json(self._p_requests, entries)
            return nid

    def requests_list(self) -> list:
        """Журнал без тел запросов: [{id, ts, model, total_tokens, error}]."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
        out = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            usage = e.get("usage")
            out.append({
                "id": e.get("id"),
                "ts": e.get("ts"),
                "model": e.get("model"),
                "total_tokens": usage.get("total_tokens") if isinstance(usage, dict) else None,
                "error": e.get("error"),
            })
        return out

    def requests_get(self, request_id: int):
        """Полная запись журнала по id (с телом запроса) или None."""
        with self._lock:
            entries = read_json(self._p_requests, None)
            entries = entries if isinstance(entries, list) else []
        for e in entries:
            if isinstance(e, dict) and e.get("id") == request_id:
                return e
        return None

    def requests_clear(self) -> None:
        """Очистить журнал запросов."""
        with self._lock:
            atomic_write_json(self._p_requests, [])

    # ---------- сессионные токены ----------

    def session_tokens(self) -> dict:
        """Суммарные токены текущей сессии {prompt, completion, total} (in-memory)."""
        return dict(self._session)

    def last_usage(self):
        """usage последнего успешного запроса (dict) или None."""
        return self._last_usage

    # ---------- список моделей ----------

    def list_models(self) -> list:
        """Доступные для ключа модели API: [{id, context_limit}].

        GPustack в /models отдаёт ВСЕ модели без статуса, поэтому доступность
        определяется зондом (минимальный запрос max_tokens=1): 200 — модель в
        списке, 403/ошибка — нет. Результат кэшируется на MODEL_PROBE_TTL.
        Неизвестной модели — DEFAULT_CONTEXT_LIMIT; при недоступном API
        бросает httpx.HTTPError (роут вернёт 502).
        """
        now = time.time()
        if self._models_cache is not None and now - self._models_cache_ts < MODEL_PROBE_TTL:
            return self._models_cache
        resp = self._client.get(self.base_url + "/models",
                                headers={"Authorization": "Bearer " + self.api_key})
        if resp.status_code != 200:
            raise httpx.HTTPStatusError(
                f"Модель вернула ошибку HTTP {resp.status_code}",
                request=resp.request, response=resp)
        data = resp.json().get("data") or []
        models = [{"id": m.get("id"),
                   "context_limit": CONTEXT_LIMITS.get(m.get("id"), DEFAULT_CONTEXT_LIMIT)}
                  for m in data if isinstance(m, dict) and m.get("id")]
        available = [m for m in models if self._probe_model(m["id"])]
        self._models_cache = available
        self._models_cache_ts = now
        return available

    def ensure_model_available(self) -> None:
        """Self-heal: если модель из конфига недоступна (не прошла зонд), а
        доступные есть — сбросить на первую доступную (persist). API недоступен
        или доступных нет — конфиг не трогаем."""
        try:
            available = self.list_models()
        except httpx.HTTPError:
            return
        if not available:
            return
        if self.get_config()["model"] not in {m["id"] for m in available}:
            self.set_config({"model": available[0]["id"]})

    def _profile_init_turn(self, dialogue_id: str, message: str,
                           model: str) -> str:
        """Ход служебного потока инициализации профиля (pending-диалог).

        Возвращает текст ответа; меняет статус профиля:
        - interview-флаг: ответ трактуется как анкета → LLM-экстракт;
          успех → active + подтверждение, сбой → просьба повторить;
        - маркеры в сообщении: интервью/вручную/отказ;
        - иначе → повторное приглашение.
        """
        p = self.store.profile_get(dialogue_id)
        if p["interview"]:
            extracted = self._extract_profile(model, message)
            if extracted:
                self.store.profile_set(dialogue_id, extracted["name"],
                                       extracted["role"], extracted["tone"],
                                       extracted["taboos"])
                return ("Профиль сохранён (имя: {}, роль и сфера: {}, "
                        "тон и стиль: {}, стоп-слова/табу: {}). Теперь "
                        "буду отвечать с учётом ваших предпочтений.".format(
                            extracted["name"] or "—",
                            extracted["role"] or "—",
                            extracted["tone"] or "—",
                            extracted["taboos"] or "—"))
            return ("Не смог разобрать ответ по всем вопросам анкеты. "
                    "Повторите, пожалуйста, одним сообщением: имя, роль и "
                    "сфера, тон и стиль общения, стоп-слова/табу.")
        low = message.lower()
        if any(m in low for m in PROFILE_INTERVIEW_MARKERS):
            self.store.profile_action(dialogue_id, "interview")
            return PROFILE_INTERVIEW_TEXT
        if any(m in low for m in PROFILE_MANUAL_MARKERS):
            return PROFILE_MANUAL_TEXT
        if any(m in low for m in PROFILE_DECLINE_MARKERS):
            self.store.profile_action(dialogue_id, "decline")
            return PROFILE_DECLINED_TEXT
        return PROFILE_INVITE_TEXT

    def _detect_memory_conflict(self, dialogue_id: str, message: str) -> list:
        """Пункты ВКЛЮЧЁННЫХ слоёв (WM диалога + LT), противоречащие запросу.

        Эвристика: ключ встречается в сообщении, значение НЕ встречается
        (пользователь называет другое), в сообщении есть глагол действия.
        Ключ короче 4 символов слишком неоднозначен — пропускается.
        Вопросы про память («какой источник?») конфликтом не считаются.
        Возвращает список (key, value).
        """
        msg = message.lower()
        if not any(v in msg for v in CONFLICT_VERBS):
            return []
        t = self.store.get_toggles()
        items = {}
        if t["wm"]:
            items.update(self.store.wm_items(dialogue_id))
        if t["lt"]:
            items.update(self.store.lt_items())
        hits = []
        for key, value in items.items():
            k = str(key).lower().strip()
            v = str(value).lower().strip()
            if len(k) < 4:
                continue
            if k in msg and v and v not in msg:
                hits.append((key, value))
        return hits

    def _extract_profile(self, model: str, answer: str) -> dict | None:
        """Извлечь профиль из ответа на анкету (best-effort, non-stream).

        Не входит в requests.json (как авто-заголовок). Успех →
        {"name","role","tone","taboos": str}; сбой/не-JSON → None
        (интервью остаётся активным, агент попросит повторить)."""
        body = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 600,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system",
                 "content": ("Пользователь ответил на анкету профиля: имя; "
                             "профессиональная роль и сфера; тон и стиль "
                             "общения; стоп-слова/табу. Извлеки значения и "
                             "верни ТОЛЬКО JSON-объект без пояснений и без "
                             'markdown: {"name": "...", "role": "...", '
                             '"tone": "...", "taboos": "..."}. Если значение '
                             "не указано — пустая строка.")},
                {"role": "user", "content": answer[:1000]},
            ],
        }
        try:
            resp = self._client.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self._key_for(model)},
                json=body, timeout=60,
            )
            if resp.status_code != 200:
                return None
            content = (((resp.json().get("choices") or [{}])[0]
                        .get("message") or {}).get("content") or "")
            s, e = content.find("{"), content.rfind("}")
            if s == -1 or e <= s:
                return None
            obj = json.loads(content[s:e + 1])
            if not isinstance(obj, dict):
                return None
            return {f: (obj[f].strip() if isinstance(obj.get(f), str) else "")
                    for f in ("name", "role", "tone", "taboos")}
        except (httpx.HTTPError, ValueError):
            return None

    def _generate_title(self, model: str, message: str) -> str | None:
        """Авто-заголовок диалога по первому сообщению (best-effort).

        Non-stream. Первая попытка с отключённым reasoning
        (chat_template_kwargs) — у reasoning-моделей (deepseek) бюджет
        max_tokens съедает «размышление» и content пуст. Если не сработала —
        повтор без параметра с большим max_tokens: заголовок берётся из
        последней строки ответа (glm «думает» внутри content).
        Любой сбой → None — чат это не затрагивает.
        """
        base_body = {"model": model, "temperature": 0.3, "messages": [
            {"role": "system",
             "content": ("Ты придумываешь названия диалогов. По сообщению "
                         "пользователя придумай краткое название диалога: "
                         "2–5 слов, без кавычек, без точки в конце, без "
                         "пояснений. Ответь только названием.")},
            {"role": "user", "content": message[:500]},
        ]}
        attempts = [
            dict(base_body, max_tokens=40,
                 chat_template_kwargs={"enable_thinking": False}),
            dict(base_body, max_tokens=400),
        ]
        for body in attempts:
            try:
                resp = self._client.post(
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self._key_for(model)},
                    json=body,
                    timeout=60,
                )
                if resp.status_code != 200:
                    continue
                choice = (resp.json().get("choices") or [{}])[0]
                if choice.get("finish_reason") == "length":
                    # ответ обрезан — «хвост» будет осколком размышления,
                    # а не названием; пробуем следующую попытку
                    continue
                content = ((choice.get("message") or {}).get("content")
                           or "").strip()
                if not content:
                    continue
                lines = [l.strip() for l in content.splitlines() if l.strip()]
                line = lines[-1]
                if len(line) > 60:
                    # модель «подумала» в той же строке: название — после
                    # последней точки/восклицания/вопроса (finish=stop)
                    idx = max(line.rfind("."), line.rfind("!"), line.rfind("?"))
                    if idx != -1:
                        line = line[idx + 1:].strip()
                # цитаты и точка в конце: «Название»., "Название"., Название
                title = line.strip('"«»\'').strip().rstrip(".").strip().strip('"«»\'').strip()
                # > 50 символов — не название, а осколок «размышлений»:
                # лучше без названия, чем мусор
                if not title or len(title) > 50:
                    continue
                return title[:60]
            except (httpx.HTTPError, ValueError):
                continue
        return None

    def _key_for(self, model: str) -> str:
        """Ключ API для модели: переменная MODEL_KEY_ENV[модель] из окружения;
        фолбэк — основной self.api_key (например, если переменная не задана)."""
        return self._env.get(MODEL_KEY_ENV.get(model, DEFAULT_KEY_ENV)) or self.api_key

    def _probe_model(self, model_id: str) -> bool:
        """Минимальный зонд доступности модели: 200 — доступна для её ключа."""
        try:
            resp = self._client.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self._key_for(model_id)},
                json={"model": model_id, "max_tokens": 1,
                      "messages": [{"role": "user", "content": "."}]},
                timeout=15,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
