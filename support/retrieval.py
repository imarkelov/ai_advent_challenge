"""RAG-ядро ассистента поддержки (день 33): гибридный поиск по FAQ/документации.

Только stdlib. Дешёвый, детерминированный и офлайновый поиск (демо-раздел
задания: «использовать RAG (FAQ, документация)»):

  - BM25 по токенам статьи (леммы не нужны: токены + инфлексия-подстроки);
  - «векторная» нога на хэш-эмбеддингах (char-3-граммы, как в базе знаний
    Студии — hashlib.md5, без сети);
  - фузия Reciprocal Rank (RRF);
  - порог релевантности `min_score` и честное «не знаю» при нуле совпадений.

Выход поиска: [{id, title, text, score, rerank_score, rank, source}] —
source = id статьи (для цитат [n] в ответе).
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

HASH_DIM = 256
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60
_MIN_TOKEN = 3
_STEM_MIN = 5   # общий префикс: «работает»/«работать»; 4 — ловит мусор
_STEM_SUB = 4   # основа внутри слова: «почт» ⊂ «почты», «вход» ⊂ «входить»
_ARTICLE_WEIGHT = 0.2
_TITLE_WEIGHT = 3.0
_TAGS_WEIGHT = 2.5
_BODY_WEIGHT = 1.0

# Русские служебные слова: в BM25 дают ложные совпадения («в», «как», «для»)
# — без стоп-списка «какой у вас офис в Москве» цеплялся за случайные статьи.
_RU_STOP = {
    "для", "или", "как", "что", "это", "все", "еще", "уже", "при", "без",
    "над", "под", "про", "чем", "так", "там", "тут", "его", "она", "они",
    "оно", "мне", "вас", "вам", "ваш", "наш", "нас", "мой", "моя", "мое",
    "был", "была", "были", "быть", "есть", "нет", "где", "когда", "почему",
    "зачем", "какой", "какая", "какие", "каких", "кого", "чего", "этот",
    "эта", "эти", "того", "этом", "если", "тоже", "также", "можно", "нужно",
    "очень", "пока", "только", "через", "после", "перед", "между", "весь",
    "вся", "всё", "свой", "своя", "них", "неё", "него", "ему", "ей", "ими",
}
# Латиница: короткие/служебные англо-токены — тоже шум.
_EN_STOP = {"the", "and", "for", "you", "your", "with", "this", "that",
            "not", "are", "was", "were", "can", "how", "why", "what", "when",
            "does", "did", "but", "from", "have", "has"}

# Мягкие основы «общего» языка поддержки: «работать», «проблема», «система»
# есть почти в каждой статье и не должны включать BM25-совпадение. Строгая
# форма — в таблице, инфлексии — по префиксу (см. _is_generic).
_GENERIC = {
    "работа", "работает", "рабочий", "рабочих", "работе", "проблема", "проблемы",
    "система", "систему", "системы", "системе", "нужно", "помоги", "помогите",
    "ошибка", "ошибку", "сервис", "сервиса", "клиент", "клиента", "пользователь",
    "приложение", "приложения", "сейчас", "делать", "делает", "должен",
}


def _is_generic(token: str) -> bool:
    """Служебное/общее для поддержки слово (точное или по 5-букв. префиксу)."""
    if token in _GENERIC:
        return True
    return any(token.startswith(g[:5]) for g in _GENERIC if len(g) >= 5)


def _stem_group(token: str) -> str:
    """Грубая основа для сравнения: первые 5 символов («работает» и
    «работать» -> «работ», «авторизация» -> «автор»)."""
    return token[:5]


def _tokenize(text: str) -> list[str]:
    """Токены: нижний регистр, [a-zа-я0-9]+, длина >= 3; ё -> е; стоп-слова
    (RU/EN) отбрасываются — иначе BM25 цепляется за «как/для/в»."""
    stop = _RU_STOP | _EN_STOP
    return [t for t in re.findall(r"[a-zа-я0-9]+",
                                  text.lower().replace("ё", "е"))
            if len(t) >= _MIN_TOKEN and t not in stop]


def _tokens_match(a: str, b: str) -> bool:
    """Word match with inflection tolerance.

    - identical tokens match;
    - a longer word starting with the first 4 chars of a shorter one is an
      inflection ("pochta"/"pochty") only if lengths differ OR the
      second-to-last char also matches ("rabotaet"/"rabotat", but NOT
      "avtorizaciya" ~ "avtomaticheski" - "avtor" is a coincidental prefix);
    - a shared prefix of >= 5 chars is a match ("rabotaet"/"rabotat").
    """
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if long_.startswith(short[:4]) and (
            len(short) < len(long_) or short[-2] == long_[-2]):
        return True
    if len(a) < _STEM_MIN or a[:_STEM_MIN] == b[:_STEM_MIN]:
        return a[:_STEM_MIN] == b[:_STEM_MIN]
    return False


def _hash_vector(text: str, dim: int = HASH_DIM) -> list[float]:
    """Хэш-эмбеддинг char-3-грамм (детерминированный, hashlib.md5)."""
    v = [0.0] * dim
    for i in range(len(text) - 2):
        gram = text[i:i + 3]
        b = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16) % dim
        v[b] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    if n > 0:
        v = [x / n for x in v]
    return v


def _cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _weighted_bm25(query_tokens: list[str], docs_tokens: list[list[str]],
                   docs_weights: list[list[float]]) -> list[float]:
    """BM25, где каждый токен документа весит по своему полю (title/tags/body).

    Инфлексия учитывается. Общий термин в K статьях ограничивается
    sqrt(K)-кратным вкладом, чтобы не доминировал над редким словом."""
    scores = [0.0] * len(docs_tokens)
    if not query_tokens or not docs_tokens:
        return scores
    doc_lens = [len(t) for t in docs_tokens]
    avgdl = sum(doc_lens) / len(doc_lens) or 1.0
    n = len(docs_tokens)
    df = {}
    for t in set(query_tokens):
        df[t] = sum(1 for d in docs_tokens if any(_tokens_match(t, dt) for dt in d))
    for q in dict.fromkeys(query_tokens):
        if df[q] == 0:
            continue
        idf = math.log(1.0 + (n - df[q] + 0.5) / (df[q] + 0.5))
        if df[q] >= 2:
            idf = idf / math.sqrt(df[q])
        for i, d in enumerate(docs_tokens):
            w = docs_weights[i]
            tf = sum(w[k] for k, dt in enumerate(d) if _tokens_match(q, dt))
            if tf == 0:
                continue
            dl = doc_lens[i]
            scores[i] += idf * (tf * (_BM25_K1 + 1)) / (
                tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
    return scores


def _rrf(rankings: list[list[int]]) -> dict[int, float]:
    fused: dict[int, float] = {}
    for ranking in rankings:
        for pos, idx in enumerate(ranking, 1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + pos)
    return fused


@dataclass
class Article:
    id: str
    title: str
    text: str
    tags: list

    @property
    def search_text(self) -> str:
        return " ".join([self.title, " ".join(self.tags), self.text])

    def fields(self) -> tuple[list[str], list[str], list[str]]:
        """Токены по полям: (title, tags, body) — разные веса в BM25."""
        return (_tokenize(self.title), _tokenize(" ".join(self.tags)),
                _tokenize(self.text))


class SupportKB:
    """Индекс статей поддержки + гибридный поиск."""

    def __init__(self, articles: list) -> None:
        self.articles = [
            Article(id=str(a["id"]), title=str(a.get("title") or a["id"]),
                    text=str(a["text"]),
                    tags=[str(t) for t in (a.get("tags") or [])])
            for a in articles
            if isinstance(a, dict) and a.get("id") and a.get("text")
        ]
        fields = [a.fields() for a in self.articles]
        self._tokens = [t + g + b for t, g, b in fields]
        self._weights = [[_TITLE_WEIGHT] * len(title)
                         + [_TAGS_WEIGHT] * len(tags)
                         + [_BODY_WEIGHT] * len(body)
                         for title, tags, body in fields]
        # Generic-термины («работает») не включают совпадение ни в одном поле.
        self._tokens = [[t for t in d if not _is_generic(t)]
                        for d in self._tokens]
        self._vectors = [_hash_vector(a.search_text.lower()) for a in self.articles]

    def __len__(self) -> int:
        return len(self.articles)

    def search(self, query: str, top_k: int = 3,
               min_score: float = 0.0, weights=None) -> dict:
        """Гибридный поиск. Возврат:
        {"query", "results": [...], "total", "no_match": bool}.

        Поле-взвешенный BM25 (title > tags > body) + векторная нога
        (хэш-эмбеддинги) как тай-брейк. `min_score` (0..1) — порог
        нормализованного скора (от лучшей статьи); при 0 не фильтруем.
        Нет лексических совпадений вовсе -> no_match=True (честное «не знаю»).
        `weights` — множители BM25-весов полей (по умолчанию title/tags/body);
        используется при подмешивании контекста тикета с меньшим весом.
        """
        q = (query or "").strip()
        if not q or not self.articles:
            return {"query": q, "results": [], "total": 0, "no_match": True}
        all_qt = _tokenize(q)
        # Generic-термины («работает», «проблема») не включают совпадение;
        # если после фильтра ничего не осталось — используем полный набор.
        qt = [t for t in all_qt if not _is_generic(t)]
        if not qt:
            qt = all_qt
        if weights is None:
            field_tokens, field_weights = self._tokens, self._weights
        else:
            factor = float(weights)
            field_tokens = self._tokens
            field_weights = [[w * factor for w in doc] for doc in self._weights]
        bm = _weighted_bm25(qt, field_tokens, field_weights)
        qv = _hash_vector(q.lower())
        vec = [_cosine(qv, v) for v in self._vectors]

        # Честное «не знаю»: хэш-вектор шумит (char-3-грамм у любых русских
        # слов много), поэтому векторная нога НЕ может вытащить статью без
        # лексического совпадения. Кандидаты — только статьи с BM25 > 0.
        candidates = [i for i in range(len(self.articles)) if bm[i] > 0]
        if not candidates:
            return {"query": q, "results": [], "total": 0, "no_match": True}
        sem_order = sorted(candidates, key=lambda i: (-vec[i], -bm[i], i))
        # RRF: BM25 — основная нога, вектор — тай-брейк внутри кандидатов.
        fused = _rrf([sorted(candidates, key=lambda i: (-bm[i], -vec[i], i)),
                      sem_order])
        # Итоговый скор — взвешенный BM25 (RRF решает порядок, BM25 —
        # значимость): иначе частое общее слово тащит мусорную статью.
        weighted = {i: fused[i] * (bm[i] + _ARTICLE_WEIGHT) for i in fused}
        best = max(weighted.values())
        ranked = sorted(candidates, key=lambda i: (-weighted[i], -bm[i], i))
        if min_score > 0:
            ranked = [i for i in ranked if weighted[i] >= min_score * best]
        results = []
        for rank, i in enumerate(ranked[:max(0, top_k)], 1):
            a = self.articles[i]
            results.append({
                "id": a.id, "title": a.title, "text": a.text,
                "score": round(weighted[i], 6),
                "norm_score": round(weighted[i] / best, 4) if best else 0.0,
                "bm25": round(bm[i], 4), "cosine": round(vec[i], 4),
                "rank": rank, "source": a.id,
            })
        return {"query": q, "results": results, "total": len(candidates),
                "no_match": not results}


def format_sources(results: list) -> str:
    """Список источников [n] id — title (для CLI)."""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['id']} — {r['title']}")
    return "\n".join(lines)


def extract_cited(results: list, answer: str) -> tuple[list, list]:
    """Какие источники процитированы в ответе ([1], [2]...) и какие нет.

    Возврат: (cited, uncited) — списки источников в порядке RAG-выдачи.
    Если маркеров нет — cited пуст (вызывающая сторона решает, что делать).
    """
    markers = {int(m) for m in re.findall(r"\[(\d+)\]", answer or "")}
    cited = [r for i, r in enumerate(results, 1) if i in markers]
    uncited = [r for i, r in enumerate(results, 1) if i not in markers]
    return cited, uncited
