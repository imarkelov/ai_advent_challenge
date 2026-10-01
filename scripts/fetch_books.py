#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""День 22 (RAG): загрузчик книг в корпус базы знаний.

Скачивает 3 основные книги (Пушкин «Евгений Онегин», Чехов «Вишнёвый сад»,
Толстой «Война и мир») из открытых источников (lib.ru / az.lib.ru),
2 запасные книги (Гоголь «Мёртвые души», Чехов «Дама с собачкой») —
кандидаты-фолбэки. Если сеть недоступна или все источники упали,
копирует committed seed-корпус из
``studio/backend/tests/fixtures/seed_corpus/``.

Только stdlib (urllib/json/os/sys/argparse/re). Без requests/httpx/bs4.

CLI::

    python scripts/fetch_books.py                  # загрузка в data/kb/uploads
    python scripts/fetch_books.py --out DIR        # другой каталог
    python scripts/fetch_books.py --list           # показать каталог книг

Печатает JSON-сводку; exit code 0 всегда (даже при полной неудаче —
тогда сработал seed-фолбэк).
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html import unescape

# Windows-консоль (cp1251) не должна ронять печать кириллицы/эмодзи.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "data", "kb", "uploads")
SEED_DIR = os.path.join(
    REPO, "studio", "backend", "tests", "fixtures", "seed_corpus"
)

USER_AGENT = "day22-fetch-books/1.0 (educational; stdlib urllib)"
HTTP_TIMEOUT = 60
# Санитарный размер текста книги (seed-копии от проверки освобождены).
MIN_TEXT_CHARS = 50 * 1024

WIKISOURCE_API = "https://ru.wikisource.org/w/api.php"

# Каталог: 3 «pinned» книги (загружаются всегда) + 2 «fallback» книги
# (используются, если основная книга недоступна). urls — кандидаты в порядке
# приоритета; последним кандидатом у каждой книги — wikisource parse API.
CATALOG = [
    {
        "title": "А.С. Пушкин — Евгений Онегин",
        "filename": "pushkin_oneygin.txt",
        "role": "pinned",
        "fallback": "gogol_dead_souls.txt",
        "urls": [
            "http://az.lib.ru/p/pushkin_a_s/text_0170.shtml",
            "wikisource:Евгений Онегин (Пушкин)",
        ],
    },
    {
        "title": "А.П. Чехов — Вишнёвый сад",
        "filename": "chekhov_cherry_orchard.txt",
        "role": "pinned",
        "fallback": "chekhov_lady_with_dog.txt",
        "urls": [
            "http://lib.ru/LITRA/CHEHOW/sad.txt",
            "wikisource:Вишнёвый сад (Чехов)",
        ],
    },
    {
        "title": "Л.Н. Толстой — Война и мир (том 1)",
        "filename": "tolstoy_war_peace.txt",
        "role": "pinned",
        "fallback": "gogol_dead_souls.txt",
        "urls": [
            "http://az.lib.ru/t/tolstoj_lew_nikolaewich/text_0040.shtml",
            "wikisource:Война и мир (Толстой)",
        ],
    },
    {
        "title": "Н.В. Гоголь — Мёртвые души",
        "filename": "gogol_dead_souls.txt",
        "role": "fallback",
        "fallback": None,
        "urls": [
            "http://az.lib.ru/g/gogolx_n_w/text_0140.shtml",
            "wikisource:Мёртвые души (Гоголь)",
        ],
    },
    {
        "title": "А.П. Чехов — Дама с собачкой",
        "filename": "chekhov_lady_with_dog.txt",
        "role": "fallback",
        "fallback": None,
        "urls": [
            "wikisource:Дама с собачкой (Чехов)",
        ],
    },
]

# Слот -> seed-файл (используется, когда сеть недоступна).
SEED_MAP = {
    "pushkin_oneygin.txt": "chekhov_chameleon.txt",
    "chekhov_cherry_orchard.txt": "chekhov_horse_first.txt",
    "tolstoy_war_peace.txt": "chekhov_chameleon.txt",
    "gogol_dead_souls.txt": "chekhov_horse_first.txt",
    "chekhov_lady_with_dog.txt": "chekhov_horse_first.txt",
}

# Частые русские служебные слова — маркер «настоящего» текста (отличает
# корректную кодировку от mojibake: cp1251-байты, прочитанные как koi8-r,
# дают « й », « ен », но не « и »/« не »).
COMMON_WORDS = [" и ", " не ", " на ", " что "]


def http_get(url, timeout=HTTP_TIMEOUT):
    """GET url -> bytes. Модульная функция — мокается в тестах."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def best_decode(raw):
    """bytes -> str: utf-8 (strict), иначе koi8-r/cp1251 — кто даёт больше
    русских служебных слов (меньше mojibake)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    probe = raw[:200000]
    best, best_score = None, -1
    for enc in ("koi8-r", "cp1251"):
        text = raw.decode(enc, errors="replace")
        score = sum(probe.decode(enc, errors="replace").count(w)
                    for w in COMMON_WORDS)
        if score > best_score:
            best, best_score = text, score
    return best


FOOTER_MARK = "<pre><hr noshade><small>"


