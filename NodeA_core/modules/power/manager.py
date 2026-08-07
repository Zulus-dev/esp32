# modules/power/manager.py — Q2 Node B / Q3 RF
#
# Флаги (не опрос GPIO):
#   powered    = Q2 ON (Node B)
#   rf_powered = Q3 ON (RF / CC1101)
#   online     = только UART (RadioHub)
#
# ВАЖНО: флаг НЕ называть rf_on — иначе перекрывает async def rf_on().
#

import asyncio
import gc

from machine import Pin

from config import Config


def _drop_rf_workspace():
    try:
        from lib import mempool
        mempool.ensure_rf_workspace(None)
    except Exception:
        pass
    gc.collect()


class PowerManager:
    STATE_B_OFF = "B_OFF"
    STATE_B_ON = "B_ON"
    STATE_RF_ON = "RF_ON"
    STATE_FAULT = "FAULT"

    def __init__(self, core):
        self.core = core
        self._lock = asyncio.Lock()
        self.state = self.STATE_B_OFF
        self.last_event = "init"
        self.last_error = 0
        self.powered = False      # Q2 commanded
        self.rf_powered = False   # Q3 commanded (НЕ rf_on — имя метода)
        self.rf_letter = "C"
        self._q2 = None
        self._q3 = None
        self._init_pins()
        self._push_status()

    def _init_pins(self):
        try:
            self._q2 = Pin(Config.NODE_B_POWER_PIN, Pin.OUT, value=0)
        except Exception:
            self._q2 = None
        try:
            self._q3 = Pin(Config.RF_POWER_PIN, Pin.OUT, value=0)
        except Exception:
            self._q3 = None

    def _drive_q2(self, on):
        self.powered = bool(on)
        if self._q2 is not None:
            try:
                self._q2.value(1 if self.powered else 0)
            except Exception:
                pass
        if not self.powered:
            self._drive_q3(False)

    def _drive_q3(self, on):
        on = bool(on) and self.powered
        self.rf_powered = on
        if self._q3 is not None:
            try:
                self._q3.value(1 if on else 0)
            except Exception:
                pass

    def _sync_state(self):
        if self.rf_powered and self.powered:
            self.state = self.STATE_RF_ON
        elif self.powered:
            self.state = self.STATE_B_ON
        else:
            self.state = self.STATE_B_OFF

    def set_rf_profile(self, letter):
        if not letter or letter == ".":
            self.rf_letter = "C"
        else:
            self.rf_letter = str(letter)[:1].upper()

    def _push_status(self):
        letter = self.rf_letter if self.rf_powered else "."
        try:
            if self.core:
                self.core.state["node_b"] = self.powered
                self.core.state["rf"] = self.rf_powered
                self.core.state["rf_letter"] = letter
        except Exception:
            pass
        try:
            if self.core and self.core.oled:
                self.core.oled.set_status(node_b=self.powered, rf=letter, force=True)
        except Exception:
            pass

    def _push_oled(self):
        self._push_status()

    def q2_level(self):
        return self.powered

    def q3_level(self):
        return self.rf_powered

    def set_node_b(self, enabled):
        self._drive_q2(enabled)
        self._sync_state()

    def set_rf(self, enabled):
        self._drive_q3(enabled)
        self._sync_state()

    async def node_b_on(self):
        async with self._lock:
            self._drive_q3(False)
            self._drive_q2(True)
            await asyncio.sleep_ms(Config.NODE_B_BOOT_WAIT_MS)
            self._sync_state()
            self.last_event = "node_b_on"
            self.last_error = 0
            self._push_status()
            return True

    async def node_b_off(self):
        async with self._lock:
            self._drive_q3(False)
            await asyncio.sleep_ms(30)
            await asyncio.sleep_ms(Config.NODE_B_CUT_DELAY_MS)
            self._drive_q2(False)
            self._sync_state()
            self.last_event = "node_b_off"
            self.last_error = 0
            self.rf_letter = "C"
            _drop_rf_workspace()
            self._push_status()
            gc.collect()
            return True

    async def rf_on(self):
        """Включить Q3 (CC1101/RF). Метод — не перекрывается флагом rf_powered."""
        async with self._lock:
            if not self.powered:
                self._drive_q2(True)
                await asyncio.sleep_ms(Config.NODE_B_BOOT_WAIT_MS)
            self._drive_q3(True)
            await asyncio.sleep_ms(30)
            self._sync_state()
            self.last_event = "rf_on"
            self.last_error = 0
            self._push_status()
            return True

    async def rf_off(self):
        async with self._lock:
            self._drive_q3(False)
            self._sync_state()
            self.last_event = "rf_off"
            self.last_error = 0
            _drop_rf_workspace()
            self._push_status()
            return True

    async def graceful_shutdown_all(self):
        return await self.node_b_off()

    async def emergency_cut(self):
        async with self._lock:
            self._drive_q3(False)
            self._drive_q2(False)
            self.state = self.STATE_FAULT
            self.last_event = "emergency_cut"
            self.last_error = 4
            self.rf_letter = "C"
            _drop_rf_workspace()
            self._push_status()
            gc.collect()

    def _drive_pin_low_hold(self, pin_no):
        p = None
        try:
            p = Pin(pin_no, Pin.OUT, value=0)
        except Exception:
            try:
                p = Pin(pin_no, Pin.OUT)
                p.value(0)
            except Exception:
                return None
        try:
            p.init(Pin.OUT, value=0, hold=True)
        except TypeError:
            try:
                p.init(Pin.OUT, value=0)
            except Exception:
                pass
        except Exception:
            pass
        return p

    def hold_power_rails_off(self):
        self.powered = False
        self.rf_powered = False
        self._q2 = self._drive_pin_low_hold(Config.NODE_B_POWER_PIN) or self._q2
        self._q3 = self._drive_pin_low_hold(Config.RF_POWER_PIN) or self._q3
        try:
            import esp32
            if hasattr(esp32, "gpio_deep_sleep_hold"):
                esp32.gpio_deep_sleep_hold(True)
        except Exception:
            pass
        self._sync_state()
        self.last_event = "hold_rails_off"
        self._push_status()

    def snapshot(self):
        letter = self.rf_letter if self.rf_powered else "."
        return {
            "state": self.state,
            "last_event": self.last_event,
            "last_error": self.last_error,
            "powered": self.powered,
            "rf_on": self.rf_powered,
            "rf_powered": self.rf_powered,
            "q2": self.powered,
            "q3": self.rf_powered,
            "node_b": self.powered,
            "rf": self.rf_powered,
            "rf_letter": letter,
        }


def get_power_manager(core):
    mgr = core.services.get("power")
    if mgr is None:
        mgr = PowerManager(core)
        core.services["power"] = mgr
    return mgr
