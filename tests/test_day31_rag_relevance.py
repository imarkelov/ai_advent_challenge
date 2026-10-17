"""Регрессия дня 31: RAG отвечает ПО ЗАПРОСУ, а не подмешивает что попало.

Сценарий бага (живая проверка): агент `dev_assistant.py` на вопрос
«как устроен шлюз?» цитировал `docs/CLI-CHEATSHEET.md` (там просто пример
команды `ask "как устроен шлюз?"`), на вопрос «как запустить тесты?» —
раздел про мобильную версию, а на «кто ты?» — документ про редизайн задач.

Причины:
  1. `.dev_assistant_rag.json` лежал в репозитории и индексировался как
     документ (README* по шаблону) — агент отвечал по устаревшим чанкам;
  2. в промпт уходили 5 «ближайших» фрагментов БЕЗ порога релевантности;
  3. лексическое ядро (BM25) совпадало только по точной форме слова и
     включало служебные слова запроса («кто ты» → проза «кто-то»).

Все тесты офлайн и детерминированы: эмбеддер подменяется фейком,
LLM — заглушкой, индекс строится в tmp-каталоге.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dev_assistant as DA  # noqa: E402


# --- фейковый эмбеддер: смысловые «оси» вместо настоящей модели ---

_TOPICS = (
    ("шлюз", "gateway", "порт", "8110"),
    ("установка", "окружение", "зависимости", "requirements"),
    ("тема", "тёмная", "css", "стили"),
    ("тест", "pytest", "запуск", "backend"),
)


def _fake_vector(text: str) -> list[float]:
    low = text.lower()
    return [float(low.count(w)) for words in _TOPICS for w in words] + [0.0]


class FakeEmbedder:
    """Эмбеддер с теми же осями для документов и запросов."""

    model = "fake-embed"
    dim = 17
    prefixed = False
    last_error = ""

    def __init__(self, available: bool = True):
        self.available = available

    def is_available(self) -> bool:
        if not self.available:
            self.last_error = "в Ollama нет модели эмбеддера «fake-embed»"
        return self.available

    def embed_documents(self, texts, on_progress=None):
        return [DA._normalize(_fake_vector(t)) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _fake_vector(text)


class FakeLLM:
    """Запоминает промпт, отвечает по его контексту."""

    def __init__(self):
        self.last_system = ""
        self.last_user = ""

    def get_model(self):
        return "fake-llm"

    def is_available(self):
        return True

    def generate(self, system: str, user_prompt: str) -> str:
        self.last_system = system
        self.last_user = user_prompt
        return "Ответ по контексту."


DOCS = {
    "README.md": "# Проект\n\n## Шлюз локальной LLM\n\nШлюз — OpenAI-совместимый "
                 "сервис на порту 8110, с ключами клиентов и rate limit.\n",
    "docs/SETUP.md": "# Установка\n\n## Окружение\n\nУстановите зависимости: "
                     "pip install -r requirements.txt, скопируйте .env.example.\n",
    "docs/UI.md": "# Интерфейс\n\n## Тёмная тема\n\nПереключатель в настройках, "
                  "выбор сохраняется в localStorage.\n",
    "docs/TESTS.md": "# Тесты\n\n## Запуск тестов бэкенда\n\npytest "
                     "studio/backend/tests -q, полная регрессия требует Ollama.\n"
                     "Порт шлюза и порт сервиса задаются в конфиге.\n"
                     "Тестовый порт берётся из .env.\n",
    "studio/backend/gateway.py": '"""Шлюз: порт, ключи и лимиты клиентов."""\n\n'
                                 "#: Порт шлюза по умолчанию.\n"
                                 "DEFAULT_GATEWAY_PORT = 8110\n\n"
                                 "#: Лимиты клиента: RPM/TPM.\n"
                                 "DEFAULT_RATE_LIMIT_RPM = 60\n",
}


def _make_project(tmp: str, extra: dict | None = None) -> str:
    files = dict(DOCS)
    files.update(extra or {})
    for rel, text in files.items():
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(full) else None
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
    return tmp


class TestCorpusHygiene(unittest.TestCase):
    """Кэш RAG и служебные каталоги не должны становиться «документацией»."""

    def test_cache_file_is_not_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_project(tmp, {".dev_assistant_rag.json":
                                json.dumps({"corpus": [{"file": "ghost.md"}]})})
            files = DA.StandaloneRAG(tmp).discover_files()
            self.assertNotIn(".dev_assistant_rag.json", files)
            self.assertTrue(any(f.endswith("README.md") for f in files))

    def test_code_is_indexed_docs_only_before(self):
        """Код проекта тоже в корпусе: вопрос «где лежит порт?» — про код."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_project(tmp, {"studio/backend/gateway.py":
                                "DEFAULT_GATEWAY_PORT = 8110\n"})
            files = DA.StandaloneRAG(tmp).discover_files()
            self.assertIn("studio/backend/gateway.py", files)

    def test_stale_cache_is_invalidated(self):
        """Документ изменился — кэш не используется, индекс пересобран."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_project(tmp)
            cache = os.path.join(tmp, ".dev_assistant_rag.json")
            rag = DA.StandaloneRAG(tmp, cache_file=cache)
            rag.build_index(with_embeddings=False)
            self.assertFalse(rag.ensure_fresh())

            path = os.path.join(tmp, "docs", "NEW.md")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# Новое\n\n## Новая секция\n\nсвежий текст про шлюз\n")
            self.assertTrue(rag.ensure_fresh())
            texts = " ".join(c["text"] for c in rag.chunks)
            self.assertIn("свежий текст", texts)

    def test_old_cache_format_rejected(self):
        """Формат 1 (без версии, хэша и векторов) не подхватывается."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_project(tmp)
            cache = os.path.join(tmp, ".dev_assistant_rag.json")
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"corpus": [{"id": "x:0", "file": "ghost.md",
                                       "section": "s", "text": "устаревший чанк"}]}, f)
            rag = DA.StandaloneRAG(tmp, cache_file=cache)
            self.assertFalse(rag.stats()["indexed"])
            rag.build_index(with_embeddings=False)
            self.assertNotIn("устаревший чанк", " ".join(c["text"] for c in rag.chunks))


