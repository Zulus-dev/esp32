# hardware/buttons.py - IRQ-driven TTP223 touch button driver
import asyncio

import utime as time

from machine import Pin
import micropython
micropython.alloc_emergency_exception_buf(100)

from config import Config


class TouchButton:
    """Debounced TTP223 button that publishes click/double/long events.

    The IRQ handler only timestamps edges and wakes a ThreadSafeFlag. Event
    classification runs in an asyncio task, keeping heap allocations outside
    interrupt context.
    """

    def __init__(self, pin_num, name, queue, *, long_ms=None, double_ms=None, debounce_ms=None):
        self.name = name
        self.queue = queue
        self.long_ms = long_ms or Config.BUTTON_LONG_MS
        self.double_ms = double_ms or Config.BUTTON_DOUBLE_MS
        self.debounce_ms = debounce_ms or Config.BUTTON_DEBOUNCE_MS
        self.pin = Pin(pin_num, Pin.IN, Pin.PULL_DOWN)
        self._flag = asyncio.ThreadSafeFlag()
        self._press_ms = 0
        self._release_ms = 0
        self._click_count = 0
        self._edge_level = self.pin.value()
        self.pin.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._irq)

    def _ticks_ms(self):
        try:
            return time.ticks_ms()
        except AttributeError:
            return int(time.time() * 1000)

    def _ticks_diff(self, newer, older):
        try:
            return time.ticks_diff(newer, older)
        except AttributeError:
            return newer - older

    def _irq(self, pin):
        self._edge_level = pin.value()
        now = self._ticks_ms()
        if self._edge_level:
            self._press_ms = now
        else:
            self._release_ms = now
        self._flag.set()

    async def run(self):
        """Async classifier task. Emits (button_name, event_name) tuples."""
        while True:
            await self._flag.wait()
            if self._edge_level:
                continue

            duration = self._ticks_diff(self._release_ms, self._press_ms)
            if duration < self.debounce_ms:
                continue

            if duration >= self.long_ms:
                self._click_count = 0
                await self._publish("long")
                continue

            self._click_count += 1
            release_snapshot = self._release_ms
            await asyncio.sleep_ms(self.double_ms)
            if self._release_ms != release_snapshot:
                continue

            count = self._click_count
            self._click_count = 0
            await self._publish("double" if count > 1 else "click")

    async def _publish(self, event):
        try:
            self.queue.put_nowait((self.name, event))
        except AttributeError:
            await self.queue.put((self.name, event))
        except IndexError:
            # Queue full: drop the oldest event to keep UI responsive.
            try:
                self.queue.get_nowait()
                self.queue.put_nowait((self.name, event))
            except Exception:
                pass
