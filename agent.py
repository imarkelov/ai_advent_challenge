"""Простой агент с LLM-клиентом: одна модель, диалоги на диске.

Диалоги хранятся в JSON-файле (по умолчанию dialogues.json рядом со
скриптом): {"active_id": str, "dialogues": [ {id, created_at, closed_at,
messages}, ... ]}. Один диалог активен (closed_at=null), остальные
закрыты (архив). Активный диалог: последние 20 сообщений уходят в LLM
(cap HISTORY_CAP), на диск записывается список целиком. Файл
загружается при создании агента и атомарно записывается после каждого
успешного ответа, сброса и «Нового диалога» — после перезапуска
сервера активный диалог продолжается, архив сохраняется.

Миграция: если dialogues.json отсутствует, а legacy-файл history.json
содержит непустой список сообщений — он становится единственным
(активным) диалогом; дальше history.json не используется.

Настройки (system_prompt/model/temperature/max_tokens/reasoning) тоже хранятся
в памяти агента и сбрасываются к значениям по умолчанию при рестарте сервера.

Сжатие контекста (day9): в LLM уходят последние DEFAULT_WINDOW_SIZE сообщений
активного диалога (скользящее окно) + LLM-сводка более старых сообщений,
которая пересобирается, когда сообщений сверх окна накопилось
DEFAULT_SUMMARY_GAP и более. Сводка хранится НА ДИАЛОГЕ (поле "summary") и
уходит в LLM в system-сообщении как «Резюме диалога: ...». Сводка строится
одним отдельным запросом к той же модели (temperature=0, thinking выключен,
max_tokens=SUMMARY_MAX_TOKENS, RU-промпт). Ошибка или пустая сводка —
деградация: история не трогается (повтор на следующем ask()), ask() работает
со всей (необрезанной) историей. Сжатие выключено (compression_enabled=False)
— режим day8: последние HISTORY_CAP сообщений, без сводки.

Клиентские настройки (базовый URL и ключи) читаются из переменных окружения
лениво, в момент каждого запроса:
  GPUSTACK_BASE_URL — базовый URL API (обязательна);
  GPUSTACK_API_KEY / GPUSTACK_KEY_DEEPSEEK / GPUSTACK_KEY_GLM — ключ,
  выбирается по модели (см. MODEL_KEY_ENV).

Ошибки — только исключения RuntimeError (в production-коде нет print).
"""
from datetime import datetime
import json
import math
import os
import sys
import threading
import urllib.error
import urllib.request

from strategies import STRATEGIES

DEFAULT_SYSTEM_PROMPT = "Ты — простой полезный ассистент. Отвечай на русском языке."

# Модель → env-переменная с API-ключом
MODEL_KEY_ENV = {
    "qwen3.8-27b": "GPUSTACK_API_KEY",
    "deepseek-v4-flash": "GPUSTACK_KEY_DEEPSEEK",
    "glm-5.3-flash": "GPUSTACK_KEY_GLM",
}

TIMEOUT = 300  # неконтролируемая генерация может занимать >120 с
HISTORY_CAP = 20  # держим последние N сообщений истории

# day9 (сжатие контекста): скользящее окно + LLM-сводка
DEFAULT_WINDOW_SIZE = 6  # в LLM уходят последние N сообщений диалога
DEFAULT_SUMMARY_GAP = 4  # каждые N сообщений сверх окна — новая LLM-сводка
SUMMARY_MAX_TOKENS = 300  # бюджет ответа сводочного запроса

# диалоги на диске: по умолчанию рядом со скриптом
DIALOGUES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dialogues.json")

# legacy-файл истории (ит.1): используется только для миграции в dialogues.json
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")

# content может быть None (reasoning-модель): бюджет ушёл в рассуждения
NO_TEXT_PLACEHOLDER = "(модель не дала текста — бюджет ушёл в рассуждения)"

# маркер "не менять" для configure()
UNSET = object()

# Лимит контекста моделей (токены). Источник — спецификации моделей;
# GPustack context length не отдаёт. None → UI показывает «—».
CONTEXT_LIMITS = {
    "qwen3.8-27b": 32768,
    "deepseek-v4-flash": 128000,
    "glm-5.3-flash": 128000,
}


# Каллиброванные нормы токенизации (токенов на символ).
# Измерено по реальному API GPustack (пробные запросы max_tokens=1), 12.09.2026:
#   rate = (prompt_tokens(текст) - prompt_tokens("")) / len(текст)
#   "other" — 142-символьный русский тест (кириллица/латиница/цифры/пунктуация);
#   "cjk"   — 17-символьная CJK-строка.
# rate считается от чистого контента (шаблон-оверхед p0 вычтен), поэтому
# подсчёт — это токены ТЕКСТА сообщений, без служебных токенов chat-шаблона.
TOKEN_RATES = {
    "qwen3.8-27b":     {"other": 0.4366, "cjk": 0.588},
    "deepseek-v4-flash": {"other": 0.3803, "cjk": 0.588},
    "glm-5.3-flash":   {"other": 0.4085, "cjk": 0.588},
}
# запасная норма для неизвестной модели (средняя по замерам)
DEFAULT_TOKEN_RATE = {"other": 0.408, "cjk": 0.588}


