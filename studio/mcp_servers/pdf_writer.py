"""PDF-райтер (день 19): текст (кириллица) -> PDF 1.4, ТОЛЬКО stdlib.

Модуль дня 19, используется MCP-сервером pipeline_tools.py (тул
saveToFile, format=pdf). Без внешних зависимостей: TTF разбирается
struct'ом, шрифт вшивается целиком (raw-байты TTF как /FontFile2),
текст кодируется 16-битными GID'ами (Type0 / Identity-H), кириллица
и любой Unicode — через cmap (форматы 4 и 12) + ToUnicode CMap
(begincidrange: GID -> Unicode).

Публичный API:
    class PdfError(Exception)
    find_default_font() -> str | None
    text_to_pdf(text, title="", font_path=None) -> bytes

Особенности:
- A4 595x842pt, поля 72pt, 11pt, межстрочный 1.45, перенос по ширине
  hmtx, пагинация (~43 строки/страница); первый блок — title (14pt).
- Детерминированный вывод: без CreationDate/ModDate — один и тот же
  вход -> побайтовые одинаковые PDF.
- Буква вне cmap -> GID 0 (notdef).
"""
import os
import re
import struct

PAGE_W, PAGE_H = 595, 842
MARGIN = 72
BODY_SIZE = 11
TITLE_SIZE = 14
LEADING = 1.45

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\tahoma.ttf",
)


class PdfError(Exception):
    """Ошибка генерации PDF (нет шрифта, битый TTF и т.п.)."""


def find_default_font():
    """Шрифт: env PIPELINE_FONT_PATH (если задан — обязателен, .ttf)
    -> первый существующий из FONT_CANDIDATES -> None."""
    env = os.environ.get("PIPELINE_FONT_PATH")
    if env:
        if os.path.isfile(env) and env.lower().endswith(".ttf"):
            return env
        raise PdfError("PIPELINE_FONT_PATH задан, но файл не найден "
                       "или не .ttf: " + env)
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


# ---------- разбор TTF (struct) ----------

def _tables(data):
    """Каталог таблиц: {tag: (offset, length)}. BDF/TTCF -> PdfError."""
    if len(data) < 12:
        raise PdfError("Файл короче, чем TTF")
    if data[:4] not in (b"\x00\x01\x00\x00", b"true"):
        raise PdfError("Не TTF (bad sfntVersion) — нужен обычный .ttf")
    num = struct.unpack_from(">H", data, 4)[0]
    out = {}
    for i in range(num):
        off = 12 + i * 16
        tag = data[off:off + 4].decode("latin-1")
        o, l = struct.unpack_from(">II", data, off + 8)
        out[tag] = (o, l)
    return out


def _cmap_fmt4(data, o):
    """Cmap format 4 (16-бит) -> {char: gid}."""
    seg2 = struct.unpack_from(">H", data, o + 6)[0]
    seg = seg2 // 2
    p = o + 14
    ends = struct.unpack_from(">%dH" % seg, data, p)
    p += seg * 2 + 2  # reservedPad
    starts = struct.unpack_from(">%dH" % seg, data, p)
    p += seg * 2
    deltas = struct.unpack_from(">%dh" % seg, data, p)
    p += seg * 2
    rofs = struct.unpack_from(">%dH" % seg, data, p)
    garr = p + seg * 2
    m = {}
    for i in range(seg):
        s, e = starts[i], ends[i]
        if e == 0xFFFF or s > e:
            continue
        d, ro = deltas[i], rofs[i]
        for c in range(s, e + 1):
            if ro == 0:
                gid = (c + d) & 0xFFFF
            else:
                idx = ro // 2 + i + (c - s)
                if garr + idx * 2 + 2 > len(data):
                    continue
                gid = (struct.unpack_from(">H", data, garr + idx * 2)[0]
                       + d) & 0xFFFF
            if gid:
                m.setdefault(c, gid)
    return m


