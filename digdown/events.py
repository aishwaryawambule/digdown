from __future__ import annotations

import asyncio


class EventBus:
    """In-process pub/sub. Each subscriber gets its own asyncio.Queue.

    publish() is safe to call from any thread. Diagnosis runs on a worker
    thread, so once a loop is bound (bind_loop, at app startup) a publish from
    off-loop is marshaled onto the loop with call_soon_threadsafe. On-loop and
    no-loop (tests) publishes go straight through.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        loop = self._loop
        if loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._deliver, event)
                return
        self._deliver(event)

    def _deliver(self, event: dict) -> None:
        for q in list(self._subscribers):
            q.put_nowait(event)
