# -*- coding: utf-8 -*-
"""Тесты дня 22: scripts/fetch_books.py (офлайн, сеть мокается).

Покрывают: каталог (5 книг), happy-path (utf-8), фолбэк декодирования
(cp1251), санитарный размер (<50KB — отбраковка → seed), цепочку
фолбэков (pinned → fallback-книга → seed), полный офлайн (seed-копия),
атомарную запись и exit 0 при любой ошибке.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

import fetch_books  # noqa: E402


def big_text(target=60 * 1024):
    """Русская проза >50KB для happy-path."""
    para = (
        "Война и мир — роман Льва Толстого. Князь Андрей ехал в поле, "
        "и над ним было высокое небо.\n\n"
    )
    out = "Л.Н. Толстой. Война и мир\n\n"
    while len(out) < target:
        out += para
    return out


def small_text(target=30 * 1024):
    """Русская проза <50KB — отвергается санитарным размером."""
    para = "Вишнёвый сад. Чехов. Раневская приехала из Парижа.\n\n"
    out = "А.П. Чехов. Вишнёвый сад\n\n"
    while len(out) < target:
        out += para
    return out


class FakeNet:
    """Подменяет fetch_books.http_get: маршрутизация по префиксу URL."""

    def __init__(self):
        self.routes = []  # (prefix, data) — data: bytes | Exception
        self.calls = []

    def add(self, prefix, data):
        self.routes.append((prefix, data))

    def __call__(self, url, timeout=60):
        self.calls.append(url)
        for prefix, data in self.routes:
            if url.startswith(prefix):
                if isinstance(data, Exception):
                    raise data
                return data
        raise AssertionError("unexpected url: " + url)


def ws_json(text):
    """Ответ wikisource parse API с готовым текстом."""
    return json.dumps({"parse": {"text": text}}).encode("utf-8")


@pytest.fixture
def fake_net(monkeypatch):
    net = FakeNet()
    monkeypatch.setattr(fetch_books, "http_get", net)
    return net


# ---------------------------------------------------------------- каталог

def test_catalog_has_five_books():
    assert len(fetch_books.CATALOG) == 5
    filenames = {e["filename"] for e in fetch_books.CATALOG}
    assert filenames == {
        "pushkin_oneygin.txt",
        "chekhov_cherry_orchard.txt",
        "tolstoy_war_peace.txt",
        "gogol_dead_souls.txt",
        "chekhov_lady_with_dog.txt",
    }
    pinned = [e for e in fetch_books.CATALOG if e["role"] == "pinned"]
    fallbacks = [e for e in fetch_books.CATALOG if e["role"] == "fallback"]
    assert len(pinned) == 3
    assert len(fallbacks) == 2
    for entry in fetch_books.CATALOG:
        assert entry["urls"], entry["filename"]
        if entry["fallback"]:
            assert entry["fallback"] in filenames
        assert entry["filename"] in fetch_books.SEED_MAP


def test_seed_corpus_committed():
    for seed in ("chekhov_chameleon.txt", "chekhov_horse_first.txt"):
        path = Path(fetch_books.SEED_DIR) / seed
        assert path.exists(), seed
        text = path.read_text(encoding="utf-8")
        assert "Чехов" in text
        assert len(text) > 1000


# ------------------------------------------------------------ happy path

def test_happy_utf8(fake_net, tmp_path):
    fake_net.add("http://az.lib.ru/p/pushkin_a_s/", big_text().encode("utf-8"))
    fake_net.add("http://", b"<html>skip</html>")  # прочие — мусор → фолбэк
    res = fetch_books.resolve_book(fetch_books.CATALOG[0], "")
    assert res["status"] == "ok"
    assert res["source"] == "http://az.lib.ru/p/pushkin_a_s/text_0170.shtml"
    assert "Война и мир" in res["text"]


def test_cp1251_decode_fallback(fake_net, tmp_path):
    """Сайт отдаёт cp1251 — декодер не должен оставить mojibake."""
    raw = big_text().encode("cp1251")
    fake_net.add("http://az.lib.ru/p/pushkin_a_s/", raw)
    res = fetch_books.resolve_book(fetch_books.CATALOG[0], "")
    assert res["status"] == "ok"
    assert "Война и мир" in res["text"]


def test_run_writes_all_files(fake_net, tmp_path):
    fake_net.add("http://", big_text().encode("utf-8"))
    fake_net.add("https://ru.wikisource", ws_json(big_text()))
    summary = fetch_books.run(str(tmp_path))
    assert summary["fetched"] == 5
    assert all(b["status"] in ("ok", "fallback") for b in summary["books"])
    for entry in fetch_books.CATALOG:
        f = tmp_path / entry["filename"]
        assert f.exists(), entry["filename"]
        assert f.stat().st_size > fetch_books.MIN_TEXT_CHARS
    assert not list(tmp_path.glob("*.tmp")), "atomic write must not leave tmp"


# --------------------------------------------------------- деградации

def test_too_small_rejected_then_seed(fake_net, tmp_path):
    """<50KB отбраковывается; pinned + fallback упали → seed-копия."""
    fake_net.add("http://", small_text().encode("utf-8"))
    fake_net.add("https://ru.wikisource", RuntimeError("net cut"))
    res = fetch_books.resolve_book(fetch_books.CATALOG[0], "")
    assert res["status"] == "seed"
    seed = Path(fetch_books.SEED_DIR) / "chekhov_chameleon.txt"
    assert res["source"] == str(seed)
    assert res["text"] == seed.read_text(encoding="utf-8")


def test_fallback_chain_pinned_to_fallback_book(fake_net):
    """Основная книга недоступна → берётся fallback-книга."""
    fake_net.add("http://az.lib.ru/p/pushkin_a_s/", RuntimeError("404"))
    fake_net.add("https://ru.wikisource", RuntimeError("net cut"))
    fake_net.add("http://az.lib.ru/g/gogolx_n_w/", big_text().encode("utf-8"))
    res = fetch_books.resolve_book(fetch_books.CATALOG[0], "")
    assert res["status"] == "fallback"
    assert res["source"] == "http://az.lib.ru/g/gogolx_n_w/text_0140.shtml"


def test_all_fail_net_cut_seeds(fake_net, tmp_path):
    """Полный офлайн: все 3 pinned-книги деградируют в seed."""
    fake_net.add("http://", RuntimeError("connection refused"))
    fake_net.add("https://ru.wikisource", RuntimeError("connection refused"))
    summary = fetch_books.run(str(tmp_path))
    assert summary["fetched"] == 0
    assert summary["seeded"] == 5
    assert summary["failed"] == 0
    for book in summary["books"]:
        assert book["status"] == "seed"
        f = tmp_path / book["file"]
        assert f.exists()
        assert "Чехов" in f.read_text(encoding="utf-8")


# ---------------------------------------------------------------- CLI

def test_list_prints_catalog(capsys):
    assert fetch_books.main(["--list"]) == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 5
    assert "pushkin_oneygin.txt" in out


def test_exit0_on_total_failure(fake_net, tmp_path, capsys):
    """exit 0 всегда — даже при фатальной ошибке run()."""
    def boom(out_dir):
        raise RuntimeError("disk exploded")

    import fetch_books as fb
    fb.http_get  # touch to satisfy linter on unused fixture paths
    saved = fb.run
    fb.run = boom
    try:
        assert fb.main(["--out", str(tmp_path)]) == 0
    finally:
        fb.run = saved
    summary = json.loads(capsys.readouterr().out)
    assert summary["error"]


def test_summary_json_shape(fake_net, tmp_path, capsys):
    fake_net.add("http://", big_text().encode("utf-8"))
    fake_net.add("https://ru.wikisource", ws_json(big_text()))
    assert fetch_books.main(["--out", str(tmp_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert set(summary) >= {"out_dir", "books", "fetched", "seeded", "failed"}
    assert summary["fetched"] == 5
    for book in summary["books"]:
        assert set(book) >= {"title", "file", "role", "status", "source", "size"}