def _cmap_fmt12(data, o):
    """Cmap format 12 (32-бит) -> {char: gid}."""
    num_sub = struct.unpack_from(">I", data, o + 8)[0]
    keys = o + 12
    idso = keys + num_sub * 12
    m = {}
    for i in range(num_sub):
        s, e, d = struct.unpack_from(">III", data, keys + i * 12)
        idoff = struct.unpack_from(">I", data, idso + i * 4)[0]
        if s > e:
            continue
        for c in range(s, e + 1):
            gid = (c + d) & 0xFFFF
            if gid == 0 and idoff:
                pos = o + idoff + (c - s) * 2
                if pos + 2 <= len(data):
                    gid = struct.unpack_from(">H", data, pos)[0]
            if gid:
                m.setdefault(c, gid)
    return m


def _parse_cmap(data, o):
    """Cmap-таблица: приоритет (3,10) fmt12 -> (3,1) fmt4 -> (0,*)."""
    n = struct.unpack_from(">H", data, o + 2)[0]
    best = None
    for i in range(n):
        plat, enc, so = struct.unpack_from(">HHI", data, o + 4 + i * 8)
        if (plat, enc) == (3, 10):
            prio = 0
        elif (plat, enc) == (3, 1):
            prio = 1
        elif plat == 0:
            prio = 2
        else:
            prio = 3
        sub = o + so
        if sub + 2 > len(data):
            continue
        fmt = struct.unpack_from(">H", data, sub)[0]
        m = None
        if fmt == 12:
            m = _cmap_fmt12(data, sub)
        elif fmt == 4:
            m = _cmap_fmt4(data, sub)
        if m and (best is None or prio < best[0]):
            best = (prio, m)
    if not best:
        raise PdfError("cmap: нет пригодной подтаблицы (fmt 4/12)")
    return best[1]


def _parse_name(data, o):
    """name-таблица -> (family, subfamily) или (None, None)."""
    count, soff = struct.unpack_from(">HH", data, o + 2)
    recs = []
    for i in range(count):
        p = o + 6 + i * 12
        try:
            nid, plat, enc, _lang, ln, ro = struct.unpack_from(
                ">HHHHHH", data, p)
        except struct.error:
            return (None, None)
        recs.append((0 if plat == 3 else 1, nid, o + soff + ro, ln))
    names = {}
    for _prio, nid, so, ln in sorted(recs):
        if nid not in (1, 2) or nid in names or not ln:
            continue
        raw = data[so:so + ln]
        try:
            txt = raw.decode("utf-16-be", "ignore")
            if not txt.strip():
                txt = raw.decode("latin-1", "ignore")
        except Exception:
            continue
        txt = txt.strip()
        if txt:
            names[nid] = txt
    return (names.get(1), names.get(2))


def _parse_ttf(data):
    """Весь TTF -> dict: upm, ascent, descent, bbox, num_glyphs, widths,
    gid_map {char: gid}, gid_to_char {gid: char}, family, subfamily."""
    t = _tables(data)
    for req in ("head", "hhea", "maxp", "hmtx", "cmap"):
        if req not in t:
            raise PdfError("TTF: нет таблицы " + req)
    ho, _ = t["head"]
    upm = struct.unpack_from(">H", data, ho + 18)[0]
    if not upm:
        raise PdfError("TTF: unitsPerEm = 0")
    hmo, _ = t["hhea"]
    asc = struct.unpack_from(">H", data, hmo + 36)[0]
    desc = -struct.unpack_from(">H", data, hmo + 38)[0]
    num_h = struct.unpack_from(">H", data, hmo + 14)[0]
    xo, yo, xi, yi = struct.unpack_from(">hhhh", data, hmo + 4)
    mo, _ = t["maxp"]
    num_glyphs = struct.unpack_from(">H", data, mo + 4)[0]
    xo, _ = t["hmtx"]
    widths = [0] * num_glyphs
    last = 0
    for i in range(num_glyphs):
        if i < num_h:
            adv = struct.unpack_from(">H", data, xo + i * 4)[0]
            last = adv
        else:
            adv = last
        widths[i] = adv
    co, _ = t["cmap"]
    gid_map = _parse_cmap(data, co)
    if not gid_map:
        raise PdfError("cmap пуст — шрифт не отображает текст")
    gid_to_char = {}
    for cp in sorted(gid_map):
        gid_to_char.setdefault(gid_map[cp], cp)
    family = subfamily = None
    if "name" in t:
        no, _ = t["name"]
        family, subfamily = _parse_name(data, no)
    return {"upm": upm, "ascent": asc, "descent": desc,
            "bbox": (xo, yo, xi, yi), "num_glyphs": num_glyphs,
            "widths": widths, "gid_map": gid_map,
            "gid_to_char": gid_to_char, "family": family,
            "subfamily": subfamily}


