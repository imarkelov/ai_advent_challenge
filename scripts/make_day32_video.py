# -*- coding: utf-8 -*-
"""Демо-видео дня 32: автоматическое AI-ревью PR — кадры из реального вывода.

Скрипт НЕ рисует «красивую картинку»: он запускает настоящий CLI-пайплайн
(`pr-review check` → `index` → `pr-review run` на живой Ollama) в сборном
git-репозитории и превращает фактический stdout в видео-запись терминала.
`git log`/`git diff` для кадров тоже выполняются по-настоящему.

Записи сценария (озвучка + субтитры + паузы) — `scripts/day32_video_script.json`,
голос — macOS `say` (ru_RU), кадры — Pillow, монтаж — ffmpeg.

Запуск:
    .venv/bin/python scripts/make_day32_video.py \
        --script scripts/day32_video_script.json \
        --out ~/Desktop/ai_advent_day32_video/day32_ai_pr_review.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "studio", "backend")
CLI = os.path.join(BACKEND, "cli.py")
PY = sys.executable


def _ffmpeg() -> str:
    """ffmpeg из PATH, иначе бинарь python-пакета imageio-ffmpeg.

    Homebrew на этой машине ставить не даёт (права в /opt/homebrew), а
    перекодировать кадры нужен только ffmpeg: imageio-ffmpeg носит его
    в site-packages проекта.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:                          # noqa: BLE001
        raise SystemExit(
            "ffmpeg не найден: установите его или выполните "
            "`.venv/bin/pip install imageio-ffmpeg`") from e


FFMPEG = _ffmpeg()

W, H = 1600, 1000
MARGIN_X = 48
TITLE_H = 66
TEXT_TOP = TITLE_H + 26
TEXT_BOTTOM = H - 40

FONT_REG = "/System/Library/Fonts/SFNS.ttf"
FONT_MONO = "/System/Library/Fonts/Menlo.ttc"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

BG = (22, 24, 31)
BAR = (36, 40, 50)
FG = (212, 216, 222)
DIM = (128, 136, 150)
ACCENT = (110, 190, 255)
OK = (110, 210, 140)
WARN = (245, 200, 105)
ERR = (240, 110, 110)
PURPLE = (190, 150, 255)
PROMPT = (150, 220, 170)
TITLE_BG = (30, 34, 43)

FPS = 30
GAP = 0.75          # пауза между сценами (сек)


# ---------------------------------------------------------------------------
# Реальный вывод CLI
# ---------------------------------------------------------------------------

PR_DIFF_USERS_API = '''"""Пользователи: REST-слой."""
from fastapi import FastAPI

app = FastAPI()

API_KEY = "sk-live-1234567890abcdefghijkl"


@app.get("/users/{user_id}")
def get_user(user_id: str):
    try:
        return {"id": user_id}
    except:
        return None


@app.delete("/users/{user_id}")
def delete_user(user_id: str):
    return {"deleted": user_id}
'''


def _load_fonts(size: int):
    return {
        "mono": ImageFont.truetype(FONT_MONO, size),
        "mono_b": ImageFont.truetype(FONT_MONO, size),
    }


@dataclass
class Line:
    text: str = ""
    color: tuple = FG
    bold: bool = False


def _color_for(text: str) -> tuple:
    t = text.strip()
    if t.startswith("$"):
        return PROMPT
    if t.startswith("❌") or "FAIL" in t or "::error" in t:
        return ERR
    if t.startswith("✅") or t.startswith("OK"):
        return OK
    if t.startswith("⚠") or t.startswith("[!]") or "::warning" in t:
        return WARN
    if t.startswith("### ") or t.startswith("## "):
        return ACCENT
    if t.startswith("•"):
        return PURPLE
    return FG