class TestChunking(unittest.TestCase):
    def test_section_keeps_its_own_heading(self):
        text = "# Док\n\n## Порт шлюза\n\n8110.\n\n## Темы\n\nCSS.\n"
        rag = DA.StandaloneRAG(tempfile.gettempdir())
        chunks = rag.chunk_file("doc.md", text)
        by_head = {c["section"]: c["text"] for c in chunks}
        self.assertIn("порт шлюза", by_head["Порт шлюза"].lower())
        self.assertNotIn("css", by_head["Порт шлюза"].lower())


class TestHybridSearch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _make_project(self._tmp.name)
        self.rag = DA.StandaloneRAG(self._tmp.name, embedder=FakeEmbedder())
        self.rag.build_index()

    def tearDown(self):
        self._tmp.cleanup()

    def test_hybrid_mode_has_vectors(self):
        st = self.rag.stats()
        self.assertEqual(st["mode"], "hybrid")
        self.assertEqual(st["vectors"], st["chunks"])

    def test_query_finds_its_section(self):
        hits = self.rag.search("какой порт у шлюза?", top_k=3)
        self.assertTrue(hits)
        self.assertIn("шлюз", (hits[0]["section"] + hits[0]["text"]).lower())

    def test_wordforms_removed_by_stemming(self):
        """«тестов» находит «тесты» (общий префикс) — без морфологии."""
        hits = self.rag.search("запуск тестов бэкенда", top_k=3)
        self.assertTrue(any("Тесты" in h["section"] for h in hits))

    def test_stopword_query_gives_nothing(self):
        """Служебные слова («кто ты») не должны совпадать с кодом/прозой."""
        hits = [h for h in self.rag.search("кто ты?", top_k=5)
                if h.get("bm25")]
        self.assertEqual(hits, [])

    def test_zero_cosine_chunks_not_ranked_first(self):
        """Чанк без пересечения с запросом не «слабая близость»."""
        hits = self.rag.search("какой порт у шлюза?", top_k=5)
        self.assertGreater(hits[0]["cosine"], 0)

    def test_bm25_only_mode_when_embedder_missing(self):
        rag = DA.StandaloneRAG(self._tmp.name, embedder=FakeEmbedder(available=False))
        rag.cache_file = os.path.join(self._tmp.name, "rag2.json")
        st = rag.build_index()
        self.assertEqual(st["vectors"], 0)
        self.assertEqual(rag.stats()["mode"], "bm25")
        self.assertIn("fake-embed", rag.stats()["embed_note"])
        # эмбеддера нет — о теме говорят только лексема+BM25: «порт» в
        # маленьком тестовом корпусе редок, но именно он и решает
        hits = rag.search("шлюз порт 8110", top_k=3)
        self.assertTrue(hits)
        self.assertIn("шлюз", (hits[0]["section"] + hits[0]["text"]).lower())