def _font_base_name(family, subfamily):
    raw = (family or "") + ("-" + subfamily if subfamily else "")
    clean = re.sub(r"[^A-Za-z0-9+\$.-]", "", raw)
    return clean or "EmbeddedFont"


# ---------- вёрстка ----------

def _wrap(text, char_w, max_w):
    """Жадный перенос: \n = жёсткий, пробелы = границы слов; слово шире
    строки рвём посимвольно. char_w — ширины символов, как в text."""
    lines = []
    cur = []
    cur_w = 0.0
    space_w = 0.0
    for idx, ch in enumerate(text):
        if ch == " ":
            space_w = char_w[idx]
            break
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in " \n":
            if ch == "\n" and cur:
                lines.append(" ".join(cur))
                cur, cur_w = [], 0.0
            i += 1
            continue
        j = i
        while j < n and text[j] not in " \n":
            j += 1
        word = text[i:j]
        ww = sum(char_w[i:j])
        if ww > max_w:  # слово шире строки — рвём
            if cur:
                lines.append(" ".join(cur))
                cur, cur_w = [], 0.0
            piece, pw = "", 0.0
            for t in range(i, j):
                if piece and pw + char_w[t] > max_w:
                    lines.append(piece)
                    piece, pw = "", 0.0
                piece += text[t]
                pw += char_w[t]
            if piece:
                cur, cur_w = [piece], pw
            i = j
            continue
        need = ww if not cur else cur_w + space_w + ww
        if need > max_w:
            lines.append(" ".join(cur))
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w = need
        i = j
    if cur:
        lines.append(" ".join(cur))
    return lines


def _char_widths(text, gid_map, widths, k):
    """Ширина каждого символа text в пунктах (коэфф k = size/upm)."""
    out = []
    for c in text:
        g = gid_map.get(ord(c), 0)
        if g >= len(widths):
            g = 0
        out.append(widths[g] * k)
    return out


# ---------- PDF-объекты ----------

def _to_unicode_stream(gid_to_char, num_glyphs):
    lines = []
    i = 0
    while i < num_glyphs:
        cp = gid_to_char.get(i)
        if cp is None:
            i += 1
            continue
        j = i
        while (j + 1 < num_glyphs
               and gid_to_char.get(j + 1) == cp + (j + 1 - i)):
            j += 1
        lines.append("<%04X> <%04X> <%04X>" % (i, j, cp))
        i = j + 1
    if not lines:
        raise PdfError("ToUnicode: нет сопоставленных глифов")
    body = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        "%d begincidrange\n%s\nendcidrange\n"
        "endcmap\nCMapName currentdict /CMap def\nend\nend\n"
        % (len(lines), "\n".join(lines)))
    return body.encode("ascii")


def _content_stream(page, gid_map, num_glyphs):
    ops = []
    y = PAGE_H - MARGIN
    for size, line in page:
        gids = []
        for c in line:
            g = gid_map.get(ord(c), 0)
            if g >= num_glyphs:
                g = 0
            gids.append(g)
        hexs = "".join("%04X" % g for g in gids)
        ops.append("BT /F1 %d Tf %d %d Td <%s> Tj ET"
                   % (size, MARGIN, int(y - size), hexs))
        y -= size * LEADING
    return ("\n".join(ops)).encode("ascii")


