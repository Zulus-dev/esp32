# hardware/radio_hub.py — binary link supervisor (lazy)
#
# RAM RULES:
# 1. Create only on demand; node_b_off → drop link + cancel RX + gc.
# 2. UART = codes only (protocol.py). No str on wire.
# 3. last_error = int ERR_* (not str).
# 4. OLED update only on state change.
# 5. snapshot = small dict of ints/bools for web (JSON only at HTTP edge).
# 6. Never uart.deinit() in RX loop — kills link permanently.

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
        # Poll only. Never deinit/recreate UART here.
        # When RF module is scanning/sniffing, poll harder so TLV_RSSI/PKT are not lost
        # under concurrent WiFi/HTTP load on Node A.
        while self.link is not None:
            try:
                pwr = self._pwr()
                hot = pwr.powered and not self.link.online()
                rf_busy = self.mod_state >= 3  # scan / sniff
                if hot:
                    loops, gap, outer = 12, 15, 80
                elif rf_busy:
                    loops, gap, outer = 4, 20, 60
                else:
                    loops, gap, outer = 2, 40, 200
                for _ in range(loops):
                    if self.link is None:
                        break
                    self.link.poll()
                    await asyncio.sleep_ms(gap)
                self._refresh_ui(False)
                await asyncio.sleep_ms(outer)
            except Exception:
                await asyncio.sleep_ms(150)

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
        gc.collect()
        pwr = self._pwr()
        link = self._ensure_link()
        # Listen BEFORE power — do not flush after B may already be talking
        self._start_rx()
        if not pwr.powered:
            link.reset_state()
            await pwr.node_b_on()
        # if already powered: keep counters, do not flush (would drop in-flight KA)
        ok = await link.wait_online(timeout_ms=10000)
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
            await self.link.wait_online(timeout_ms=3000)
        if not pwr.rf_powered:
            await pwr.rf_on()
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
            cmd, _, _ = self._bufs()
            cmd[0] = OP_CONFIG
            cmd[1] = 0
            link.send_cmd(mod_id, memoryview(cmd)[:2])
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
                cmd, _, _ = self._bufs()
                cmd[0] = OP_STOP
                link.send_cmd(self.active_mod, memoryview(cmd)[:1])
                await asyncio.sleep_ms(20)
                link.send_cmd(CMD_STOP_ALL)
                await link.wait_status((ST_STOPPED, ST_MOD_OFF), timeout_ms=500)
            except Exception:
                pass
        self.active_mod = MOD_NONE
        self.mod_state = 0
        if link is not None:
            try:
                link.clear_last_raw()
            except Exception:
                pass
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

    # One-time fixed buffers — never reallocated
    _CMD = None
    _STATUS = None
    _TX = None  # OP_TX_RAW + up to 48 B payload

    def _bufs(self):
        if RadioHub._CMD is None:
            RadioHub._CMD = bytearray(16)
            # header 40 + 8*12 events + 52 last_raw trailer
            # header40 + events96 + last_raw54 + rssi_samples(1+16*5=81) ≈ 271
            RadioHub._STATUS = bytearray(40 + 8 * 12 + 54 + 1 + 16 * 5)
            RadioHub._TX = bytearray(56)
        return RadioHub._CMD, RadioHub._STATUS, RadioHub._STATUS

    async def command_bin(self, action, mod_id=MOD_SUBGHZ, freq_khz=433920, preset=0, thresh=72, raw=None):
        """Binary command path — no dict. raw = optional bytes for WA_TX_RAW."""
        from protocol import (
            WA_NODE_B_ON, WA_NODE_B_OFF, WA_MOD_ON, WA_MOD_OFF,
            WA_SCAN, WA_LISTEN, WA_CAPTURE, WA_RSSI_ONCE, WA_REPLAY, WA_STOP,
            WA_HID_SNIFF, WA_HONEYPOT, WA_WIFI_SCAN, WA_BLE_SCAN, WA_TX_RAW,
            OP_TX_RAW, OP_CONFIG,
        )
        if action == WA_NODE_B_ON or action == "node_b_on":
            await self.node_b_on()
            return
        if action == WA_NODE_B_OFF or action == "node_b_off":
            await self.node_b_off()
            return
        if action == WA_MOD_ON or action == "module_on" or action == "rf_on":
            await self.module_on(mod_id if action != "rf_on" else (self.active_mod or MOD_SUBGHZ))
            return
        if action in (WA_MOD_OFF, "module_off", "rf_off", "stop_all"):
            await self.module_off(rail_off=True)
            return

        # map str → WA
        if isinstance(action, str):
            smap = {
                "scan": WA_SCAN, "sniff": WA_LISTEN, "listen": WA_LISTEN,
                "capture": WA_CAPTURE, "rssi_once": WA_RSSI_ONCE, "replay": WA_REPLAY,
                "stop": WA_STOP, "hid_sniff": WA_HID_SNIFF, "honeypot": WA_HONEYPOT,
                "wifi_scan": WA_WIFI_SCAN, "ble_scan": WA_BLE_SCAN,
            }
            action = smap.get(action, 0)
            if not action:
                self.err = ERR_FAULT
                return

        need_mod = action in (
            WA_SCAN, WA_LISTEN, WA_CAPTURE, WA_RSSI_ONCE, WA_REPLAY, WA_STOP,
            WA_HID_SNIFF, WA_HONEYPOT, WA_WIFI_SCAN, WA_BLE_SCAN,
        )
        if need_mod and self.active_mod != mod_id and action != WA_STOP:
            await self.module_on(mod_id)
        link = self._ensure_link()
        cmd, _, _ = self._bufs()

        if action == WA_STOP:
            cmd[0] = OP_STOP
            link.send_cmd(mod_id, memoryview(cmd)[:1])
            self.mod_state = 1
            try:
                link.clear_last_raw()
            except Exception:
                pass
        elif action == WA_RSSI_ONCE:
            fk = int(freq_khz)
            cmd[0] = OP_ONCE
            cmd[1] = 0
            cmd[2] = (fk >> 24) & 0xFF
            cmd[3] = (fk >> 16) & 0xFF
            cmd[4] = (fk >> 8) & 0xFF
            cmd[5] = fk & 0xFF
            link.send_cmd(mod_id, memoryview(cmd)[:6])
        elif action == WA_CAPTURE:
            fk = int(freq_khz)
            cmd[0] = OP_ONCE
            cmd[1] = 1
            cmd[2] = (fk >> 24) & 0xFF
            cmd[3] = (fk >> 16) & 0xFF
            cmd[4] = (fk >> 8) & 0xFF
            cmd[5] = fk & 0xFF
            cmd[6] = int(thresh) & 0x7F
            # optional preset config
            cmd[8] = OP_CONFIG
            cmd[9] = int(preset) & 0xFF
            cmd[10] = cmd[2]
            cmd[11] = cmd[3]
            cmd[12] = cmd[4]
            cmd[13] = cmd[5]
            link.send_cmd(mod_id, memoryview(cmd)[8:14])
            await asyncio.sleep_ms(40)
            link.send_cmd(mod_id, memoryview(cmd)[:7])
            self.mod_state = 4
        elif action == WA_REPLAY:
            cmd[0] = OP_REPLAY
            link.send_cmd(mod_id, memoryview(cmd)[:1])
        elif action == WA_TX_RAW and raw is not None and len(raw) > 0:
            fk = int(freq_khz)
            cmd[0] = OP_CONFIG
            cmd[1] = int(preset) & 0xFF
            cmd[2] = (fk >> 24) & 0xFF
            cmd[3] = (fk >> 16) & 0xFF
            cmd[4] = (fk >> 8) & 0xFF
            cmd[5] = fk & 0xFF
            link.send_cmd(mod_id, memoryview(cmd)[:6])
            await asyncio.sleep_ms(40)
            if RadioHub._TX is None:
                RadioHub._TX = bytearray(56)
            tx = RadioHub._TX
            n = len(raw)
            if n > 48:
                n = 48
            tx[0] = OP_TX_RAW
            tx[1:1 + n] = raw[:n]
            link.send_cmd(mod_id, memoryview(tx)[:1 + n])
            self.mod_state = 2
        else:
            fk = int(freq_khz)
            cmd[0] = OP_START
            if mod_id == MOD_SUBGHZ:
                cmd[1] = SUB_MODE_SCAN if action == WA_SCAN else SUB_MODE_SNIFF
                cmd[2] = (fk >> 24) & 0xFF
                cmd[3] = (fk >> 16) & 0xFF
                cmd[4] = (fk >> 8) & 0xFF
                cmd[5] = fk & 0xFF
                cmd[6] = int(preset) & 0xFF
                cmd[7] = int(thresh) & 0x7F
                # config preset first
                cmd[8] = OP_CONFIG
                cmd[9] = int(preset) & 0xFF
                cmd[10] = cmd[2]
                cmd[11] = cmd[3]
                cmd[12] = cmd[4]
                cmd[13] = cmd[5]
                link.send_cmd(mod_id, memoryview(cmd)[8:14])
                await asyncio.sleep_ms(40)
                link.send_cmd(mod_id, memoryview(cmd)[:8])
            elif mod_id == MOD_NRF:
                cmd[0] = OP_START
                cmd[1] = NRF_MODE_HONEYPOT if action == WA_HONEYPOT else NRF_MODE_HID
                link.send_cmd(mod_id, memoryview(cmd)[:2])
            elif mod_id == MOD_WIFI:
                cmd[0] = OP_START
                cmd[1] = WIFI_MODE_SCAN
                link.send_cmd(mod_id, memoryview(cmd)[:2])
            elif mod_id == MOD_BLE:
                cmd[0] = OP_START
                cmd[1] = BLE_MODE_SCAN
                link.send_cmd(mod_id, memoryview(cmd)[:2])
            self.mod_state = 3 if action in (WA_SCAN, WA_WIFI_SCAN, WA_BLE_SCAN) else 4
        for _ in range(4):
            link.poll()
            await asyncio.sleep_ms(15)
        self._refresh_ui(True)
        gc.collect()

    async def command(self, data):
        """Legacy entry for OLED menu (dict once). Prefer command_bin."""
        if not isinstance(data, dict):
            return
        action = data.get("action", "")
        mod = data.get("module", "subghz")
        mod_id = MOD_SUBGHZ
        if mod == "nrf":
            mod_id = MOD_NRF
        elif mod == "wifi":
            mod_id = MOD_WIFI
        elif mod == "ble":
            mod_id = MOD_BLE
        await self.command_bin(
            action, mod_id,
            int(data.get("freq_khz") or 433920),
            int(data.get("preset") or 0),
            int(data.get("thresh") or 72),
        )

    def pack_status(self, include_events=True, max_events=8):
        """Fill static STATUS buffer. Returns memoryview.
        Header 40 B + n*16 event records. Zero dict.
        """
        from protocol import WEB_MAGIC, WEB_VER
        _, st, _ = self._bufs()
        pwr = self._pwr()
        powered = 1 if pwr.powered else 0
        rf_on = 1 if pwr.rf_powered else 0
        link = self.link
        online = 1 if (link and link.online()) else 0
        boot = 1 if (link and link.boot_ok) else 0
        pending = 1 if (powered and not online) else 0
        letter = self._letter(pwr, rf_on)
        letter_u = ord(letter[0]) if letter else ord(".")
        ms = self.mod_state if 0 <= self.mod_state < 5 else 0

        # magic be
        st[0] = (WEB_MAGIC >> 8) & 0xFF
        st[1] = WEB_MAGIC & 0xFF
        st[2] = WEB_VER
        st[3] = 0  # flags
        st[4] = online
        st[5] = boot
        st[6] = pending
        st[7] = powered
        st[8] = rf_on
        st[9] = self.err & 0xFF
        st[10] = self.active_mod & 0xFF
        st[11] = ms & 0xFF
        st[12] = letter_u & 0xFF
        st[13] = 0
        st[14] = 0
        st[15] = 0
        rx = link.rx_frames if link else 0
        st[16] = (rx >> 24) & 0xFF
        st[17] = (rx >> 16) & 0xFF
        st[18] = (rx >> 8) & 0xFF
        st[19] = rx & 0xFF
        crc = link.rx_crc_fail if link else 0
        st[20] = (crc >> 8) & 0xFF
        st[21] = crc & 0xFF
        raw = link.raw_rx_bytes if link else 0
        st[22] = (raw >> 24) & 0xFF
        st[23] = (raw >> 16) & 0xFF
        st[24] = (raw >> 8) & 0xFF
        st[25] = raw & 0xFF
        kaf = link.ka_free_kb if link else 0
        st[26] = (kaf >> 8) & 0xFF
        st[27] = kaf & 0xFF
        age = link.ka_age_ms() if link else 0
        if age > 65535:
            age = 65535
        st[28] = (age >> 8) & 0xFF
        st[29] = age & 0xFF
        sage = link.status_age_ms() if link else 0
        if sage > 65535:
            sage = 65535
        st[30] = (sage >> 8) & 0xFF
        st[31] = sage & 0xFF
        st[32] = (link.ka_state if link else 0) & 0xFF
        st[33] = (link.ka_flags if link else 0) & 0xFF
        lr = link.last_rssi if link and link.last_rssi is not None else -128
        st[34] = lr & 0xFF
        st[35] = 0
        fk = link.last_rssi_freq if link else 0
        st[36] = (fk >> 24) & 0xFF
        st[37] = (fk >> 16) & 0xFF
        st[38] = (fk >> 8) & 0xFF
        st[39] = fk & 0xFF
        # events packed after header — store count at rewrite of byte 13-14 as event_count, 15 as n_events
        ec = link.event_count if link else 0
        st[13] = (ec >> 8) & 0xFF
        st[14] = ec & 0xFF
        n_ev = 0
        if include_events and link:
            n_ev = link.pack_events_wire(memoryview(st)[40:], max_events)
            if n_ev > 8:
                n_ev = 8
            st[15] = n_ev & 0xFF
        else:
            st[15] = 0
        # last_raw trailer at 40+8*12 = 136: len_u8, rssi_i8, freq_u32be, data[48]
        base = 40 + 8 * 12
        plen = 0
        if link is not None:
            try:
                plen = link._pkt_len & 0xFF
                if plen > 48:
                    plen = 48
                st[base] = plen
                st[base + 1] = link._pkt_rssi & 0xFF
                fk2 = link._pkt_freq & 0xFFFFFFFF
                st[base + 2] = (fk2 >> 24) & 0xFF
                st[base + 3] = (fk2 >> 16) & 0xFF
                st[base + 4] = (fk2 >> 8) & 0xFF
                st[base + 5] = fk2 & 0xFF
                if plen:
                    st[base + 6:base + 6 + plen] = link._pkt[:plen]
            except Exception:
                st[base] = 0
                plen = 0
        else:
            st[base] = 0
        # RSSI sample batch for waterfall: after last_raw, count_u8 + n*5
        raw_end = base + 6 + plen
        n_rs = 0
        if link is not None:
            try:
                max_rs = min(16, (len(st) - raw_end - 1) // 5)
                if max_rs > 0:
                    n_rs = link.pack_rssi_samples(memoryview(st)[raw_end + 1:raw_end + 1 + max_rs * 5])
                    st[raw_end] = n_rs & 0xFF
                else:
                    st[raw_end] = 0
            except Exception:
                st[raw_end] = 0
                n_rs = 0
        else:
            st[raw_end] = 0
        return memoryview(st)[:raw_end + 1 + n_rs * 5]

    def snapshot(self):
        """Deprecated for HTTP. Kept minimal for OLED radio_actions only."""
        pwr = self._pwr()
        powered = bool(pwr.powered)
        rf_on = bool(pwr.rf_powered)
        link = self.link
        online = bool(link and link.online())
        letter = self._letter(pwr, rf_on)
        ms = self.mod_state if 0 <= self.mod_state < 5 else 0
        names = {0: "none", 1: "subghz", 0x20: "nrf", 0x30: "wifi", 0x40: "ble"}
        states = ("idle", "armed", "on", "scan", "sniff")
        # minimal — no events list
        return {
            "ok": True,
            "link": {"online": online, "rx_frames": link.rx_frames if link else 0},
            "power": {"powered": powered, "rf_on": rf_on, "err": self.err},
            "module": {"name": names.get(self.active_mod, "none"), "state": states[ms], "letter": letter},
        }

    def clear_log(self):
        if self.link:
            self.link.clear_events()
        return True

    def save_session(self, clear=False):
        """Write human-readable TSV (#COLIBRY1). No JSON. Returns (ok, path_or_err, bytes)."""
        if not self.link:
            return (False, "no_link", 0)
        try:
            import uos, time
        except Exception:
            return (False, "no_fs", 0)
        try:
            uos.mkdir("/sessions")
        except Exception:
            pass
        names = {0: "none", 1: "subghz", 0x20: "nrf", 0x30: "wifi", 0x40: "ble"}
        mod_name = names.get(self.active_mod, "radio")
        try:
            ts = time.localtime()
            name = "/sessions/%04d%02d%02d_%02d%02d%02d_%s.txt" % (
                ts[0], ts[1], ts[2], ts[3], ts[4], ts[5], mod_name,
            )
        except Exception:
            name = "/sessions/session_%s.txt" % mod_name
        try:
            with open(name, "w") as f:
                total = self.link.write_session_text(f, mod_name, 12)
        except Exception as ex:
            return (False, str(ex), 0)
        if clear:
            self.link.clear_events()
        return (True, name, total)

    def close(self):
        self._drop_link()
        self.err = ERR_NONE


def get_radio_hub(core):
    hub = core.services.get("radio")
    if hub is None:
        hub = RadioHub(core)
        core.services["radio"] = hub
    return hub