class TestRelevanceGate(unittest.TestCase):
    """Отсечение: посторонний вопрос → пустой контекст, честный отказ."""

    def setUp(self):
        DA.C.disable()
        self._tmp = tempfile.TemporaryDirectory()
        _make_project(self._tmp.name)
        self.cli = DA.DevAssistantCLI(self._tmp.name)
        self.cli.rag = DA.StandaloneRAG(self._tmp.name, embedder=FakeEmbedder())
        self.cli.rag.build_index()
        self.llm = FakeLLM()
        self.cli.llm = self.llm
        self.cli.explorer = DA.ProjectExplorer(self._tmp.name)

    def tearDown(self):
        self.cli.mcp.close()
        self._tmp.cleanup()

    def test_on_topic_kept(self):
        hits, _ = self.cli._retrieve("какой порт у шлюза?")
        self.assertTrue(hits)

    def test_absent_term_rejected(self):
        """Термина нет в корпусе вовсе — вопрос не о проекте."""
        self.assertFalse(self.cli._retrieve("что такое борщ?")[0])
        self.assertFalse(self.cli._retrieve("как приготовить борщ?")[0])

    def test_small_talk_rejected(self):
        for q in ("кто ты?", "привет", "как дела?"):
            self.assertFalse(self.cli._retrieve(q)[0], q)

    def test_transliterated_duplicate_kept(self):
        """«джанго (django)» — один термин: кириллическая транслитерация
        уже есть в запросе латиницей, а не новый термин."""
        terms = self.cli._gate_terms("что такое джанго (django)?")
        self.assertIn("django", terms)
        self.assertNotIn("джанго", terms)

    def test_not_found_answer_does_not_call_llm(self):
        import io
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.cli.cmd_help("как приготовить борщ?")
        text = out.getvalue()
        self.assertIn("нет информации по запросу", text)
        self.assertNotIn("Ответ по контексту.", text)
        self.assertEqual(self.llm.last_user, "")

    def test_prompt_contains_only_topical_chunks(self):
        import io
        with patch("sys.stdout", io.StringIO()):
            self.cli.cmd_help("какой порт у шлюза?")
        prompt = self.llm.last_user
        self.assertIn("8110", prompt)
        self.assertNotIn("localStorage", prompt)      # тёмная тема не подмешана
        self.assertNotIn("pip install", prompt)       # установка не подмешана

    def test_follow_up_uses_previous_question(self):
        self.cli._remember("как устроен шлюз локальной LLM?", [])
        resolved = self.cli._resolve_follow_up("какой порт у него по умолчанию?")
        self.assertIn("шлюз", resolved)
        self.assertIn("порт", resolved)

    def test_follow_up_without_history_unchanged(self):
        self.assertEqual(self.cli._resolve_follow_up("какой порт у шлюза?"),
                         "какой порт у шлюза?")

    def test_follow_up_supplement_finds_missing_fact(self):
        """Уточнение добирает из тех же файлов фрагмент, где термин
        уточнения встречается БОЛЬШЕ раз, чем в общем разделе
        («порт» мимоходом в описании шлюза vs строка константы)."""
        # базовое описание шлюза живёт в README; строка константы —
        # отдельный чанк того же файла и в гибридный топ не попадает
        hits, dropped = self.cli._retrieve("шлюз локальной LLM")
        hits = [h for h in hits if h["file"] == "README.md"]
        self.assertTrue(hits)
        hits, supp = self.cli._supplement_follow_up(
            hits, "какой порт шлюза по умолчанию?")
        self.assertTrue(supp)
        self.assertTrue(any("8110" in h["text"] for h in supp))

    def test_english_hint_terms(self):
        """«порт» ↔ port: русский вопрос, английские ключи в коде."""
        terms = self.cli._hint_terms("какой порт у шлюза по умолчанию?")
        self.assertIn("port", terms)
        self.assertIn("limit", self.cli._hint_terms("какие лимиты у шлюза?"))

    def test_system_prompt_forbids_off_topic(self):
        for phrase in ("ТОЛЬКО по предоставленному контексту",
                       "этого нет", "не упоминай"):
            self.assertIn(phrase.lower(), DA.SYSTEM_PROMPT.lower())