def text_to_pdf(text, title="", font_path=None):
    """Текст (+опц. title) -> bytes валидного PDF 1.4 (A4, кириллица).
    font_path: явный путь; иначе find_default_font(). PdfError — нет
    шрифта / битый TTF."""
    fp = font_path or find_default_font()
    if not fp:
        raise PdfError("Шрифт не найден: задайте PIPELINE_FONT_PATH "
                       "(.ttf) — системные Arial/Segoe/Tahoma тоже "
                       "отсутствуют")
    try:
        with open(fp, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise PdfError("Не удалось прочитать шрифт " + fp + ": "
                       + str(e)) from None
    ttf = _parse_ttf(raw)
    widths, gid_map = ttf["widths"], ttf["gid_map"]
    upm, ng = ttf["upm"], ttf["num_glyphs"]

    max_w = PAGE_W - 2 * MARGIN
    title_lines = (_wrap(title, _char_widths(title, gid_map, widths,
                                             TITLE_SIZE / upm), max_w)
                   if title else [])
    body_lines = _wrap(text or "",
                       _char_widths(text or "", gid_map, widths,
                                    BODY_SIZE / upm), max_w)
    seq = ([(TITLE_SIZE, l) for l in title_lines]
           + [(BODY_SIZE, l) for l in body_lines])
    pages, cur, y = [], [], PAGE_H - MARGIN
    for size, line in seq:
        if cur and y - size < MARGIN:
            pages.append(cur)
            cur, y = [], PAGE_H - MARGIN
        cur.append((size, line))
        y -= size * LEADING
    if cur:
        pages.append(cur)
    if not pages:
        pages = [[]]

    base = _font_base_name(ttf["family"], ttf["subfamily"])
    dw = widths[0] or 1000
    w_parts = ["%d %d" % (g, w) for g, w in enumerate(widths) if w != dw]
    x0, y0, x1, y1 = ttf["bbox"]

    n = len(pages)
    kids = " ".join("%d 0 R" % (8 + i * 2) for i in range(n))
    objs = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (2, b"<< /Type /Pages /Kids [" + kids.encode("ascii")
         + b"] /Count " + str(n).encode("ascii") + b" >>"),
        (3, b"<< /Type /Font /Subtype /Type0 /BaseFont /"
         + base.encode("ascii")
         + b" /Encoding /Identity-H /DescendantFonts [4 0 R] "
           b"/ToUnicode 7 0 R >>"),
        (4, b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /"
         + base.encode("ascii")
         + b" /CIDSystemInfo << /Registry (Adobe) /Ordering "
           b"(Identity) /Supplement 0 >> /CIDToGIDMap /Identity "
           b"/FontDescriptor 5 0 R /DW "
         + str(dw).encode("ascii")
         + b" /W [" + " ".join(w_parts).encode("ascii") + b"] >>"),
        (5, b"<< /Type /FontDescriptor /FontName /" + base.encode("ascii")
         + b" /Flags 32 /FontBBox ["
         + b" ".join(str(v).encode("ascii") for v in (x0, y0, x1, y1))
         + b"] /ItalicAngle 0 /Ascent " + str(ttf["ascent"]).encode()
         + b" /Descent " + str(ttf["descent"]).encode()
         + b" /CapHeight " + str(ttf["ascent"]).encode()
         + b" /StemV 80 /MissingWidth " + str(widths[0]).encode()
         + b" /FontFile2 6 0 R >>"),
        (6, b"<< /Length " + str(len(raw)).encode("ascii")
         + b" >>\nstream\n" + raw + b"\nendstream"),
        (7, b"<< /Length "
         + str(len(_to_unicode_stream(ttf["gid_to_char"], ng))).encode()
         + b" >>\nstream\n"
         + _to_unicode_stream(ttf["gid_to_char"], ng) + b"\nendstream"),
    ]
    for i in range(n):
        num = 8 + i * 2
        res = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
               b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
               % (num + 1))
        objs.append((num, res))
        objs.append((num + 1,
                     b"<< /Length "
                     + str(len(_content_stream(pages[i], gid_map, ng)))
                     .encode("ascii") + b" >>\nstream\n"
                     + _content_stream(pages[i], gid_map, ng)
                     + b"\nendstream"))

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in objs:
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
    last = len(objs)
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (last + 1)
    out += b"0000000000 65535 f \n"
    for i in range(1, last + 1):
        out += b"%010d 00000 n \n" % offsets[i]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n" % (last + 1)
    out += str(xref_pos).encode("ascii") + b"\n%%EOF\n"
    return bytes(out)
