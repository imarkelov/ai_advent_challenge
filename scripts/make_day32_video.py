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

def _make_repo(root: str) -> None:
    """Сборный репозиторий: корпус проекта + ветка PR (как в CI)."""
    corpus = ["README.md", "docs", "agents", "openspec", "data/crm",
              "studio/backend", "studio/frontend/src", "scripts"]
    for rel in corpus:
        src, dst = os.path.join(REPO, rel), os.path.join(root, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "node_modules", "dist"))
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(root, "data", "kb"), exist_ok=True)

    def git(*a):
        subprocess.run(["git"] + list(a), cwd=root, check=True,
                       capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Dev")
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write("# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST.\n"
                "Удаление доступно только администратору через сервис "
                "`users_service`.\n")
    git("add", "-A")
    git("commit", "-qm", "база")
    git("checkout", "-qb", "feature/users-api")
    os.makedirs(os.path.join(root, "studio", "backend"), exist_ok=True)
    with open(os.path.join(root, "studio", "backend", "users_api.py"), "w",
              encoding="utf-8") as f:
        f.write(PR_DIFF_USERS_API)
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write("# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST "
                "и удаляет пользователя.\n")
    git("add", "-A")
    git("commit", "-qm", "feat(users): REST-слой пользователей")


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


def run_cli(repo: str, args: list, timeout: int = 1800) -> tuple:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "GITHUB_", "OLLAMA_", "REVIEW_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(os.environ.get("DAY32_LLM_ENV_JSON", "{}")
               and json.loads(os.environ["DAY32_LLM_ENV_JSON"]))
    p = subprocess.run([PY, CLI, "--repo", repo] + args, cwd=BACKEND,
                       capture_output=True, text=True, env=env,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_git(repo: str, *args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=repo, capture_output=True,
                       text=True)
    return p.stdout if p.returncode == 0 else ""


def collect(repo: str) -> dict:
    """Фактический вывод всех шагов сценария."""
    out = {}
    # Как в Action: сначала check (индекса ещё нет), затем index и run.
    _, chk, err = run_cli(repo, ["pr-review", "check"])
    out["check"] = (chk + err).rstrip()
    _, idx, err = run_cli(repo, ["index"])
    out["index"] = (idx + err).rstrip()
    _, chk2, err2 = run_cli(repo, ["pr-review", "check"])
    out["check_after"] = (chk2 + err2).rstrip()
    code, rev, err = run_cli(repo, ["pr-review", "run", "--base", "main",
                                    "--out", os.path.join(repo,
                                                          "review.md"),
                                    "--json-out", os.path.join(repo,
                                                               "review.json")])
    out["run_console"] = (rev + err).rstrip()
    out["exit_code"] = code
    try:
        with open(os.path.join(repo, "review.md"), encoding="utf-8") as f:
            out["report"] = f.read()
    except OSError:
        out["report"] = ""
    try:
        with open(os.path.join(repo, "review.json"), encoding="utf-8") as f:
            out["json"] = json.load(f)
    except (OSError, ValueError):
        out["json"] = {}
    out["log"] = run_git(repo, "log", "--oneline", "main..HEAD")
    out["stat"] = run_git(repo, "diff", "--stat", "main...HEAD")
    out["diff"] = run_git(repo, "diff", "main...HEAD")
    out["files"] = run_git(repo, "diff", "--name-status", "main...HEAD")
    return out


# ---------------------------------------------------------------------------
# Терминальная запись: строки, стили, откуда переиспользовать реальный текст
# ---------------------------------------------------------------------------

@dataclass
class Line:
    text: str = ""
    color: tuple = FG
    bold: bool = False
    indent: int = 0


def _color_for(text: str) -> tuple:
    t = text.strip()
    if t.startswith("$"):
        return PROMPT
    if t.startswith("❌") or "FAIL" in t or "::error" in t:
        return ERR
    if t.startswith("✅") or t.startswith("OK"):
        return OK
    if t.startswith("⚠") or t.startswith("[!]"):
        return WARN
    if t.startswith("::warning"):
        return WARN
    if t.startswith("### ") or t.startswith("## "):
        return ACCENT
    if t.startswith("|") or t.startswith("+"):
        return FG
    return FG


def text_lines(text: str, color: tuple | None = None) -> list[Line]:
    return [Line(t, color or _color_for(t)) for t in text.splitlines()]


def section(text: str, title: str) -> list[Line]:
    """Вырезать раздел markdown-отчёта `### title` до следующего `###`."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == title),
                 None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("### ")), len(lines))
    out = []
    for ln in lines[start + 1:end]:
        if not ln.strip():
            continue
        if ln.startswith("_Раздел без модели"):
            continue
        out.append(ln)
    return out


# ---------------------------------------------------------------------------
# Рендер кадра
# ---------------------------------------------------------------------------

def _load_fonts(size: int):
    return {
        "mono": ImageFont.truetype(FONT_MONO, size),
        "mono_b": ImageFont.truetype(FONT_MONO, size),
        "ui": ImageFont.truetype(FONT_REG, size),
        "ui_b": ImageFont.truetype(FONT_BOLD, size),
    }


class Renderer:
    def __init__(self, font_size: int):
        self.fs = font_size
        self.fonts = _load_fonts(font_size)
        self.lh = int(font_size * 1.42)
        self.chars = max(40, (W - 2 * MARGIN_X - 24) // int(font_size * 0.602))
        self.max_lines = (TEXT_BOTTOM - TEXT_TOP) // self.lh
        self.sub_font = ImageFont.truetype(FONT_REG, 30)
        self.title_font = ImageFont.truetype(FONT_BOLD, 26)
        self.logo_font = ImageFont.truetype(FONT_BOLD, 22)

    def render(self, lines: list[Line], cursor: bool = True,
               subtitle: str = "", typing: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        # заголовок: «окно» CI
        d.rectangle([0, 0, W, TITLE_H], fill=TITLE_BG)
        for i, c in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            d.ellipse([22 + i * 26, 24, 38 + i * 26, 40], fill=c)
        d.text((MARGIN_X + 60, 20), "GitHub Actions · ai-review · "
               "pull_request #42", font=self.title_font, fill=DIM)
        d.text((W - MARGIN_X - 260, 20), "Day 32 · CLI", font=self.logo_font,
               fill=ACCENT)
        d.rectangle([0, TITLE_H, W, TITLE_H + 2], fill=BAR)

        y = TEXT_TOP
        for ln in lines[-self.max_lines:]:
            x = MARGIN_X + 24 + ln.indent * 26
            font = self.fonts["mono_b"] if ln.bold else self.fonts["mono"]
            d.text((x, y), ln.text, font=font, fill=ln.color)
            y += self.lh
        if cursor:
            cx = MARGIN_X + 24
            if lines:
                last = lines[-1].text
                cx += int(len(last) * self.fs * 0.602)
                cy = TEXT_TOP + (self.max_lines - 1 - min(
                    len(lines) - 1, self.max_lines - 1)) * self.lh
                cy = TEXT_TOP + (min(len(lines), self.max_lines) - 1) * \
                    self.lh
            else:
                cy = TEXT_TOP
            if typing:
                d.rectangle([cx + 2, cy + 3, cx + 3 + int(
                    self.fs * 0.55), cy + self.lh - 5], fill=FG)

        # субтитр
        if subtitle:
            tw = d.textlength(subtitle, font=self.sub_font)
            pad = 22
            box = [W // 2 - tw / 2 - pad, H - 88, W // 2 + tw / 2 + pad,
                   H - 26]
            d.rounded_rectangle(box, radius=12, fill=(12, 13, 17))
            d.text((W // 2 - tw / 2, H - 76), subtitle, font=self.sub_font,
                   fill=(240, 240, 240))
        return img


# ---------------------------------------------------------------------------
# Таймлайн: сцены → кадры
# ---------------------------------------------------------------------------

def _segments(text: str, chars: int) -> list[str]:
    """Разбить текст на строки до `chars` символов (по словам)."""
    out, cur = [], ""
    for word in text.split(" "):
        candidate = f"{cur} {word}".strip()
        if len(candidate) <= chars:
            cur = candidate
        else:
            if cur:
                out.append(cur)
            while len(word) > chars:
                out.append(word[:chars])
                word = word[chars:]
            cur = word
    if cur:
        out.append(cur)
    return out


SLOW = re.compile(r"(HTTP 400|exceeds|потенциальные баги|архитектурные "
                  r"проблемы|рекомендации|3 мин|8192|RAG|Ollama)",
                  re.I)


def build_frames(scenes: list[dict], real: dict) -> list[tuple[Image.Image,
                                                               float]]:
    frames: list[tuple[Image.Image, float]] = []
    for sc in scenes:
        font_size = sc.get("font", 26)
        r = Renderer(font_size)
        target = sc.get("lines", [])
        sub = sc.get("subtitle", "")
        hold = float(sc.get("hold", 1.4))
        for line in target:
            assert isinstance(line, str)
        # набираем построчно, терминал «прокручивается» сам
        shown: list[Line] = []
        for line in target:
            if line.startswith("[center]"):
                shown = [Line(line[8:], ACCENT, bold=True)]
                frames.append((r.render(shown, False, sub), 0.9))
                continue
            color = _color_for(line)
            if SLOW.search(line):
                color = ACCENT if not line.startswith(("✅", "❌", "⚠")) \
                    else color
            segs = _segments(line, r.max_lines and r.chars - 8)
            for i, seg in enumerate(segs):
                shown.append(Line(seg, color, indent=2 if i else 0))
                frames.append((r.render(shown, True, sub, typing=False),
                               0.035 if len(segs) > 2 else 0.06))
        if not shown:
            frames.append((r.render([], True, sub), 1.0))
        else:
            frames.append((r.render(shown, True, sub), hold))
    return frames


def tts(text: str, out_path: str, voice: str, rate: int) -> float:
    aiff = out_path + ".aiff"
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", aiff, text],
                   check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@44100", aiff,
                    out_path], check=True)
    os.remove(aiff)
    with wave.open(out_path) as w:
        return w.getnframes() / w.getframerate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice", default="Milena")
    ap.add_argument("--rate", type=int, default=200)
    ap.add_argument("--no-voice", action="store_true")
    ap.add_argument("--keep", help="каталог для промежуточных файлов")
    args = ap.parse_args()

    with open(args.script, encoding="utf-8") as f:
        script = json.load(f)
    work = args.keep or tempfile.mkdtemp(prefix="day32_video_")
    os.makedirs(work, exist_ok=True)

    repo = os.path.join(work, "repo")
    print("[1/5] сборный репозиторий и живой прогон пайплайна…", flush=True)
    os.makedirs(repo, exist_ok=True)
    _make_repo(repo)
    real = collect(repo)
    with open(os.path.join(work, "real_output.json"), "w",
              encoding="utf-8") as f:
        json.dump(real, f, ensure_ascii=False, indent=2)
    print(f"      exit={real['exit_code']}, отчёт "
          f"{len(real['report'])} символов", flush=True)

    print("[2/5] сборка сцен из реального вывода…", flush=True)
    scenes = script["scenes"]
    for sc in scenes:
        if sc.get("source"):
            src = sc["source"]
            if src == "check":
                sc["lines"] = ["$ cli.py pr-review check"] + \
                    real["check"].splitlines()
            elif src == "index":
                sc["lines"] = [f"$ cli.py index"] + real["index"].splitlines()
            elif src == "git":
                sc["lines"] = (["$ git log --oneline main..HEAD"]
                               + real["log"].splitlines() + [""]
                               + ["$ git diff --name-status main...HEAD"]
                               + real["files"].splitlines())
            elif src.startswith("report:"):
                title = "### " + src.split(":", 1)[1]
                sc["lines"] = section(real["report"], title)
            elif src == "check-after":
                sc["lines"] = ["$ cli.py pr-review check   # после index"] + \
                    real["check_after"].splitlines()
            elif src == "what-model-saw":
                j = real.get("json") or {}
                pr = j.get("prompt") or {}
                rag = j.get("corpus_context") or []
                sc["lines"] = [
                    "$ python -c \"json.load(open('review.json'))\"", "",
                    f"llm: {j.get('llm')}   agent: {j.get('agent')}",
                    f"промпт: {pr.get('user_chars')} символов "
                    f"(бюджет {pr.get('budget')})",
                    f"RAG-фрагментов: {len(rag)}",
                ] + [f"  · {c['file']} :: {c.get('section')}"
                     f"   ← {c['why']}" for c in rag[:6]] + [
                    f"находок: {j.get('summary', {}).get('findings')}"]
            elif src == "run-console":
                sc["lines"] = ["$ cli.py pr-review run --base main"]
                sc["lines"] += real["run_console"].splitlines() + \
                    [f"\n[exit code: {real['exit_code']}]"]

    print("[3/5] озвучка…", flush=True)
    events = []
    t = 0.6
    for i, sc in enumerate(scenes):
        wav = os.path.join(work, f"voice_{i:02d}.wav")
        dur = 0.0
        if not args.no_voice and sc.get("say"):
            dur = tts(sc["say"], wav, args.voice, args.rate)
        events.append({"start": t, "voice": wav if dur else None,
                       "voice_dur": dur, "scene": i})
        # пауза после сцены: голос затихает, кадр ещё держится
        t += dur + GAP

    print("[4/5] кадры…", flush=True)
    timeline = []          # (duration_seconds, png_path)
    idx = 0

    def emit(img, dur):
        nonlocal idx
        path = os.path.join(work, f"frame_{idx:05d}.png")
        img.save(path)
        timeline.append((max(dur, 1 / FPS), path))
        idx += 1

    for ev, sc in zip(events, scenes):
        # Сцена «печатается» ровно за длительность своей озвучки: сначала
        # пауза (голос ещё говорит вступление), затем нарастание текста,
        # последний кадр держится до конца голоса.
        frames = build_frames([sc], real)
        voice = max(ev["voice_dur"], 0.8)
        lead = min(0.9, voice * 0.25)
        sum_dur = sum(d for _, d in frames) or 1.0
        scale = max(0.2, voice - lead - sum_dur / 3) / sum_dur
        emit(frames[0][0], lead)
        for img, d in frames:
            emit(img, d * scale)
        hold = max(0.0, voice - lead - sum_dur * scale)
        if hold > 0:
            emit(frames[-1][0], hold)
        # пауза между сценами — последний кадр держится без озвучки
        emit(frames[-1][0], GAP)
    total = sum(d for d, _ in timeline)

    print(f"      кадров: {len(timeline)}, длительность {total:.1f} с",
          flush=True)
    print("[5/5] монтаж (ffmpeg)…", flush=True)
    concat = os.path.join(work, "frames.txt")
    with open(concat, "w", encoding="utf-8") as f:
        for d, path in timeline:
            f.write(f"file '{path}'\nduration {max(d, 1 / FPS):.4f}\n")
        f.write(f"file '{timeline[-1][1]}'\n")
    video = os.path.join(work, "video.mp4")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", concat,
                    "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264",
                    "-preset", "medium", "-crf", "20", video], check=True)
    if not args.no_voice:
        audio = os.path.join(work, "audio.wav")
        inputs = ["-i", video]
        filters, labels, k = [], [], 0
        for ev in events:
            if not ev["voice"]:
                continue
            inputs += ["-i", ev["voice"]]
            k += 1
            ms = int(ev["start"] * 1000)
            filters.append(f"[{k}:a]adelay={ms}|{ms}[a{k}]")
            labels.append(f"[a{k}]")
        filters.append(f"{''.join(labels)}amix=inputs={k}:"
                       "normalize=0:duration=first,"
                       "volume=1.6,alimiter=limit=0.95[a]")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error"] + inputs +
                       ["-filter_complex", ";".join(filters),
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", "-shortest",
                        audio + ".m4a"], check=True)
        final = os.path.join(work, "final.mp4")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", video,
                        "-i", audio + ".m4a", "-c:v", "copy", "-c:a", "copy",
                        final], check=True)
        src = final
    else:
        src = video
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    shutil.copy2(src, args.out)
    print(f"готово: {args.out}")
    return 0


import wave  # noqa: E402  (после основного кода: нужен только tts)

if __name__ == "__main__":
    sys.exit(main())
