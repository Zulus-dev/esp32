# main.py — ColibryOS Node A kernel (async, lazy modules, GC)
#
# === RAM RULES ===
# 1. execute_module: purge + gc после модуля; web.* не трогать пока HTTP server жив.
# 2. services: не копить radio/link после stop — None + close.
# 3. Не добавлять фоновые tasks с list.append без потолка.
# 4. Крупные буферы — только lib.mempool.register/acquire.
# 5. Перед WiFi AP reclaim только в modules.wifi.manager.
#

import asyncio
import gc
import sys
import time

import machine
from machine import Pin

from config import Config


class ColibryCore:
    def __init__(self):
        self.oled = None
        self.buzzer = None
        self.menu = None
        self.input_queue = None
        self._tasks = []
        self.services = {}
        self.state = {
            "wifi": False,
            "server": False,
            "link": False,
            "node_b": False,
            "rf": False,
            "rf_letter": ".",
        }
        self.self_latch = None
        self.wdt = None
        self.battery = None
        self._last_battery_alert_ms = 0

    async def boot(self):
        try:
            machine.freq(Config.CPU_FREQ_HZ)
        except Exception:
            pass
        try:
            self.self_latch = Pin(Config.SELF_LATCH_PIN, Pin.OUT, value=1)
        except Exception:
            self.self_latch = None

        from hardware.oled import OLED
        from hardware.buzzer import Buzzer
        from hardware.buttons import TouchButton
        from hardware.event_queue import EventQueue
        from hardware.battery import BatteryMonitor
        from menu.manager import MenuManager

        self.oled = OLED()
        self.buzzer = Buzzer()
        self.menu = MenuManager(self.oled)
        self.input_queue = EventQueue(Config.INPUT_QUEUE_DEPTH)
        self.battery = BatteryMonitor()
        self.services["battery"] = self.battery
        # PowerManager: командное состояние Q2/Q3 (без опроса GPIO)
        try:
            from modules.power.manager import get_power_manager
            get_power_manager(self)
        except Exception as e:
            print("[POWER] init:", e)

        self.oled.show_message("ColibryOS", "Core boot...")
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
        self._tasks.append(asyncio.create_task(self._battery_loop()))
        self._start_watchdog()
        # Early GC under load; pre-register sticky web slabs only when WiFi starts
        try:
            gc.threshold(24 * 1024)
        except Exception:
            pass
        self._tasks.append(asyncio.create_task(self._mem_guard_loop()))

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

    def _web_server_alive(self):
        """True while HTTP server holds web.* code in RAM."""
        try:
            srv = self.services.get("web")
            return srv is not None and getattr(srv, "is_running", False)
        except Exception:
            return False

    def _is_web_module(self, name):
        return name == "web" or name.startswith("web.")

    def _purge_new_modules(self, loaded_before):
        protect_web = self._web_server_alive()
        for name in tuple(sys.modules.keys()):
            if name not in loaded_before:
                if protect_web and self._is_web_module(name):
                    continue
                try:
                    del sys.modules[name]
                except KeyError:
                    pass

    def _is_awaitable(self, value):
        if value is None:
            return False
        if hasattr(value, "__await__"):
            return True
        name = type(value).__name__
        return name == "generator" or name == "coroutine"

    def purge_modules(self, roots):
        for root in roots:
            self._purge_module_tree(root)
        gc.collect()

    def reclaim_radio(self):
        """Stop link RX task, close UART, purge radio modules."""
        try:
            hub = self.services.pop("radio", None)
            if hub is not None and hasattr(hub, "close"):
                hub.close()
        except Exception:
            pass
        try:
            link = self.services.pop("link", None)
            if link is not None and hasattr(link, "close"):
                link.close()
        except Exception:
            pass
        self.purge_modules(("hardware.radio_hub", "hardware.link_uart"))
        gc.collect()
        gc.collect()

    def free_mem(self):
        return self._free_mem()

    def update_status(self, *, wifi=None, server=None, link=None, battery=None, node_b=None, rf=None):
        if wifi is not None:
            self.state["wifi"] = wifi
        if server is not None:
            self.state["server"] = server
        if link is not None:
            self.state["link"] = link
        if node_b is not None:
            self.state["node_b"] = bool(node_b)
        if rf is not None:
            # rf may be bool (Q3) or letter char
            if rf is True:
                self.state["rf"] = True
                if not self.state.get("rf_letter") or self.state.get("rf_letter") == ".":
                    self.state["rf_letter"] = "C"
            elif rf is False or rf == "" or rf == ".":
                self.state["rf"] = False
                self.state["rf_letter"] = "."
            else:
                self.state["rf"] = True
                self.state["rf_letter"] = str(rf)[:1].upper()
        if self.oled:
            self.oled.set_status(
                server=self.state.get("server", False),
                node_b=self.state.get("node_b", False),
                rf=self.state.get("rf_letter", "."),
                battery=battery,
            )

    async def _battery_loop(self):
        while True:
            snap = self.battery.read() if self.battery else None
            if snap:
                self.update_status(battery=snap)
                await self._battery_alert(snap)
            await asyncio.sleep_ms(Config.BATTERY_POLL_MS)

    async def _battery_alert(self, snap):
        if not snap.get("ok") or snap.get("status") not in ("LOW", "CRITICAL"):
            return
        now = _ticks_ms()
        if self._last_battery_alert_ms and _ticks_diff(now, self._last_battery_alert_ms) < Config.BATTERY_ALERT_REPEAT_MS:
            return
        self._last_battery_alert_ms = now
        try:
            if self.buzzer:
                await self.buzzer.beep(
                    Config.BUZZER_WARN_HZ if snap.get("status") == "LOW" else Config.BUZZER_ERROR_HZ,
                    90,
                )
            if snap.get("status") == "CRITICAL" and self.oled:
                self.oled.show_message(
                    "Battery",
                    "Critical %d%%\n%.2f V" % (snap.get("percent", 0), snap.get("voltage", 0)),
                )
        except Exception:
            pass

    async def _idle_gc_loop(self):
        while True:
            await asyncio.sleep_ms(Config.IDLE_GC_PERIOD_MS)
            gc.collect()

    async def _mem_guard_loop(self):
        """Background pressure relief — does not claim contiguous free size."""
        while True:
            await asyncio.sleep_ms(3000)
            try:
                free = gc.mem_free()
            except Exception:
                free = 0
            if free and free < 40 * 1024:
                gc.collect()
                gc.collect()

    def _start_watchdog(self):
        try:
            self.wdt = machine.WDT(timeout=Config.WDT_TIMEOUT_MS)
            self._tasks.append(asyncio.create_task(self._watchdog_loop()))
        except Exception as exc:
            self.wdt = None
            print("[WDT] unavailable:", exc)

    async def _watchdog_loop(self):
        while True:
            await asyncio.sleep_ms(Config.WDT_FEED_MS)
            try:
                self.wdt.feed()
            except Exception:
                return

    async def power_off_sequence(self):
        pwr = self.services.get("power")
        if pwr is not None:
            try:
                await pwr.graceful_shutdown_all()
            except Exception:
                pass
            try:
                pwr.hold_power_rails_off()
            except Exception:
                pass
        if self.self_latch is not None:
            self.self_latch.value(0)
        await asyncio.sleep_ms(100)
        machine.deepsleep()

    def _free_mem(self):
        try:
            return gc.mem_free()
        except AttributeError:
            return 0


def _ticks_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)


def _ticks_diff(new, old):
    try:
        return time.ticks_diff(new, old)
    except Exception:
        return new - old


async def main():
    core = ColibryCore()
    await core.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("[FATAL]", exc)
        machine.reset()
