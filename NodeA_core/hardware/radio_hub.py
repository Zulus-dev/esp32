# hardware/radio_hub.py — binary link supervisor (lazy)
#
# RAM RULES:
# 1. Create only on demand; node_b_off → drop link + cancel RX + gc.
# 2. UART = codes only (protocol.py). No str on wire.
# 3. last_error = int ERR_* (not str).
# 4. OLED update only on state change.
# 5. snapshot = small dict of ints/bools for web (JSON only at HTTP edge).

import asyncio
import gc

from protocol import *


class RadioHub:
    def __init__(self, core):
        self.core = core
        self.link = None
        self.active_mod = MOD_NONE
        self.mod_state = 0  # 0 idle, 1 armed, 2 on, 3 scan, 4 sniff
        self.err = ERR_NONE
        self._rx_task = None
        self._ui = None  # (powered, rf, letter, online)

    def _pwr(self):
        from modules.power.manager import get_power_manager
        return get_power_manager(self.core)

    def _ensure_link(self):
        if self.link is None:
            from hardware.link_uart import LinkClient
            self.link = LinkClient()
            self.core.services["link"] = self.link
        return self.link

    def _drop_link(self):
        self._stop_rx()
        if self.link is not None:
            try:
                self.link.close()
            except Exception:
                pass
            self.link = None
        self.core.services.pop("link", None)
        self.active_mod = MOD_NONE
        self.mod_state = 0
        self._ui = None
        gc.collect()

    def _letter(self, pwr, rf_on):
        if not rf_on:
            return "."
        if self.active_mod == MOD_SUBGHZ:
            return "C"
        if self.active_mod == MOD_NRF:
            return "N"
        if self.active_mod == MOD_WIFI:
            return "W"
        if self.active_mod == MOD_BLE:
            return "L"
        return pwr.rf_letter if pwr.rf_letter else "C"

    def _refresh_ui(self, force=False):
        try:
            pwr = self._pwr()
            powered = 1 if pwr.powered else 0
            rf_on = 1 if pwr.rf_powered else 0
            online = 1 if (self.link and self.link.online()) else 0
            letter = self._letter(pwr, rf_on)
            key = (powered, rf_on, letter, online)
            if not force and key == self._ui:
                return
            self._ui = key
            st = self.core.state
            st["node_b"] = bool(powered)
            st["rf"] = bool(rf_on)
            st["rf_letter"] = letter
            st["link"] = bool(online)
            oled = self.core.oled
            if oled:
                oled.set_status(node_b=bool(powered), rf=letter)
        except Exception:
            pass

    async def _rx_loop(self):
        while self.link is not None:
            try:
                self.link.poll()
                self._refresh_ui(False)
            except Exception:
                pass
            await asyncio.sleep_ms(250)

    def _start_rx(self):
        if self._rx_task is None:
            self._rx_task = asyncio.create_task(self._rx_loop())

    def _stop_rx(self):
        t = self._rx_task
        self._rx_task = None
        if t:
            try:
                t.cancel()
            except Exception:
                pass

    async def node_b_on(self):
        pwr = self._pwr()
        link = self._ensure_link()
        link.reset_state()
        link.flush_rx()
        self._start_rx()
        await pwr.node_b_on()
        ok = await link.wait_online(timeout_ms=5000)
        self.err = ERR_NONE if ok else ERR_LINK_TIMEOUT
        self._refresh_ui(True)
        return ok

    async def node_b_off(self):
        link = self.link
        if link is not None and link.online():
            try:
                link.send_cmd(CMD_STOP_ALL)
                await asyncio.sleep_ms(30)
                link.send_cmd(CMD_PREPARE_SHUTDOWN)
                await link.wait_status((ST_PREPARE_DONE,), timeout_ms=600)
                link.send_cmd(CMD_SHUTDOWN)
                await link.wait_status((ST_READY_POWER_OFF,), timeout_ms=600)
            except Exception:
                pass
        self._drop_link()
        self.err = ERR_NONE
        pwr = self._pwr()
        pwr.set_rf_profile(".")
        await pwr.node_b_off()
        self.core.state["link"] = False
        self._refresh_ui(True)
        gc.collect()
        return True

    async def module_on(self, mod_id):
        pwr = self._pwr()
        if not pwr.powered:
            await self.node_b_on()
        elif self.link is None:
            self._ensure_link()
            self._start_rx()
            await self.link.wait_online(timeout_ms=2000)
        if not pwr.rf_powered:
            await pwr.rf_on()
        # letter from mod id
        if mod_id == MOD_SUBGHZ:
            pwr.set_rf_profile("C")
        elif mod_id == MOD_NRF:
            pwr.set_rf_profile("N")
        elif mod_id == MOD_WIFI:
            pwr.set_rf_profile("W")
        elif mod_id == MOD_BLE:
            pwr.set_rf_profile("L")
        link = self._ensure_link()
        try:
            link.send_cmd(CMD_RF_ON)
            await link.wait_status((ST_RF_ON_DONE,), timeout_ms=800)
        except Exception:
            pass
        try:
            buf = bytearray(2)
            buf[0] = OP_CONFIG
            buf[1] = 0
            link.send_cmd(mod_id, buf)
            self.active_mod = mod_id
            self.mod_state = 1
            ok = await link.wait_status((ST_MOD_ON, ST_UNSUPPORTED, ST_RF_ON_DONE), timeout_ms=800)
            if link.last_status_code == ST_MOD_ON:
                self.mod_state = 2
            self.err = ERR_NONE if ok else ERR_ACK_TIMEOUT
        except Exception:
            self.active_mod = mod_id
            self.mod_state = 1
            self.err = ERR_ACK_TIMEOUT
        self._refresh_ui(True)
        return True

    async def module_off(self, rail_off=True):
        link = self.link
        pwr = self._pwr()
        if link is not None and self.active_mod != MOD_NONE:
            try:
                buf = bytearray(1)
                buf[0] = OP_STOP
                link.send_cmd(self.active_mod, buf)
                await asyncio.sleep_ms(20)
                link.send_cmd(CMD_STOP_ALL)
                await link.wait_status((ST_STOPPED, ST_MOD_OFF), timeout_ms=500)
            except Exception:
                pass
        self.active_mod = MOD_NONE
        self.mod_state = 0
        if rail_off:
            if link is not None:
                try:
                    link.send_cmd(CMD_RF_OFF)
                    await link.wait_status((ST_RF_OFF_DONE,), timeout_ms=500)
                except Exception:
                    pass
            await pwr.rf_off()
        pwr.set_rf_profile(".")
        self._refresh_ui(True)
        gc.collect()
        return True

    async def command(self, data):
        action = data.get("action", "")
        mod = data.get("module", "subghz")
        mod_id = MOD_SUBGHZ
        if mod == "nrf":
            mod_id = MOD_NRF
        elif mod == "wifi":
            mod_id = MOD_WIFI
        elif mod == "ble":
            mod_id = MOD_BLE
        if action == "node_b_on":
            await self.node_b_on()
        elif action == "node_b_off":
            await self.node_b_off()
        elif action == "module_on" or action == "rf_on":
            await self.module_on(mod_id if action == "module_on" else (self.active_mod or MOD_SUBGHZ))
        elif action in ("module_off", "rf_off", "stop_all"):
            await self.module_off(rail_off=True)
        elif action in ("scan", "sniff", "rssi_once", "stop"):
            if self.active_mod != mod_id:
                await self.module_on(mod_id)
            link = self._ensure_link()
            if action == "stop":
                buf = bytearray(1)
                buf[0] = OP_STOP
                link.send_cmd(mod_id, buf)
                self.mod_state = 1
            elif action == "rssi_once":
                buf = bytearray(1)
                buf[0] = OP_ONCE
                link.send_cmd(mod_id, buf)
            else:
                fk = int(data.get("freq_khz") or 433920)
                body = bytearray(6)
                body[0] = OP_START
                body[1] = SUB_MODE_SCAN if action == "scan" else SUB_MODE_SNIFF
                body[2] = (fk >> 24) & 0xFF
                body[3] = (fk >> 16) & 0xFF
                body[4] = (fk >> 8) & 0xFF
                body[5] = fk & 0xFF
                link.send_cmd(mod_id, body)
                self.mod_state = 3 if action == "scan" else 4
            for _ in range(6):
                link.poll()
                await asyncio.sleep_ms(20)
            self._refresh_ui(True)
        else:
            self.err = ERR_FAULT

    def snapshot(self):
        pwr = self._pwr()
        powered = bool(pwr.powered)
        rf_on = bool(pwr.rf_powered)
        link = self.link
        online = bool(link and link.online())
        letter = self._letter(pwr, rf_on)
        names = {MOD_NONE: "none", MOD_SUBGHZ: "subghz", MOD_NRF: "nrf", MOD_WIFI: "wifi", MOD_BLE: "ble"}
        states = ("idle", "armed", "on", "scan", "sniff")
        ms = self.mod_state if 0 <= self.mod_state < 5 else 0
        return {
            "ok": True,
            "link": {
                "online": online,
                "pending": powered and not online,
                "boot_ok": bool(link and link.boot_ok),
                "rx_frames": link.rx_frames if link else 0,
                "crc_fail": link.rx_crc_fail if link else 0,
                "ka_free_kb": link.ka_free_kb if link else 0,
            },
            "power": {
                "state": pwr.state,
                "powered": powered,
                "rf_on": rf_on,
                "q2": powered,
                "q3": rf_on,
                "node_b": powered,
                "rf": rf_on,
                "rf_letter": letter,
                "node_b_online": powered and online,
                "rf_online": rf_on and online,
                "last_event": pwr.last_event,
                "err": self.err,
                "last_error": self.err,
            },
            "module": {
                "id": self.active_mod,
                "name": names.get(self.active_mod, "none"),
                "active": self.active_mod != MOD_NONE and ms != 0,
                "state": states[ms],
                "letter": letter if rf_on else ".",
            },
            "telemetry": (
                {"last_rssi": link.last_rssi, "freq_khz": link.last_rssi_freq}
                if link and link.last_rssi is not None
                else {}
            ),
        }

    def close(self):
        self._drop_link()
        self.err = ERR_NONE


def get_radio_hub(core):
    hub = core.services.get("radio")
    if hub is None:
        hub = RadioHub(core)
        core.services["radio"] = hub
    return hub
