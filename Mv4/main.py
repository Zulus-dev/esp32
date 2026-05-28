# main.py - ColibryOS minimal async kernel and lazy module dispatcher
import asyncio

import gc
import sys

import machine

from config import Config


class ColibryCore:
    """Event-driven kernel for ESP32-C3 with strict lazy module lifecycle."""

    def __init__(self):
        self.oled = None
        self.buzzer = None
        self.menu = None
        self.input_queue = None
        self._tasks = []
        self.services = {}
        self.state = {"wifi": False, "server": False, "radio": False}

    async def boot(self):
        try:
            machine.freq(Config.CPU_FREQ_HZ)
        except Exception:
            pass

        from hardware.oled import OLED
        from hardware.buzzer import Buzzer
        from hardware.buttons import TouchButton
        from hardware.event_queue import EventQueue
        from menu.manager import MenuManager

        self.oled = OLED()
        self.buzzer = Buzzer()
        self.menu = MenuManager(self.oled)
        self.input_queue = EventQueue(Config.INPUT_QUEUE_DEPTH)

        self.oled.show_message("ColibryOS", "Starting...")
        await self.buzzer.boot_test()
        await asyncio.sleep_ms(250)
        self.menu.load_config()
        self.menu.draw()

        up = TouchButton(Config.BUTTON_UP_PIN, "up", self.input_queue)
        down = TouchButton(Config.BUTTON_DOWN_PIN, "down", self.input_queue)
        self._tasks.append(asyncio.create_task(up.run()))
        self._tasks.append(asyncio.create_task(down.run()))
        self._tasks.append(asyncio.create_task(self._input_loop()))
        self._tasks.append(asyncio.create_task(self._idle_gc_loop()))

    async def run(self):
        await self.boot()
        while True:
            await asyncio.sleep_ms(60000)

    async def _input_loop(self):
        while True:
            button, event = await self.input_queue.get()
            action = self._map_button(button, event)
            if not action:
                continue

            await self._feedback(action)
            if self.menu.in_action and action == "back":
                self.menu.exit_action_mode()
                continue
            if self.menu.in_action:
                continue

            descriptor = self.menu.handle(action)
            if descriptor:
                await self._dispatch(descriptor)

    def _map_button(self, button, event):
        if button == "up":
            if event == "click":
                return "up"
            if event == "double":
                return "enter"
            if event == "long":
                return "back"
        elif button == "down":
            if event == "click":
                return "down"
            if event == "double":
                return "enter"
            if event == "long":
                return "back"
        return None

    async def _feedback(self, action):
        if not self.buzzer:
            return
        if action == "enter":
            await self.buzzer.beep(Config.BUZZER_ENTER_HZ, 45)
        elif action == "back":
            await self.buzzer.beep(Config.BUZZER_BACK_HZ, 60)
        else:
            await self.buzzer.beep(Config.BUZZER_CLICK_HZ, 25)

    async def _dispatch(self, descriptor):
        self.menu.enter_action_mode()
        hold_screen = False
        try:
            hold_screen = bool(await self.execute_module(descriptor["module"], descriptor["entry"]))
        finally:
            if not hold_screen:
                self.menu.exit_action_mode()

    async def execute_module(self, path, function):
        """Load, execute and purge a module with deterministic RAM cleanup."""
        if not path or not function:
            self.oled.show_message("Menu error", "Bad module")
            return None

        gc.collect()
        module = func = result = None
        before = self._free_mem()
        loaded_before = set(sys.modules.keys())
        try:
            module = __import__(path, None, None, (function,))
            func = getattr(module, function)
            result = func(self)
            if self._is_awaitable(result):
                result = await result
            return result
        except Exception as exc:
            print("[MODULE ERROR]", path, function, exc)
            try:
                self.oled.show_message("Module error", str(exc)[:48])
                await self.buzzer.beep(Config.BUZZER_ERROR_HZ, 120)
            except Exception:
                pass
            return None
        finally:
            result = None
            func = None
            module = None
            self._purge_module_tree(path)
            self._purge_new_modules(loaded_before)
            gc.collect()
            print("[MODULE PURGE]", path, "free", before, "->", self._free_mem())
            if Config.MODULE_SETTLE_MS:
                await asyncio.sleep_ms(Config.MODULE_SETTLE_MS)

    def _purge_module_tree(self, root):
        prefix = root + "."
        for name in tuple(sys.modules.keys()):
            if name == root or name.startswith(prefix):
                try:
                    del sys.modules[name]
                except KeyError:
                    pass

    def _purge_new_modules(self, loaded_before):
        for name in tuple(sys.modules.keys()):
            if name not in loaded_before:
                try:
                    del sys.modules[name]
                except KeyError:
                    pass

    def _is_awaitable(self, value):
        if value is None:
            return False
        if hasattr(value, "__await__"):
            return True
        # MicroPython coroutine objects are generator-like on some builds and
        # do not always expose __await__, so detect them by type name too.
        name = type(value).__name__
        return name == "generator" or name == "coroutine"

    def purge_modules(self, roots):
        for root in roots:
            self._purge_module_tree(root)
        gc.collect()

    def free_mem(self):
        return self._free_mem()

    def update_status(self, *, wifi=None, server=None, radio=None):
        if wifi is not None:
            self.state["wifi"] = wifi
        if server is not None:
            self.state["server"] = server
        if radio is not None:
            self.state["radio"] = radio
        if self.oled:
            self.oled.set_status(wifi=self.state["wifi"], server=self.state["server"], radio=self.state["radio"])

    async def _idle_gc_loop(self):
        while True:
            await asyncio.sleep_ms(Config.IDLE_GC_PERIOD_MS)
            gc.collect()

    def _free_mem(self):
        try:
            return gc.mem_free()
        except AttributeError:
            return 0


async def main():
    core = ColibryCore()
    await core.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("[FATAL] Kernel panic:", exc)
        machine.reset()