class Renderer:
    """Кадр «терминала»: заголовок окна, вывод, подпись этапа."""

    def __init__(self, font_size: int = 24):
        self.fs = font_size
        self.fonts = _load_fonts(font_size)
        self.lh = int(font_size * 1.42)
        self.chars = max(40, (W - 2 * MARGIN_X - 24) // int(font_size * 0.602))
        self.max_lines = (TEXT_BOTTOM - TEXT_TOP) // self.lh
        self.title_font = ImageFont.truetype(FONT_BOLD, 24)
        self.logo_font = ImageFont.truetype(FONT_BOLD, 22)
        self.caption_font = ImageFont.truetype(FONT_BOLD, 28)
        self.caption_sub = ImageFont.truetype(FONT_REG, 24)
        self.tiny = ImageFont.truetype(FONT_REG, 18)

    def wrap(self, text: str) -> list[str]:
        """Строка → строки экрана по ФАКТИЧЕСКОЙ ширине глифов.

        Считать символы нельзя: моноширинный Menlo + кириллица уже
        оценённой ширины, и длинная пункт отчёта вылезал за край кадра.
        Поэтому ширину мерим PIL-ом, а не арифметикой.
        """
        max_px = W - 2 * MARGIN_X - 48
        font = self.fonts["mono"]
        if font.getlength(text) <= max_px:
            return [text]
        out, cur = [], ""
        for word in text.split(" "):
            cand = f"{cur} {word}".strip()
            if font.getlength(cand) <= max_px:
                cur = cand
                continue
            if cur:
                out.append(cur)
            while font.getlength(word) > max_px:
                cut = len(word)
                while cut > 1 and font.getlength(word[:cut]) > max_px:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            cur = word
        if cur:
            out.append(cur)
        return out

    # Промпт оболочки: строки команд начинаются с него, как в настоящем
    # терминале (реальный скрипт печатает только вывод).
    PROMPT_TEXT = "day32 ❯ "

    def render(self, lines: list[Line], caption: str = "",
               caption_sub: str = "", title: str = "",
               ts: float | None = None) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, TITLE_H], fill=TITLE_BG)
        for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([22 + i * 26, 24, 38 + i * 26, 40], fill=c)
        d.text((MARGIN_X + 60, 20),
               title or ("scripts/day32_pr_review.sh demo · локальный "
                         "запуск AI-ревью PR"),
               font=self.title_font, fill=DIM)
        logo = "Day 32 · CLI"
        if ts is not None:
            logo = f"Day 32 · CLI · {_mmss(ts)}"     # как в записи экрана
        lw = d.textlength(logo, font=self.logo_font)
        d.text((W - MARGIN_X - lw, 20), logo, font=self.logo_font,
               fill=ACCENT)
        d.rectangle([0, TITLE_H, W, TITLE_H + 2], fill=BAR)

        y = TEXT_TOP
        rows = []
        for ln in lines:
            for piece in self.wrap(ln.text):
                rows.append(Line(piece, ln.color, ln.bold))
        if len(rows) > self.max_lines:
            d.text((W - MARGIN_X - 120, 100), "▼ прокрутка", font=self.tiny,
                   fill=DIM)
        for ln in rows[-self.max_lines:]:
            x = MARGIN_X + 24
            if ln.text.startswith("$ "):
                pw = d.textlength(self.PROMPT_TEXT, font=self.fonts["mono"])
                d.text((x, y), self.PROMPT_TEXT, font=self.fonts["mono"],
                       fill=ACCENT)
                x += pw
            d.text((x, y), ln.text[2:] if ln.text.startswith("$ ") else ln.text,
                   font=self.fonts["mono"], fill=ln.color)
            y += self.lh

        if caption:
            tw = d.textlength(caption, font=self.caption_font)
            d.rounded_rectangle([W - MARGIN_X - tw - 24, H - 70,
                                 W - MARGIN_X, H - 18], radius=12,
                                fill=(14, 16, 21))
            d.text((W - MARGIN_X - tw - 12, H - 62), caption,
                   font=self.caption_font, fill=ACCENT)
        if caption_sub:
            tw = d.textlength(caption_sub, font=self.caption_sub)
            x1 = W - MARGIN_X
            d.rounded_rectangle([x1 - tw - 30, 96, x1, 96 + 46], radius=12,
                                fill=(14, 16, 21))
            d.text((x1 - tw - 15, 104), caption_sub,
                   font=self.caption_sub, fill=WARN)
        return img


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "GITHUB_", "OLLAMA_", "REVIEW_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(json.loads(os.environ.get("DAY32_LLM_ENV_JSON", "{}")))
    return env