def count_tokens(text: str, model: str | None = None) -> int:
    """Оценка числа токенов ТЕКСТА (без BPE-токенайзера в stdlib).

    Символы делятся на две группы, каждая умножается на каллиброванную
    норму модели (см. TOKEN_RATES) и округляется вверх:
      - CJK (Han/Kana/Hangul) — rates["cjk"] токенов на символ;
      - остальное (кириллица, латиница, цифры, пунктуация, пробелы) —
        rates["other"] токенов на символ.
    Точность ±10% по замерам — для HUD достаточно.
    Клиентское зеркало — countTokens() в index.html (нормы приходят из
    GET /agent/models: token_rate); при изменении менять оба места.
    """
    if not isinstance(text, str) or not text:
        return 0
    rates = TOKEN_RATES.get(model, DEFAULT_TOKEN_RATE)
    cjk = 0
    other = 0
    for ch in text:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF      # CJK Unified
                or 0x3400 <= o <= 0x4DBF   # CJK Extension A
                or 0x3040 <= o <= 0x30FF   # Kana
                or 0xAC00 <= o <= 0xD7A3):  # Hangul
            cjk += 1
        else:
            other += 1
    return max(1, math.ceil(cjk * rates["cjk"] + other * rates["other"]))


def _new_dialogue_id() -> str:
    """Новый id диалога: d-<YYYYMMDDTHHMMSS>-<4 hex> (уникальный за счёт uuid)."""
    import uuid
    return "d-" + datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _clean_messages(data) -> list:
    """Отфильтровать список сообщений: только user/assistant со str-контентом."""
    if not isinstance(data, list):
        return []
    return [
        {"role": m["role"], "content": m["content"]}
        for m in data
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
    ]


def list_models() -> list:
    """Доступные чат-модели: [{"id", "context_limit", "token_rate"}, ...].

    token_rate — каллиброванные нормы (TOKEN_RATES) для клиентского live-
    подсчёта «текущего запроса» (норма модели, unknown → null, клиент
    подставит DEFAULT).
    """
    return [
        {"id": m, "context_limit": CONTEXT_LIMITS.get(m),
         "token_rate": TOKEN_RATES.get(m)}
        for m in MODEL_KEY_ENV
    ]


