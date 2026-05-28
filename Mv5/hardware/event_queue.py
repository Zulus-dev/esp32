# hardware/event_queue.py - MicroPython 1.28 compatible async FIFO
import asyncio


class EventQueue:
    """Tiny bounded FIFO built on asyncio.Event.

    MicroPython 1.28 firmware builds can omit or lazily load asyncio.Queue.
    ColibryOS only needs a single producer/consumer button event channel, so this
    avoids the optional Queue class and keeps boot deterministic on ESP32-C3.
    """

    def __init__(self, maxlen):
        self.maxlen = maxlen
        self._items = []
        self._event = asyncio.Event()

    async def get(self):
        while not self._items:
            await self._event.wait()
            self._event.clear()
        return self._items.pop(0)

    async def put(self, item):
        self.put_nowait(item)

    def put_nowait(self, item):
        if len(self._items) >= self.maxlen:
            self._items.pop(0)
        self._items.append(item)
        self._event.set()

    def get_nowait(self):
        if not self._items:
            raise IndexError("empty")
        return self._items.pop(0)