class Terminal:
    """Запись терминала: текст, курсор и итоговое состояние экрана.

    Сырые выводы идут в текстовый буфер целиком (с метками времени) —
    именно он становится транскриптом видео. Терминальные
    последовательности (цвет, курсор, очистка строки) разбираются
    минимально: без этого «Индекс собран» и отчёт склеивались в одну
    строку в 300 символов — прогресс-вывод переименовывается через `\r`
    и очистку.
    """

    _CSI_RE = re.compile(r"\x1b\[[0-9;?]*([A-Za-z])")

    def __init__(self):
        self.raw: list[str] = []
        self.t0 = time.time()
        self.duration = 0.0

    def feed(self, data: str) -> None:
        self.raw.append(data)
        self.duration = time.time() - self.t0

    @property
    def text(self) -> str:
        """Чистый текст: ANSI-цвета и управляющие символы убраны."""
        text = "".join(self.raw)
        text = text.replace("\r\n", "\n")
        text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
        text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
        # прогресс-строки: оставляем последнее состояние после \r
        out = []
        for line in text.split("\n"):
            line = line.split("\r")[-1]
            out.append(line.rstrip())
        return "\n".join(out)


def pty_fork(rows: int = 50, cols: int = 200):
    """(pid, master_fd) с явным размером терминала.

    Без TIOCSWINSZ программа считает терминал 80-символьным и режет
    markdown-таблицы отчёта.
    """
    import fcntl
    import pty as _pty
    import struct
    import termios
    # os.openpty() (a не псевдоним pty.openpty): на macOS псевдонимы
    # /dev/ptmx и /dev/pts/* не существуют, и pty.openpty() падает
    # «out of pty devices», хотя настоящий pts открывается.
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0))
    pid = os.fork()
    if pid == 0:                                    # дочерний процесс
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        if slave > 2:
            os.close(slave)
        try:
            os.chdir(RECORD_CWD[0])
            os.execvpe(RECORD_CMD[0], RECORD_CMD, _env())
        finally:
            os._exit(127)
    os.close(slave)
    return pid, master


RECORD_CWD = ["."]
RECORD_CMD: list = []


def record_session(cmd: list, cwd: str) -> Terminal:
    """Записать реальный прогон команды в псевдотерминале."""
    global RECORD_CMD
    RECORD_CMD = list(cmd)
    RECORD_CWD[0] = cwd
    term = Terminal()
    pid, fd = pty_fork()
    with os.fdopen(fd, "rb", buffering=0) as fh:
        while True:
            try:
                chunk = fh.read(4096)
            except OSError:                    # EIO — терминал закрыт
                break
            if not chunk:
                break
            term.feed(chunk.decode("utf-8", errors="replace"))
    os.waitpid(pid, 0)
    term.duration = time.time() - term.t0
    return term