def strip_html(text):
    """Убрать script/style/теги, развернуть entity."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text)


def raw_to_prose(raw):
    """bytes HTTP-ответа -> чистый текст книги (без html-обвязки)."""
    text = best_decode(raw)
    i = text.find("</form>")
    j = text.find(FOOTER_MARK)
    if i != -1 and j != -1 and j > i:
        text = text[i + len("</form>"):j]
    return strip_html(text).strip()


def looks_like_prose(text):
    """Валидация: кириллица, служебные слова, без html-остатков."""
    if not text:
        return False
    low = text.lower()
    if "<html" in low or "<!doctype" in low or "<body" in low:
        return False
    cyr = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    if cyr / max(len(text), 1) < 0.3:
        return False
    return any(w in text for w in COMMON_WORDS)


def wikisource_text(title, timeout=HTTP_TIMEOUT):
    """Полный текст страницы wikisource через parse API -> чистый текст."""
    url = (
        WIKISOURCE_API + "?action=parse&prop=text&format=json"
        "&formatversion=2&redirects=1&page=" + urllib.parse.quote(title)
    )
    data = json.loads(http_get(url, timeout=timeout).decode("utf-8"))
    if "error" in data:
        raise ValueError("wikisource: " + data["error"].get("info", "error"))
    return strip_html(data["parse"]["text"]).strip()


def fetch_from_urls(entry):
    """Перебрать кандидатов книги; вернуть (text, source) или (None, причины)."""
    notes = []
    for url in entry["urls"]:
        try:
            if url.startswith("wikisource:"):
                text = wikisource_text(url.split(":", 1)[1])
                source = url
            else:
                text = raw_to_prose(http_get(url))
                source = url
            if looks_like_prose(text):
                if len(text) >= MIN_TEXT_CHARS:
                    return text, source
                notes.append("%s: too small (%d chars)" % (source, len(text)))
            else:
                notes.append("%s: not prose" % source)
        except Exception as e:  # noqa: BLE001 — сеть/парсинг, слот деградирует
            notes.append("%s: %s" % (url, e))
    return None, "; ".join(notes) or "no urls"


def by_filename(filename):
    for entry in CATALOG:
        if entry["filename"] == filename:
            return entry
    return None


def seed_source(filename):
    """Путь к seed-файлу для слота (или None)."""
    name = SEED_MAP.get(filename)
    if not name:
        return None
    path = os.path.join(SEED_DIR, name)
    return path if os.path.exists(path) else None


def atomic_write(path, data):
    """Атомарная запись (tmp в том же каталоге -> os.replace)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(tmp, mode, **kwargs) as f:
        f.write(data)
    os.replace(tmp, path)


def resolve_book(entry, report):
    """Достать текст для слота: свои urls -> fallback-книга -> seed."""
    filename = entry["filename"]
    text, source, note = None, None, ""

    text, src = fetch_from_urls(entry)
    if text is not None:
        return {"status": "ok", "text": text, "source": src, "note": ""}

    fb = entry.get("fallback")
    if fb:
        fb_entry = by_filename(fb)
        if fb_entry:
            text, src = fetch_from_urls(fb_entry)
            if text is not None:
                return {
                    "status": "fallback",
                    "text": text,
                    "source": src,
                    "note": "primary failed: " + src_note(report),
                }

    seed = seed_source(filename)
    if seed:
        with open(seed, "r", encoding="utf-8") as f:
            return {
                "status": "seed",
                "text": f.read(),
                "source": seed,
                "note": "network/size failure",
            }

    return {"status": "failed", "text": None, "source": None, "note": src_note(report)}


def src_note(report):
    return report if isinstance(report, str) else ""


def run(out_dir):
    books = []
    for entry in CATALOG:
        res = resolve_book(entry, "")
        size = 0
        if res["text"] is not None:
            atomic_write(
                os.path.join(out_dir, entry["filename"]),
                res["text"].encode("utf-8"),
            )
            size = len(res["text"].encode("utf-8"))
        books.append({
            "title": entry["title"],
            "file": entry["filename"],
            "role": entry["role"],
            "status": res["status"],
            "source": res["source"],
            "size": size,
        })
        print("[fetch] %-28s %s (%d bytes, %s)" % (
            entry["filename"], res["status"], size, res["source"]), file=sys.stderr)
    return {
        "out_dir": out_dir,
        "books": books,
        "fetched": sum(1 for b in books if b["status"] in ("ok", "fallback")),
        "seeded": sum(1 for b in books if b["status"] == "seed"),
        "failed": sum(1 for b in books if b["status"] == "failed"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="День 22: загрузчик книг для БЗ")
    parser.add_argument("--out", default=DEFAULT_OUT, help="каталог выгрузки")
    parser.add_argument("--list", action="store_true", help="показать каталог книг")
    args = parser.parse_args(argv)

    if args.list:
        for entry in CATALOG:
            print("%-28s %-8s %s" % (entry["filename"], entry["role"], entry["title"]))
        return 0

    try:
        summary = run(args.out)
    except Exception as e:  # noqa: BLE001 — exit 0 всегда, даже при фатальной ошибке
        print("[fetch] fatal: %r" % e, file=sys.stderr)
        summary = {
            "out_dir": args.out,
            "books": [],
            "fetched": 0,
            "seeded": 0,
            "failed": 0,
            "error": repr(e),
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0  # exit 0 всегда: seed-фолбэк — штатная деградация


if __name__ == "__main__":
    sys.exit(main())
