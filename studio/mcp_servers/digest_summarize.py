"""MCP-сервер digest_summarize (день 20) — stdio, один тул: summarize.

Port из pipeline_tools.py дня 19, алгоритм без изменений:
детерминированная extractive-суммаризация (топ-N предложений по частоте
слов, RU+EN стоп-слова), без LLM. Возвращает summary (строки '• точка'),
points, input_chars, output_chars.

Только stdlib, внешних импортов нет.

Запуск: python studio/mcp_servers/digest_summarize.py
"""
import os
import re
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
from _mcp_base import run_server  # noqa: E402

STOPWORDS = frozenset((
    "и в на не что а как но это то по для с из к у о от за при бы уже "
    "так есть был была было были он она оно они мы вы я их его ее ее "
    "the a an and or but of to in on is are was were it that".split()))
MIN_WORD = 2

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[0-9a-zа-яё]+")


def _split_sentences(text: str) -> list:
    out = []
    for part in _SENT_RE.split(text.strip()):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _extractive_summary(text: str, max_points: int) -> list:
    """Детерминированная extractive-выжимка: топ-N предложений по частоте
    слов (стоп-слова исключены); при равенстве очков — исходный порядок."""
    sentences = _split_sentences(text)
    if not sentences:
        return []
    freq = {}
    for s in sentences:
        for w in _WORD_RE.findall(s.lower()):
            if len(w) >= MIN_WORD and w not in STOPWORDS:
                freq[w] = freq.get(w, 0) + 1
    scored = []
    for idx, s in enumerate(sentences):
        score = sum(freq.get(w, 0) for w in _WORD_RE.findall(s.lower())
                    if w in freq)
        scored.append((-score, idx, s))
    scored.sort()
    return [s for _neg, _i, s in scored[:max_points]]


def call(args: dict) -> tuple:
    text = (args or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return {"error": "summarize: аргумент 'text' (непустая строка) "
                         "обязателен"}, True
    mp = (args or {}).get("max_points")
    try:
        mp = int(mp) if mp is not None else 8
    except (TypeError, ValueError):
        mp = 8
    mp = max(1, min(mp, 20))
    points = _extractive_summary(text, mp)
    if not points:
        return {"error": "summarize: в тексте не найдено "
                         "ни одного предложения"}, True
    summary = "\n".join("• " + p for p in points)
    return {"summary": summary, "points": points,
            "input_chars": len(text),
            "output_chars": len(summary)}, False


TOOL = {
    "name": "summarize",
    "description": ("Детерминированная extractive-суммаризация: топ-N "
                    "предложений по частоте слов (RU+EN стоп-слова). "
                    "Возвращает summary (строки '• точка'), points, "
                    "input_chars, output_chars."),
    "input_schema": {"type": "object", "properties": {
        "text": {"type": "string",
                 "description": "Текст для суммаризации"},
        "max_points": {"type": "integer",
                       "description": "Число точек (дефолт 8, макс 20)"}},
        "required": ["text"]},
}


if __name__ == "__main__":
    run_server("digest_summarize", TOOL, call)