# Сколько держится строка вывода и «ожидание» между шагами (сек).
SPIN_LINE = 0.30
THINK_MIN, THINK_MAX, THINK_CAP = 1.8, 4.5, 200     # ожидание модели
SPIN = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _mmss(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def _md_lines(text: str) -> list[Line]:
    """Markdown отчёта → строки экрана."""
    out = []
    for ln in text.splitlines():
        t = ln.rstrip()
        if not t or t.startswith("<details") or t.startswith("</details"):
            continue
        if t.startswith("## "):
            out.append(Line("", FG))
            out.append(Line(t[3:], ACCENT, bold=True))
        elif t.startswith("### "):
            out.append(Line("", FG))
            out.append(Line("▸ " + t[4:], PURPLE, bold=True))
        elif t.startswith("|"):
            out.append(Line(t, DIM if t.startswith("|---") else FG))
        elif t.startswith(("- ", "* ")):
            out.append(Line("  • " + t[2:], FG))
        else:
            out.append(Line(t, FG))
    return out


def build_report_frames(report: str, r: Renderer, thinking: float,
                        pace: float = 1.0):
    """«Живой отчёт»: markdown review.md появляется по частям.

    Отчёт — ровно тот файл, который записал пайплайн (три блока
    сформулировала модель). Пока модель отвечает, вместо отчёта —
    строка ожидания, как в терминале.
    """
    frames = []
    pending = [Line("AI-ревью: считаю…", ACCENT, bold=True), Line("", FG),
               Line("⠋  модель формулирует три раздела:", WARN),
               Line("    1. потенциальные баги", FG),
               Line("    2. архитектурные проблемы", FG),
               Line("    3. рекомендации", FG)]
    frames.append((r.render(pending, "Готовый отчёт появится после "
                            "ответа модели",
                            "⠋  выполняется — модель отвечает…",
                            title="review.md · отчёт ещё не записан"),
                   max(1.6, thinking)))
    blocks = [b for b in report.split("\n### ") if b.strip()]
    head = _md_lines(blocks[0].replace("## ", "## ", 1))
    head = _md_lines(blocks[0])
    acc = list(head)
    for i, block in enumerate(blocks[1:], 1):
        part = _md_lines("### " + block)
        base = len(acc)
        for k in range(2, len(part) + 2, 2):
            frames.append((r.render(acc + part[:min(k, len(part))],
                                    f"review.md · раздел {i} из "
                                    f"{len(blocks) - 1}",
                                    title="review.md · AI-ревью PR"),
                           0.12 * pace))
        acc = acc + part
    frames.append((r.render(acc, "review.md · готово",
                            title="review.md · AI-ревью PR"), 2.5))
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True,
                    help="куда сохранить mp4 (без звука)")
    ap.add_argument("--font", type=int, default=23)
    ap.add_argument("--keep", help="каталог для промежуточных файлов")
    ap.add_argument("--fit", type=float, default=75.0,
                    help="целевая длительность, сек (≈ верхняя граница)")
    args = ap.parse_args()

    work = args.keep or tempfile.mkdtemp(prefix="day32_video_")
    os.makedirs(work, exist_ok=True)
    os.environ["OUT_DIR"] = os.path.join(work, "out")

    print("[1/3] запись реального прогона (pty, как в терминале)…",
          flush=True)
    term = record_session(["bash", "scripts/day32_pr_review.sh", "demo"],
                          REPO)
    text = term.text
    with open(os.path.join(work, "transcript.txt"), "w",
              encoding="utf-8") as f:
        f.write(text)
    print(f"      {len(text.splitlines())} строк за {term.duration:.1f} с",
          flush=True)

    report_path = os.path.join(os.environ["OUT_DIR"], "review.md")
    report = ""
    if os.path.isfile(report_path):
        with open(report_path, encoding="utf-8") as f:
            report = f.read()

    print("[2/3] кадры…", flush=True)
    r = Renderer(args.font)
    # Сценарий = шаги «прохода»: (подпись этапа, от, до, множитель паузы).
    # Кадры строятся по транскрипту; таймкод в заголовке — видео-время.
    script: list[dict] = []

    # 1) терминал: команда, демо-репозиторий, check, index, начало run
    REPORT_START = ("# AI-ревью PR", "LLM:", "Полный отчёт:", "Отчёт:")
    shown: list[Line] = [
        Line("$ bash scripts/day32_pr_review.sh demo", PROMPT, bold=True)]
    console_rows: list[tuple[list[Line], str, float]] = []
    for raw in text.splitlines():
        ln = raw.rstrip()
        if not ln.strip() or ln.startswith(REPORT_START):
            continue
        shown.append(Line(ln, _color_for(ln)))
        console_rows.append((list(shown), _caption_for(shown),
                             _line_delay(ln)))

    # распределяем общий бюджет пауз: консоль, ожидание модели, отчёт,
    # финал — пропорционально числу элементов, но отчёт всегда richest
    budget = args.fit
    n_report = max(1, len(report.split("\n### ")) - 1)
    weights = {"console": len(console_rows), "think": 2,
               "report": n_report * 6.0, "tail": 3}
    wsum = sum(weights.values())
    shares = {k: budget * v / wsum for k, v in weights.items()}

    for rows, cap, delay in console_rows:
        script.append({"kind": "console", "lines": rows, "caption": cap,
                       "dur": delay * shares["console"] / len(console_rows)})
    script.append({"kind": "console", "lines": shown,
                   "caption": "Шаг 3 · run: модель думает",
                   "wait": True, "dur": shares["think"] * 0.8})

    if report:
        blocks = [b for b in report.split("\n### ") if b.strip()]
        acc = _md_lines(blocks[0])
        per_block = shares["report"] / max(1, len(blocks) - 1)
        for i, block in enumerate(blocks[1:], 1):
            part = _md_lines("### " + block)
            steps = max(2, len(part) // 3)
            for k in range(1, steps + 1):
                cut = max(1, round(len(part) * k / steps))
                script.append({"kind": "report",
                               "lines": acc + part[:cut] if k < steps
                               else acc + part,
                               "caption": f"review.md · раздел {i} из "
                                          f"{len(blocks) - 1}",
                               "dur": per_block / steps})
            acc = acc + part

    tail = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith(("LLM:", "Полный отчёт", "Отчёт"))]
    shown_t: list[Line] = []
    for ln in tail:
        shown_t.append(Line(ln, _color_for(ln)))
        script.append({"kind": "console", "lines": list(shown_t),
                       "caption": "Шаг 3 · готово: отчёт сохранён",
                       "dur": shares["tail"] / max(1, len(tail))})

    # 3) рендер: подписи-этапы + таймкод в заголовке (как запись экрана)
    frames = []
    t = 0.0
    frames.append((r.render([], "AI-ревью PR · локальный запуск",
                            title="scripts/day32_pr_review.sh demo · "
                                  "локальный запуск AI-ревью PR",
                            ts=0.0), 1.4))
    t += 1.4
    for step in script:
        dur = max(0.35, step["dur"])
        wait = "⠋  выполняется — модель отвечает…" if step.get("wait") else ""
        img = r.render(step["lines"], step["caption"], wait,
                       title="scripts/day32_pr_review.sh demo · "
                             "локальный запуск AI-ревью PR"
                             + (f" · отчёт review.md"
                                if step["kind"] == "report" else ""),
                       ts=t)
        frames.append((img, dur))
        t += dur
    total = sum(d for _, d in frames)
    print(f"      кадров {len(frames)}, длительность {total:.1f} с",
          flush=True)

    concat = os.path.join(work, "frames.txt")
    last_png = ""
    with open(concat, "w", encoding="utf-8") as f:
        for i, (img, dur) in enumerate(frames):
            path = os.path.join(work, f"frame_{i:05d}.png")
            img.save(path)
            last_png = path
            f.write(f"file '{path}'\nduration {max(dur, 1 / FPS):.4f}\n")
        f.write(f"file '{last_png}'\n")

    print("[3/3] монтаж (ffmpeg, без звука)…", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", concat,
                    "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", "-an", args.out],
                   check=True)
    print(f"готово: {args.out}")
    return 0


