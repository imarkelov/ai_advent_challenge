"""SSE event bus с блокировкой пайплайна на вопросах к человеку."""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

CLOSE = "__close__"


class EventBus:
    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._answers: dict[str, tuple[asyncio.Event, list[str]]] = {}

    def _queue(self, run_id: str) -> asyncio.Queue:
        return self._queues.setdefault(run_id, asyncio.Queue())

    def publish(self, run_id: str, event: dict) -> None:
        self._queue(run_id).put_nowait(event)

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        queue = self._queue(run_id)
        while True:
            event = await queue.get()
            if event.get("type") == CLOSE:
                return
            yield event

    def close(self, run_id: str) -> None:
        self._queue(run_id).put_nowait({"type": CLOSE})

    def register_question(self, question_id: str) -> None:
        self._answers[question_id] = (asyncio.Event(), [""])

    def answer(self, question_id: str, text: str) -> bool:
        entry = self._answers.get(question_id)
        if entry is None:
            return False
        event, holder = entry
        holder[0] = text
        event.set()
        return True

    async def wait_for_answer(self, question_id: str,
                              timeout: float | None = 1800.0) -> str | None:
        entry = self._answers.get(question_id)
        if entry is None:
            return None
        event, holder = entry
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        return holder[0]

    def wait_for_answer_sync(self, question_id: str,
                             timeout: float | None = 1800.0) -> str | None:
        """Ожидание ответа из синхронного потока (без запущенного event loop)."""
        entry = self._answers.get(question_id)
        if entry is None:
            return None
        event, holder = entry
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(asyncio.wait_for(event.wait(), timeout=timeout))
        except asyncio.TimeoutError:
            return None
        finally:
            loop.close()
        return holder[0]