class SimpleAgent:
    """Агент с одной LLM-моделью и диалогами на диске (dialogues.json).

    Модель данных: {"active_id": str, "dialogues": [{id, created_at,
    closed_at, messages}, ...]} — один активный диалог (closed_at=null),
    остальные — архив. self.history — тот же объект-список
    self._active["messages"] (алиас, без копий): ask() делает in-place
    append/trim, переключение диалога — переприсваивание.

    Публичный API:
      ask(user_input) -> dict — собирает messages из system-промпта +
      истории активного диалога + нового вопроса, шлёт POST
      {base}/chat/completions и возвращает
      {"reply": str, "reasoning": str|None, "usage": {...}} (см. ask()).
      При неудаче — RuntimeError; история не меняется.
      configure(...) — атомарно меняет настройки (system_prompt, model,
      temperature, max_tokens, reasoning); аргумент по умолчанию (UNSET) =
      «не менять», None для temperature/max_tokens = сброс в None;
       валидация — RuntimeError, при неудаче настройки не меняются.
        get_config() -> dict — текущие настройки.
        get_last_request() -> dict | None — копия последнего JSON-запроса
        к LLM (payload /chat/completions) или None, если ask ещё не было.
         get_history() -> list — копия истории активного диалога
        [{"role": ..., "content": ...}, ...].
         get_token_stats() -> dict — токены для HUD: вся история
        активного диалога (каллиброванный count_tokens по нормам модели)
        + последний ответ модели (точные completion_tokens из usage).
       reset_history() — очистить активный диалог и файл (closed не
       помечается).
        new_dialogue() -> str — закрыть текущий (архив на диск) и открыть
        новый пустой; пустой активный не дублируется (возвращает его id).
        get_dialogues() -> dict — сводка по диалогам для UI
        (id, времена, message_count, last_message<=80, active_id).
        get_dialogue_messages(dialogue_id) -> list | None — копия сообщений
        диалога по id (активного или архивного); None, если такого нет.
         activate_dialogue(dialogue_id) -> str | None — открыть диалог по id
         для продолжения: он становится активным, предыдущий активный
         закрывается (closed_at = now, даже если пуст); id == active → noop
         (возврат того же id); None — такого диалога нет.
         get_strategy_info() -> dict — стратегия активного диалога,
         состояние (глубокая копия) и факты (day10).
         switch_strategy(name) -> dict — сменить стратегию активного
         диалога (свежее state); неизвестная — ValueError.
         make_checkpoint() -> dict — чекпоинт ветвления (только
         "branching", иначе ValueError); возвращает state.
         switch_branch(name) -> dict — переключить ветку A/B (только
         "branching"; не branching / плохое имя — ValueError).

        Инвариант: в любой момент ровно один диалог активен (closed_at=null);
        при случайных двух closed_at=null в файле инвариант восстанавливается
        при первой записи (activate_dialogue / new_dialogue / ask).
    """

    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT, model: str = "qwen3.8-27b",
                 temperature=None, max_tokens=None, history_file=None, reasoning: bool = True,
                 dialogues_file=None):
        self.system_prompt = system_prompt
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning = reasoning  # включено ли рассуждение (thinking) модели
        # day9 (сжатие контекста): параметры скользящего окна и LLM-сводки
        self._window_size = DEFAULT_WINDOW_SIZE
        self._summary_gap = DEFAULT_SUMMARY_GAP
        self._compression_enabled = True
        # day10: стратегия сборки контекста. self._default_strategy —
        # стратегия для НОВЫХ диалогов (меняется configure(strategy=...));
        # текущая стратегия живёт на ДИАЛОГЕ (ключ "strategy", absent ->
        # "legacy"). Экземпляры стратегий кэшируются по классу
        # (self._strategy_instances) — состояние per-dialogue (strategy_state).
        self._default_strategy = "legacy"  # байт-в-байт поведение day9
        self._strategy_instances = {}
        self.history_file = history_file or HISTORY_FILE  # только для миграции
        self.dialogues_file = dialogues_file or DIALOGUES_FILE
        # RLock: методы стратегии (switch_strategy и др.) вызывают
        # get_strategy_info(), который тоже берёт self._lock
        self._lock = threading.RLock()
        self._last_request = None  # копия последнего payload ask() (под self._lock)
        self._last_usage = None  # usage последнего ask(): точные токены API
        self._dialogues = self._load_dialogues()  # {"active_id", "dialogues"}
        # активный — по active_id (порядок в списке не гарантирует:
        # закрытые диалоги стоят раньше активного)
        active = next((d for d in self._dialogues["dialogues"]
                       if d["id"] == self._dialogues["active_id"]), None)
        self._active = active if active is not None else self._dialogues["dialogues"][0]
        self.history = self._active["messages"]  # алиас (тот же объект-список)

    def configure(self, system_prompt=UNSET, model=UNSET, temperature=UNSET, max_tokens=UNSET,
                  reasoning=UNSET, window_size=UNSET, summary_gap=UNSET,
                  compression_enabled=UNSET, strategy=UNSET):
        """Атомарно изменить настройки: сначала валидация всех переданных
        (не-UNSET) значений, потом применение (все или ничего).

        Семантика аргументов:
          UNSET (значение по умолчанию) — параметр не менять;
          temperature / max_tokens: None — сбросить в None; иначе — валидное
          число (0 <= t <= 2) / положительный int;
          system_prompt / model: None — ошибка (агент обязан иметь промпт
          и известную модель);
          reasoning: строго bool (int не проходит) — включать/выключать
          рассуждение (thinking) модели;
           window_size / summary_gap (day9): int (>=2 / >=1, bool не проходит) —
           размер скользящего окна сообщений и шаг LLM-сводки;
           compression_enabled (day9): строго bool — сжатие контекста вкл/выкл;
           strategy (day10): str из ключей STRATEGIES — стратегия сборки
           контекста для НОВЫХ диалогов (активный диалог не меняется;
           смена активного — switch_strategy()).

        Ошибки валидации — RuntimeError (strategy — ValueError);
        при неудаче настройки не меняются.
        """
        with self._lock:
            if system_prompt is not UNSET:
                if not isinstance(system_prompt, str) or not system_prompt.strip():
                    raise RuntimeError("system_prompt must be a non-empty string")
            if model is not UNSET:
                if not isinstance(model, str) or model not in MODEL_KEY_ENV:
                    raise RuntimeError(f"unknown model {model!r} (доступные: {', '.join(MODEL_KEY_ENV)})")
            if temperature is not UNSET and temperature is not None:
                if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) \
                        or not (0 <= temperature <= 2):
                    raise RuntimeError("temperature must be a number between 0 and 2")
            if max_tokens is not UNSET and max_tokens is not None:
                if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
                    raise RuntimeError("max_tokens must be a positive integer")
            if reasoning is not UNSET:
                # bool — подтип int, но здесь нужен именно bool (1/0 не проходят)
                if not isinstance(reasoning, bool):
                    raise RuntimeError("reasoning must be a boolean")
            if window_size is not UNSET:
                # bool — подтип int: 1/0 не проходят, нужен именно int
                if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size < 2:
                    raise RuntimeError("window_size must be an integer >= 2")
            if summary_gap is not UNSET:
                if isinstance(summary_gap, bool) or not isinstance(summary_gap, int) or summary_gap < 1:
                    raise RuntimeError("summary_gap must be an integer >= 1")
            if compression_enabled is not UNSET:
                if not isinstance(compression_enabled, bool):
                    raise RuntimeError("compression_enabled must be a boolean")
            if strategy is not UNSET:
                if not isinstance(strategy, str) or strategy not in STRATEGIES:
                    raise ValueError(
                        f"неизвестная стратегия {strategy!r}: "
                        f"доступные — {', '.join(STRATEGIES)}"
                    )

            if system_prompt is not UNSET:
                self.system_prompt = system_prompt
            if model is not UNSET:
                self.model = model
            if temperature is not UNSET:
                self.temperature = temperature
            if max_tokens is not UNSET:
                self.max_tokens = max_tokens
            if reasoning is not UNSET:
                self.reasoning = reasoning
            if window_size is not UNSET:
                self._window_size = window_size
            if summary_gap is not UNSET:
                self._summary_gap = summary_gap
            if compression_enabled is not UNSET:
                self._compression_enabled = compression_enabled
            if strategy is not UNSET:
                self._default_strategy = strategy

    def get_config(self) -> dict:
        """Текущие настройки: system_prompt, model, temperature, max_tokens,
        reasoning, window_size, summary_gap, compression_enabled, strategy."""
        with self._lock:
            return {
                "system_prompt": self.system_prompt,
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "reasoning": self.reasoning,
                "window_size": self._window_size,
                "summary_gap": self._summary_gap,
                "compression_enabled": self._compression_enabled,
                # day10: стратегия для НОВЫХ диалогов (активный может жить
                # на своей стратегии — см. "strategy" в dict диалога)
                "strategy": self._default_strategy,
            }

    def get_last_request(self) -> dict | None:
        """Копия последнего JSON-запроса к LLM (payload /chat/completions) или None.

        Без авторизационных данных (Authorization не входит в payload).
        """
        with self._lock:
            if self._last_request is None:
                return None
            return json.loads(json.dumps(self._last_request))

    def _migrate_legacy_history(self) -> dict:
        """Миграция из legacy history.json (ит.1) в структуру диалогов.

        Только если history.json существует и содержит непустой список —
        он становится единственным активным диалогом. Иначе — новый пустой.
        """
        legacy = _clean_messages(None)
        try:
            with open(self.history_file, encoding="utf-8") as f:
                legacy = _clean_messages(json.load(f))
        except (OSError, ValueError):
            legacy = []
        now = datetime.now().isoformat(timespec="seconds")
        active_id = _new_dialogue_id()
        return {
            "active_id": active_id,
            "dialogues": [
                {"id": active_id, "created_at": now, "closed_at": None,
                 "messages": legacy[-HISTORY_CAP:],
                 "summary": ""}  # legacy-диалоги без сводки
            ],
        }

    def _load_dialogues(self) -> dict:
        """Загрузить {"active_id", "dialogues"} из dialogues.json.

        Файл валиден — использовать его (сообщения санитизируются, активный
        = по active_id, иначе единственный closed_at=null, иначе — новый
        пустой диалог). Файла нет / битый — миграция из history.json или
        новый пустой диалог (в обоих случаях — один активный).
        """
        data = None
        try:
            with open(self.dialogues_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("dialogues"), list) \
                and data["dialogues"]:
            dialogues = []
            for d in data["dialogues"]:
                if not isinstance(d, dict) or not isinstance(d.get("id"), str):
                    continue
                # whitelist day10: стратегия диалога — только str из STRATEGIES
                # (отсутствует/не-str/неизвестная -> "legacy": байт-в-байт
                # поведение day9 для старых файлов и повреждённых полей)
                raw_strategy = d.get("strategy")
                if not isinstance(raw_strategy, str) or raw_strategy not in STRATEGIES:
                    raw_strategy = "legacy"
                # whitelist day10: strategy_state — только dict; иначе свежий
                # default_state() стратегии диалога (в т.ч. при отсутствии —
                # как _ensure_strategy_state, но валидация уже при загрузке)
                raw_state = d.get("strategy_state")
                if not isinstance(raw_state, dict):
                    raw_state = STRATEGIES[raw_strategy]().default_state()
                else:
                    for key, value in STRATEGIES[raw_strategy]().default_state().items():
                        raw_state.setdefault(key, value)  # недостающие ключи дописать
                dialogues.append({
                    "id": d["id"],
                    "created_at": d.get("created_at") if isinstance(d.get("created_at"), str)
                    else datetime.now().isoformat(timespec="seconds"),
                    "closed_at": d.get("closed_at") if isinstance(d.get("closed_at"), str) else None,
                    "messages": _clean_messages(d.get("messages"))[-HISTORY_CAP:],
                    # whitelist day9: сводка диалога; не-str (None/число) -> ""
                    "summary": d.get("summary") if isinstance(d.get("summary"), str) else "",
                    # whitelist day10: стратегия и её состояние (см. выше)
                    "strategy": raw_strategy,
                    "strategy_state": raw_state,
                })
            if dialogues:
                active_id = data.get("active_id")
                active = next((d for d in dialogues if d["id"] == active_id), None)
                if active is None:
                    active = next((d for d in dialogues if d["closed_at"] is None), None)
                if active is None:
                    active = dialogues[-1]
                    active["closed_at"] = None
                return {"active_id": active["id"], "dialogues": dialogues}
        # миграция из legacy history.json (или новый пустой диалог)
        structure = self._migrate_legacy_history()
        if structure["dialogues"][0]["messages"]:
            # миграцию зафиксировать на диске сразу (legacy-история не теряется)
            self._save_dialogues_data(structure)
        return structure

    def _save_dialogues(self) -> None:
        """Атомарная запись диалогов: tmp-файл + os.replace (нет полубитого файла).

        Вызывать ТОЛЬКО под self._lock (в ask / reset_history / new_dialogue).
        """
        self._save_dialogues_data(self._dialogues)

    def _save_dialogues_data(self, data) -> None:
        """Атомарная запись структуры {"active_id", "dialogues"} в self.dialogues_file."""
        tmp = self.dialogues_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.dialogues_file)

    def get_history(self) -> list:
        """Копия истории активного диалога: [{"role": ..., "content": ...}, ...]."""
        with self._lock:
            return [dict(m) for m in self.history]

    def get_token_stats(self) -> dict:
        """Токены активного диалога для HUD.

        {"history_tokens": int,  # вся история: каллиброванный подсчёт
                                  #  (count_tokens) по нормам модели
          "reply_tokens": int,   # ответ модели: точные completion_tokens
                                  #  из usage последнего API-ответа; если usage
                                  #  ещё нет (после рестарта) — оценка по тексту
          "summary_tokens": int} # сводка диалога: каллиброванный подсчёт
                                  #  (count_tokens); пустая сводка — 0
        """
        with self._lock:
            history_tokens = sum(
                count_tokens(m.get("content") or "", self.model)
                for m in self.history
            )
            reply_tokens = 0
            usage = self._last_usage or {}
            ct = usage.get("completion_tokens")
            if isinstance(ct, int):
                reply_tokens = ct
            else:
                for m in reversed(self.history):
                    if m.get("role") == "assistant":
                        reply_tokens = count_tokens(
                            m.get("content") or "", self.model)
                        break
            summary_tokens = count_tokens(
                self._active.get("summary", "") or "", self.model)
            return {
                "history_tokens": history_tokens,
                "reply_tokens": reply_tokens,
                "summary_tokens": summary_tokens,
            }

    def reset_history(self) -> None:
        """Очистить активный диалог (и файл) — closed не помечается."""
        with self._lock:
            self.history.clear()  # in-place: алиас на self._active["messages"]
            self._active["summary"] = ""  # day9: сводка к пустой истории не нужна
            # day10: state стратегии — как к пустой истории: свежий
            # default_state() ТЕКУЩЕЙ стратегии диалога (имя стратегии не
            # меняется; зеркалит сброс сводки, day9-семантика)
            name = self._resolve_strategy_name()
            self._active["strategy_state"] = STRATEGIES[name]().default_state()
            self._save_dialogues()

    def new_dialogue(self) -> str:
        """Закрыть текущий диалог (архив на диск) и открыть новый пустой.

        Если активный диалог пуст — новый не создаётся (возвращается его id).
        """
        with self._lock:
            if not self.history:
                # day10: пустому активному диалогу проставляем стратегию по
                # умолчанию (additive; только если ключа ещё нет)
                if not isinstance(self._active.get("strategy"), str) \
                        or self._active["strategy"] not in STRATEGIES:
                    self._active["strategy"] = self._default_strategy
                    self._active["strategy_state"] = \
                        STRATEGIES[self._default_strategy]().default_state()
                return self._active["id"]
            self._active["closed_at"] = datetime.now().isoformat(timespec="seconds")
            new_id = _new_dialogue_id()
            now = datetime.now().isoformat(timespec="seconds")
            # day10: новый диалог получает стратегию по умолчанию
            # (additive ключи strategy/strategy_state)
            self._active = {"id": new_id, "created_at": now, "closed_at": None,
                            "messages": [], "summary": "",
                            "strategy": self._default_strategy,
                            "strategy_state": STRATEGIES[self._default_strategy]().default_state()}
            self._dialogues["dialogues"].append(self._active)
            self._dialogues["active_id"] = new_id
            self.history = self._active["messages"]
            self._save_dialogues()
            return new_id

    def get_dialogues(self) -> dict:
        """Список диалогов для UI: id, времена, число сообщений, последнее сообщение."""
        with self._lock:
            out = []
            for d in self._dialogues["dialogues"]:
                last = d["messages"][-1]["content"] if d["messages"] else None
                out.append({
                    "id": d["id"],
                    "created_at": d["created_at"],
                    "closed_at": d["closed_at"],
                    "message_count": len(d["messages"]),
                    "last_message": (last[:80] if isinstance(last, str) else None),
                })
            return {"active_id": self._dialogues["active_id"], "dialogues": out}

    def get_dialogue_messages(self, dialogue_id: str) -> list | None:
        """Копия сообщений диалога по id (активного или архивного); None — такого нет."""
        with self._lock:
            for d in self._dialogues["dialogues"]:
                if d["id"] == dialogue_id:
                    return [{"role": m["role"], "content": m["content"]} for m in d["messages"]]
            return None

    def activate_dialogue(self, dialogue_id: str) -> str | None:
        """Открыть диалог по id для продолжения: он становится активным.

        Предыдущий активный закрывается (closed_at = now), даже если пуст
        (в списке появится как архивный с 0 сообщ.). Возвращает новый
        active_id; None — такого диалога нет. id == active → noop (возврат
        того же id).
        """
        with self._lock:
            target = next((d for d in self._dialogues["dialogues"] if d["id"] == dialogue_id), None)
            if target is None:
                return None
            if dialogue_id == self._active["id"]:
                return dialogue_id
            self._active["closed_at"] = datetime.now().isoformat(timespec="seconds")
            target["closed_at"] = None
            self._active = target
            self._dialogues["active_id"] = dialogue_id
            self.history = self._active["messages"]  # алиас (тот же объект-список)
            self._save_dialogues()
            return dialogue_id

    # ------------------------------------------------------------------
    # day10 (Task 9): стратегии контекста — публичные методы для роутов
    # ------------------------------------------------------------------

    def _resolve_strategy_name(self) -> str:
        """Стратегия активного диалога; absent/не-str/неизвестный — "legacy".

        Вызывать под self._lock (читает self._active).
        """
        name = self._active.get("strategy")
        if not isinstance(name, str) or name not in STRATEGIES:
            return "legacy"
        return name

    def _ensure_strategy_state(self, strategy_name: str) -> dict:
        """strategy_state активного диалога (additive, под self._lock).

        Ключа нет/не-dict — заполнить свежим default_state() стратегии.
        Есть — дописать недостающие ключи из default_state() (setdefault,
        additive: существующие значения не перезаписываются). Возвращает
        живой dict из dict диалога (мутации — in-place).
        """
        state = self._active.get("strategy_state")
        if not isinstance(state, dict):
            state = STRATEGIES[strategy_name]().default_state()
            self._active["strategy_state"] = state
        for key, value in STRATEGIES[strategy_name]().default_state().items():
            state.setdefault(key, value)
        return state

    def _get_strategy_instance(self, strategy_name: str):
        """Экземпляр стратегии (кэш по классу; состояние per-dialogue).

        Вызывать под self._lock.
        """
        instance = self._strategy_instances.get(strategy_name)
        if instance is None:
            instance = STRATEGIES[strategy_name]()
            self._strategy_instances[strategy_name] = instance
        return instance

    def get_strategy_info(self) -> dict:
        """Стратегия активного диалога и состояние (для UI/роутов).

        {"strategy": str, "strategy_state": dict (глубокая копия),
         "facts": dict} — для legacy в копию подставляются актуальные
        live-значения сводки и флага сжатия (в самом state диалога
        summary живёт на поле "summary" диалога, а не в state).
        """
        with self._lock:
            name = self._resolve_strategy_name()
            state = self._ensure_strategy_state(name)
            if name == "legacy":
                state["summary"] = self._active.get("summary") or ""
                state["compression_enabled"] = self._compression_enabled
            facts = dict(state.get("facts") or {})
            snapshot = json.loads(json.dumps(state))  # глубокая копия
        return {"strategy": name, "strategy_state": snapshot, "facts": facts}

    def switch_strategy(self, name: str) -> dict:
        """Сменить стратегию активного диалога (свежее default_state()).

        ``name`` — ключ STRATEGIES, иначе ValueError (RU). Старое
        strategy_state НЕ переносится (состояние стратегий
        несовместимо). Возвращает get_strategy_info().
        """
        with self._lock:
            if not isinstance(name, str) or name not in STRATEGIES:
                raise ValueError(
                    f"неизвестная стратегия {name!r}: "
                    f"доступные — {', '.join(STRATEGIES)}"
                )
            self._active["strategy"] = name
            self._active["strategy_state"] = STRATEGIES[name]().default_state()
            self._save_dialogues()
            return self.get_strategy_info()

    def make_checkpoint(self) -> dict:
        """Явный чекпоинт ветвления (только стратегия "branching").

        Фиксирует преамбулу на текущей длине истории (сбрасывает ветки
        A/B — контракт BranchingStrategy.checkpoint). Не branching —
        ValueError (RU). Возвращает состояние стратегии (глубокая копия).
        """
        with self._lock:
            name = self._resolve_strategy_name()
            if name != "branching":
                raise ValueError(
                    "чекпоинт доступен только для стратегии 'branching' "
                    f"(активна: {name!r})"
                )
            state = self._ensure_strategy_state(name)
            self._get_strategy_instance(name).checkpoint(state, len(self.history))
            self._save_dialogues()
            return json.loads(json.dumps(state))

    def switch_branch(self, name: str) -> dict:
        """Переключить ветку A/B (только стратегия "branching").

        Сначала имплицитный чекпоинт (BranchingStrategy.branch: первый,
        если нет), затем switch_branch (неизвестное имя — ValueError,
        RU). Не branching — ValueError. Возвращает состояние стратегии
        (глубокая копия).
        """
        with self._lock:
            name_s = self._resolve_strategy_name()
            if name_s != "branching":
                raise ValueError(
                    "переключение веток доступно только для стратегии 'branching' "
                    f"(активна: {name_s!r})"
                )
            state = self._ensure_strategy_state(name_s)
            strategy = self._get_strategy_instance(name_s)
            strategy.branch(state, len(self.history))
            strategy.switch_branch(state, name)  # ValueError при плохом имени
            self._save_dialogues()
            return json.loads(json.dumps(state))

    def _generate_summary(self, existing_summary: str, msgs: list) -> str:
        """LLM-сводка старейших сообщений: ОДИН запрос к той же модели (day9).

        Запрос: model=self.model, temperature=0, max_tokens=SUMMARY_MAX_TOKENS,
        chat_template_kwargs={"enable_thinking": False} (сводке рассуждения
        не нужны) и RU-промпт «Сожми диалог...» с предыдущей сводкой (если
        есть) + сообщениями для сжатия. В self._last_request НЕ попадает
        (это инспекция пользовательского запроса, а не служебного).

        Возвращает стрижнутую непустую строку; при ЛЮБОЙ ошибке (сеть, API,
        разбор) или пустом ответе — "" (деградация: ask() не ломается,
        история не трогается — повтор на следующем ask()).
        """
        lines = [
            "Сожми диалог в краткое резюме на русском языке (до 150 слов).",
            "Сохрани важные решения, факты и текущее состояние задачи.",
        ]
        if existing_summary:
            lines.append("Предыдущее резюме:\n" + existing_summary)
        lines.append(
            "Сообщения для сжатия:\n"
            + "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        )
        return self._llm_raw_text([
            {"role": "system", "content": "Ты сжимаешь историю диалога."},
            {"role": "user", "content": "\n\n".join(lines)},
        ])

    def _llm_raw_text(self, messages: list) -> str:
        """Служебный текстовый LLM-вызов (сводка/извлечение), общий механизм.

        Запрос: model=self.model, temperature=0, max_tokens=SUMMARY_MAX_TOKENS,
        chat_template_kwargs={"enable_thinking": False}. В
        self._last_request НЕ попадает (это инспекция пользовательского
        запроса ask(), а не служебного). Возвращает стрижнутый текст; при
        ЛЮБОЙ ошибке (сеть, API, разбор) или пустом ответе — "" (деградация:
        RuntimeError НЕ бросаем, логируем на stderr — паттерн main.py).
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": SUMMARY_MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
        key = os.environ.get(MODEL_KEY_ENV.get(self.model) or "", "").strip()
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = data["choices"][0]["message"].get("content") or ""
        except Exception as e:
            print(f"[compression] служебный LLM-вызов не удался: {e}", file=sys.stderr)
            return ""
        return content.strip()

    def _extraction_call(self, prompt: str) -> str:
        """Служебный LLM-вызов для стратегии (извлечение sticky-фактов).

        Оборачивает промпт стратегии в ОДИН user-сообщение и ходит тем же
        механизмом, что day9-сводка (_llm_raw_text: temperature=0,
        max_tokens=SUMMARY_MAX_TOKENS, thinking выключен). В
        self._last_request НЕ попадает. При сбое — "" (fallback уже
        в facts.extract_facts: прежние факты не меняются).
        """
        return self._llm_raw_text([{"role": "user", "content": prompt}])

    def _maybe_compress(self) -> bool:
        """Сжать старейшие сообщения сверх окна (day9).

        Вызывать из ask() под self._lock, ДО сборки payload. Порог: пока
        len(history) - window_size >= summary_gap. Чанк для сжатия —
        старейшие len(history) - window_size сообщений. Успех (непустая
        сводка): сводка на диалоге + in-place trim del self.history[:k]
        (алиас self._active["messages"] не рвётся) + запись на диск.
        Сбой/пусто: False, история не трогается (повтор на след. ask()).
        """
        if not self._compression_enabled:
            return False
        k = len(self.history) - self._window_size
        while k >= self._summary_gap:
            summary = self._generate_summary(
                self._active.get("summary") or "", self.history[:k])
            if not summary:
                return False
            self._active["summary"] = summary  # сводка живёт на диалоге, не в messages
            del self.history[:k]  # in-place: алиас не рвётся
            self._save_dialogues()  # срез + сводка на диск (уже под self._lock)
            k = len(self.history) - self._window_size
        return True

    def ask(self, user_input: str) -> dict:
        """Отправить вопрос модели с учётом истории; вернуть результат ответа.

        Возвращаемый dict:
          {"reply": str,                        # текст ответа (или плейсхолдер)
           "reasoning": str | None,             # текст рассуждения, если модель дала
           "usage": {
               "prompt_tokens": int | None,
               "completion_tokens": int | None,
               "total_tokens": int | None,
               "reasoning_tokens": int | None,  # None, если API не отдал detail
           }}

        Рассуждение включается в запрос через
        chat_template_kwargs: {"enable_thinking": bool(self.reasoning)}
        (проверено на всех трёх моделях; выключенный thinking — reasoning null).

        Ответ добавляется в историю только после успешного ответа —
        только content (user+assistant), reasoning в историю не попадает.
        Ошибки API/сети/разбора — RuntimeError с описанием.

        day9 (сжатие): перед сборкой payload (под тем же self._lock) вызывается
        _maybe_compress(); при сжатии и непустой сводке активного диалога
        system-сообщение = system_prompt + «\n\nРезюме диалога: ...» (одно
        system-сообщение, фейковых реплик нет). Обрезка HISTORY_CAP применяется
        только при включённом сжатии; сжатие выключено — режим day8 (последние
        HISTORY_CAP, без сводки). Сбой сводки ask() не ломает.

        day10 (стратегии): контекст собирает стратегия диалога (ключ
        "strategy", absent — "legacy"). Порядок: on_user_message (хук, у
        S3 кладёт user-ход в ветку) ПЕРЕД build_payload. Движок сжатия
        (_maybe_compress) и обрезка HISTORY_CAP — только для legacy; прочие
        стратегии non-destructive. Ответ ассистента, кроме основной истории,
        дописывается в активную ветку при включённой ветке (branching +
        установленный checkpoint). Служебные LLM-вызовы стратегии
        (_extraction_call) в self._last_request не попадают.
        """
        with self._lock:
            # day10: контекст собирает стратегия диалога (ключ "strategy";
            # absent/неизвестный — "legacy", поведение day9 байт-в-байт)
            strategy_name = self._resolve_strategy_name()
            strategy = self._get_strategy_instance(strategy_name)
            if strategy_name == "legacy":
                # движок сжатия day9 (окно + сводка + trim) — ТОЛЬКО legacy;
                # сбой сводки не критичен (деградация)
                self._maybe_compress()
                state_for_call = {
                    "summary": self._active.get("summary") or "",
                    "compression_enabled": self._compression_enabled,
                }
            else:
                # S1/S2/S3: non-destructive — движок сжатия и HISTORY_CAP
                # НЕ применяются, state = strategy_state самого диалога
                state_for_call = self._ensure_strategy_state(strategy_name)
            # Контракт порядка вызовов (task 7): on_user_message ВСЕГДА
            # ПЕРЕД build_payload (S3 кладёт user-ход в активную ветку)
            strategy.on_user_message(user_input, state_for_call,
                                     llm_call=self._extraction_call)
            system_content, history_slice = strategy.build_payload(
                self.system_prompt, self.history, user_input, state_for_call
            )
            messages = [{"role": "system", "content": system_content}] + history_slice

            # env-переменные читаются лениво — в момент запроса
            base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
            key_env = MODEL_KEY_ENV.get(self.model)
            if key_env is None:
                raise RuntimeError(f"unknown model {self.model!r} (доступные: {', '.join(MODEL_KEY_ENV)})")
            key = os.environ.get(key_env, "").strip()
            if not key:
                raise RuntimeError(f"API key not set: env {key_env} required for model {self.model!r}")

            payload = {"model": self.model, "messages": messages}
            if self.temperature is not None:
                payload["temperature"] = self.temperature
            if self.max_tokens is not None:
                payload["max_tokens"] = self.max_tokens
            # оба состояния отправляются всегда: поведение проверено на 3 моделях
            payload["chat_template_kwargs"] = {"enable_thinking": bool(self.reasoning)}
            # копия последнего запроса для инспекции (GET /agent/last-request)
            self._last_request = json.loads(json.dumps(payload))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                raise RuntimeError(f"Network error calling {base}: {e.reason}") from e

            try:
                data = json.loads(raw)
                choice = data["choices"][0]
                message = choice["message"]
                content = message.get("content")  # может быть None (reasoning-модель)
                reasoning = message.get("reasoning")  # str или None — как в ответе
                usage = data.get("usage") or {}
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                raise RuntimeError(f"malformed response: {raw[:500]}") from None

            if content is None:
                content = NO_TEXT_PLACEHOLDER

            # completion_tokens_details может отсутствовать целиком (GLM)
            reasoning_tokens = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")

            # точные токены последнего ответа (для get_token_stats / HUD)
            self._last_usage = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "reasoning_tokens": reasoning_tokens,
            }

            # история активного диалога растёт только после успешного
            # ответа (только content); append/trim — in-place (алиас на
            # self._active["messages"] не рвётся)
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
            # day10 (task 7 контракт): S3 — агент дописывает assistant-ответ
            # ещё и в активную ветку (user-ход туда уже положен
            # on_user_message; чекпоинт не стоит — веток нет, no-op)
            if strategy_name == "branching" and state_for_call.get("checkpoint") is not None:
                state_for_call["branches"][state_for_call["active"]].append(
                    {"role": "assistant", "content": content}
                )
            # day9: cap HISTORY_CAP — только legacy при включённом сжатии
            # (выключено = режим day8); S1/S2/S3 non-destructive: del нет
            if strategy_name == "legacy" and self._compression_enabled \
                    and len(self.history) > HISTORY_CAP:
                del self.history[:len(self.history) - HISTORY_CAP]
            self._save_dialogues()
            return {
                "reply": content,
                "reasoning": reasoning,
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "reasoning_tokens": reasoning_tokens,
                },
            }