def _line_delay(ln: str) -> float:
    """Сколько держать строку: короткие — быстро, важные — дольше."""
    t = ln.strip()
    if not t:
        return 0.0
    if t.startswith("::"):
        return 1.6
    if t.startswith(("###", "| ", "|")):
        return 0.12
    if t.startswith(("✅", "❌", "⚠", "Индекс")):
        return 1.1
    if t.startswith(("•", "  ")):
        return 0.9
    return 0.45


def _caption_for(lines: list[Line]) -> str:
    """Подпись-этап из вывода: что сейчас делает пайплайн."""
    text = " ".join(ln.text for ln in lines)
    if "::error" in text or "::warning" in text:
        return "Шаг 3 · run: аннотации на строки diff"
    if "Полный отчёт" in text or "Отчёт:" in text:
        return "Готово: отчёт и данные сохранены"
    if "AI-ревью:" in text and "LLM:" in text:
        return "Шаг 3 · run: итог ревью"
    if "Индекс собран" in text:
        return "Шаг 2 · index: индекс корпуса готов"
    if "Проверки RAG" in text or "RAG:" in text:
        return "Шаг 1 · check: предполётная проверка"
    if "Репозиторий:" in text or "diff --stat" in text or "file changed" in text:
        return "Демо-репозиторий и его PR"
    return ""


if __name__ == "__main__":
    sys.exit(main())
