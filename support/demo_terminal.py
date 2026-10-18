"""Демо-терминал дня 33: Textual-TUI, который проигрывает сценарий задания.

Это НЕ фейк вывода: TUI печатает реальный stdout CLI-ассистента
(`python -m support`), реальные MCP-серверы CRM (stdio-процессы), реальный
RAG и реальную LLM. TUI лишь «набирает» вопросы пользователя по буквам,
чтобы запись принтер-терминалом была читаемой.

Запуск (для записи видео):
    python -X utf8 -m support.demo_terminal --ticket TCK-4101

Выход по завершении сценария (TUI закрывается сам).
"""
from __future__ import annotations

import asyncio
import os
import threading

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

TYPE_DELAY = 0.035      # пауза между символами «пользователя»
AFTER_SUBMIT = 0.25     # пауза после Enter
TIP_DELAY = 0.9

DEMO_SCRIPT = [
    "/tickets",
    "/ticket TCK-4101",
    "Почему не работает авторизация?",
    "Что ответить клиенту? Какие шаги предложить?",
    "А если SMS совсем не доставляется?",
    "Сколько стоит билет на Марс?",
]


class SupportTerminal(App):
    """Терминал ассистента поддержки: печать CLI-вывода + имитация ввода."""

    TITLE = "support — ассистент поддержки (день 33)"
    CSS = """
    Screen { background: $surface; }
    #body { height: 1fr; background: $surface; padding: 0 1; }
    .line { color: $text; }
    .prompt { color: $accent; text-style: bold; }
    .typed { color: $text; text-style: bold; }
    """

    BINDINGS = [Binding("q", "quit", "Выход", show=False)]

    def __init__(self, ticket: str | None = None, model: str | None = None,
                 script: list[str] | None = None) -> None:
        super().__init__()
        self._ticket = ticket
        self._model = model
        self._script = script or DEMO_SCRIPT
        self._stream = None
        self._busy_typing = False

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="body")

    @property
    def _body(self) -> VerticalScroll:
        return self.query_one("#body", VerticalScroll)

    def on_mount(self) -> None:
        self.set_interval(0.06, self._pump)
        self.run_worker(self._session, exclusive=True)

    # --- CLI-процесс ---

    def _pump(self) -> None:
        """Перенос строк из stdout CLI-процесса в виджет (поток -> UI)."""
        if self._stream is None:
            return
        chunk = self._stream.readline()
        while chunk:
            text = chunk.rstrip("\n")
            if text:
                self._body.mount(Static(Text(text), classes="line"))
                self._body.scroll_end(animate=False)
            chunk = self._stream.readline()

    def _mount_system(self, text: str = "") -> None:
        self._body.mount(Static(Text(text), classes="line"))
        self._body.scroll_end(animate=False)

    async def _start_cli(self) -> None:
        args = ["-u", "-X", "utf8", "-m", "support"]
        if self._ticket:
            args += ["--ticket", self._ticket]
        if self._model:
            args += ["--model", self._model]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        proc = await asyncio.create_subprocess_exec(
            os.sys.executable, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self._proc = proc
        self._stream = proc.stdout
        await asyncio.sleep(2.2)  # дать напечатать шапку (MCP-подключение)

    async def _type_and_send(self, text: str) -> None:
        """Имитация набора вопроса пользователем: буква за буквой."""
        self._busy_typing = True
        line = Static(Text(""), classes="typed")
        self._body.mount(line)
        typed = ""
        for ch in text:
            typed += ch
            line.update(Text("> " + typed, style="bold"))
            self._body.scroll_end(animate=False)
            await asyncio.sleep(TYPE_DELAY)
        await asyncio.sleep(AFTER_SUBMIT)
        line.remove()
        self._proc.stdin.write((text + "\n").encode("utf-8"))
        await self._proc.stdin.drain()
        self._busy_typing = False
        await self._wait_turn()

    async def _wait_turn(self) -> None:
        """Ждать ответа: появление «Следующий шаг:» или ошибки LLM."""
        await asyncio.sleep(1.0)
        while True:
            await asyncio.sleep(0.4)
            if self._last_has("Следующий шаг:") or self._last_has("! LLM ошибка"):
                await asyncio.sleep(TIP_DELAY)
                return

    def _last_has(self, needle: str) -> bool:
        lines = [w for w in self._body.children
                 if isinstance(w, Static)]
        for w in lines[-3:]:
            try:
                rendered = str(w.renderable) if hasattr(w, "renderable") else ""
            except Exception:
                rendered = ""
            if needle in rendered:
                return True
        return False

    async def _session(self) -> None:
        await self._start_cli()
        for step in self._script:
            await self._type_and_send(step)
        await asyncio.sleep(2.0)
        self._proc.stdin.write(b"/exit\n")
        try:
            await self._proc.stdin.drain()
        except Exception:
            pass
        await asyncio.sleep(1.5)
        self.exit()

    def on_unmount(self) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    import argparse
    parser = argparse.ArgumentParser(prog="support.demo_terminal")
    parser.add_argument("--ticket", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    SupportTerminal(ticket=args.ticket, model=args.model).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
