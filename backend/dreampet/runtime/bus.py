"""In-process pub/sub used to stream live updates (SSE) to the web UI and CLI."""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class Bus:
    def __init__(self):
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    def subscribe(self, loop: asyncio.AbstractEventLoop, maxsize: int = 1000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(loop, x) for (loop, x) in self._subs if x is not q]

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, {"kind": kind, "data": data})
            except RuntimeError:  # loop closed
                self.unsubscribe(q)


def _put_nowait(q: asyncio.Queue, item: Any) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