if __name__ == "__main__":
    unittest.main()


class TestAssistantTokenUsage(unittest.TestCase):
    """Расход токенов в автономном ассистенте: числа от Ollama, не оценка."""

    class FakeUsageLLM(DA.LLMClient):
        def __init__(self, available=True):
            super().__init__()
            self._available = available
            self._model = "fake-model"

        def get_model(self):
            return self._model if self._available else None

        def context_window(self, model=None):
            return 8192

        def generate(self, system, user_prompt):
            if not self._available:
                return ""
            self.last_usage = {"model": "fake-model", "prompt_tokens": 2047,
                               "answer_tokens": 487, "total_tokens": 2534,
                               "context_window": 8192,
                               "remaining": 8192 - 2534,
                               "percent": round(2534 * 100 / 8192, 1)}
            return "Ответ."

    def setUp(self):
        DA.C.disable()
        self._tmp = tempfile.TemporaryDirectory()
        _make_project(self._tmp.name)
        self.cli = DA.DevAssistantCLI(self._tmp.name)
        self.cli.rag = DA.StandaloneRAG(self._tmp.name, embedder=FakeEmbedder())
        self.cli.rag.build_index()
        self.cli.llm = self.FakeUsageLLM()
        self.cli.explorer = DA.ProjectExplorer(self._tmp.name)

    def tearDown(self):
        self.cli.mcp.close()
        self._tmp.cleanup()

    def test_usage_line_printed_after_answer(self):
        import io
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.cli.cmd_help("какой порт у шлюза по умолчанию?")
        text = out.getvalue()
        self.assertIn("Токены: промпт 2047 + ответ 487 = 2534", text)
        self.assertIn("окно 8 192", text)
        self.assertIn(f"остаток {8192 - 2534}".replace("5658", "5 658"), text)

    def test_no_usage_line_without_llm_call(self):
        import io
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.cli.cmd_help("как приготовить борщ?")
        text = out.getvalue()
        self.assertNotIn("Токены:", text)   # гейт отсекает до вызова модели
        self.assertIn("нет информации по запросу", text)

    def test_client_stores_real_usage(self):
        client = self.FakeUsageLLM()
        client.generate("s", "u")
        self.assertEqual(client.last_usage["total_tokens"], 2534)
        self.assertEqual(client.last_usage["remaining"], 5658)
