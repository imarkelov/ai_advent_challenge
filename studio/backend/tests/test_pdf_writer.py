"""День 19: pdf_writer (stdlib PDF, TTF, кириллица) — офлайн, в-процессе.

Проверяем структуру PDF-байт: хедер, вшитый шрифт (/FontFile2),
ToUnicode CMap, /CIDToGIDMap, корректные xref-офсеты, пагинацию,
детерминированный вывод и PdfError на отсутствующем шрифте.
Тесты, требующие шрифта, пропускаются (pytest.skip), если
find_default_font() не находит системный TTF."""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers")))

import pytest  # noqa: E402
import pdf_writer  # noqa: E402


def _font_or_skip():
    f = pdf_writer.find_default_font()
    if f is None:
        pytest.skip("системный TTF не найден (arial/segoe/tahoma)")
    return f


def _xref_entries(data):
    """Разбор xref: [(offset, gen, kind)] по порядку (индекс = номер объекта)."""
    # ^xref — якорь строки, чтобы не спутать с "startxref"
    m = re.search(rb"^xref\n", data, re.MULTILINE)
    assert m, "нет секции xref"
    header, _, remainder = data[m.end():].partition(b"\n")
    count = int(header.split()[1])
    entries = []
    for line in remainder.split(b"\n"):
        if len(entries) >= count:
            break
        line = line.rstrip(b"\r")
        if not line.strip():
            continue
        entries.append((int(line[:10]), int(line[11:16]), chr(line[17])))
    return entries


def test_pdf_header_and_font_objects():
    font = _font_or_skip()
    data = pdf_writer.text_to_pdf(
        "Привет, мир! Это кириллица в PDF.", font_path=font)
    assert data.startswith(b"%PDF-1.4")
    for token in (b"/FontFile2", b"/ToUnicode", b"/CIDToGIDMap",
                  b"/CIDFontType2", b"/Identity-H"):
        assert token in data, "не найден объект: " + token.decode()
    assert data.rstrip().endswith(b"%%EOF")


def test_xref_offsets_point_to_objects():
    font = _font_or_skip()
    data = pdf_writer.text_to_pdf("Текст для проверки xref.", font_path=font)
    entries = _xref_entries(data)
    assert entries, "xref пуст"
    checked = 0
    for i, (offset, _gen, kind) in enumerate(entries):
        if kind != "n":
            continue
        head = data[offset:offset + 24]
        m = re.match(rb"(\d+) 0 obj", head)
        assert m, "офсет %d не указывает на 'N 0 obj': %r" % (offset, head)
        assert int(m.group(1)) == i, ("офсет указывает на объект %s, "
                                      "ожидается %d" % (m.group(1), i))
        checked += 1
    assert checked >= 1, "нет используемых (n) записей xref"


def test_long_text_paginates():
    font = _font_or_skip()
    long_text = ("Длинное предложение для проверки переноса строк и "
                 "пагинации PDF. " * 200)
    data = pdf_writer.text_to_pdf(long_text, font_path=font)
    pages = len(re.findall(rb"/Type /Page\b", data))
    assert pages >= 2, "должно быть несколько страниц, получено %d" % pages


def test_missing_font_raises():
    with pytest.raises(pdf_writer.PdfError):
        pdf_writer.text_to_pdf("x", font_path="/nonexistent/none.ttf")


def test_deterministic_bytes():
    font = _font_or_skip()
    text = "Один и тот же текст. Кириллица и пунктуация. 123."
    a = pdf_writer.text_to_pdf(text, title="Заголовок", font_path=font)
    b = pdf_writer.text_to_pdf(text, title="Заголовок", font_path=font)
    assert a == b, "выход не детерминирован"


def test_title_larger_and_present():
    font = _font_or_skip()
    data = pdf_writer.text_to_pdf("Тело документа.", title="Большой заголовок",
                                  font_path=font)
    # title идёт первым при 14pt, тело при 11pt
    assert b"14 Tf" in data
    assert b"11 Tf" in data


def test_no_time_stamp_dates():
    font = _font_or_skip()
    data = pdf_writer.text_to_pdf("Текст.", font_path=font)
    assert b"CreationDate" not in data
    assert b"ModDate" not in data
