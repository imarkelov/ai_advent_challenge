"""BM25-lite индекс по Markdown-документам doc/kb, doc/tasks, doc/agents."""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

ROOTS = ("kb", "tasks", "agents")
_TOKEN_RE = re.compile(r"[\w\-\.]+", re.UNICODE)
STOP_WORDS = {"и", "в", "на", "с", "по", "для", "не", "что", "как", "из", "или"}


def _tokenize(text: str) -> list[str]:
    tokens = (t.lower() for t in _TOKEN_RE.findall(text))
    return [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]


class RagIndex:
    def __init__(self, doc_dir: str):
        self.doc_dir = Path(doc_dir)
        self._docs: dict[str, dict] = {}
        self._df: dict[str, int] = {}
        self._avg_len = 0.0
        self._updated_at = ""

    def _files(self) -> list[Path]:
        paths: list[Path] = []
        for root in ROOTS:
            paths.extend((self.doc_dir / root).rglob("*.md"))
        return sorted(paths)

    def reindex(self) -> int:
        self._docs.clear()
        self._df.clear()
        total_len = 0
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            tokens = _tokenize(text)
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            rel = path.relative_to(self.doc_dir).as_posix()
            self._docs[rel] = {"tf": tf, "len": len(tokens), "text": text,
                               "mtime": path.stat().st_mtime}
            for token in tf:
                self._df[token] = self._df.get(token, 0) + 1
            total_len += len(tokens)
        self._avg_len = (total_len / len(self._docs)) if self._docs else 0.0
        self._updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return len(self._docs)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        tokens = _tokenize(query)
        if not tokens or not self._docs:
            return []
        n = len(self._docs)
        k1, b = 1.5, 0.75
        scored: list[tuple[float, str]] = []
        for rel, doc in self._docs.items():
            score = 0.0
            for token in tokens:
                df = self._df.get(token, 0)
                tf = doc["tf"].get(token, 0)
                if not df or not tf:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                norm = 1 - b + b * (doc["len"] / (self._avg_len or 1))
                score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
            if score > 0:
                scored.append((score, rel))
        scored.sort(reverse=True)
        return [
            {"path": rel, "score": round(score, 3),
             "snippet": self._snippet(self._docs[rel]["text"], tokens)}
            for score, rel in scored[:limit]
        ]

    @staticmethod
    def _snippet(text: str, tokens: list[str], width: int = 180) -> str:
        lowered = text.lower()
        pos = -1
        for token in tokens:
            pos = lowered.find(token)
            if pos >= 0:
                break
        if pos < 0:
            return text[:width].replace("\n", " ")
        start = max(0, pos - width // 3)
        return text[start:start + width].replace("\n", " ").strip()

    def stats(self) -> dict:
        return {"documents": len(self._docs), "updated_at": self._updated_at}

    def stale(self, rel_or_abs: str) -> bool:
        path = Path(rel_or_abs)
        if path.is_absolute():
            try:
                rel = path.relative_to(self.doc_dir).as_posix()
            except ValueError:
                return True
        else:
            rel = path.as_posix()
        doc = self._docs.get(rel)
        if doc is None:
            return True
        full = self.doc_dir / rel
        return (not full.exists()) or full.stat().st_mtime > doc["mtime"]
