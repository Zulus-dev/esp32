# hardware/link_uart.py — Node A link (binary SLIP only)
#
# RAM: static decoder; no str on wire; poll drains FIFO, no backlog list.

import time
from machine import UART

from config import Config
from lib.slip_tlv import SlipDecoder, build_tlv
from protocol import TLV_STATUS, TLV_KEEPALIVE, TLV_RSSI, TLV_PKT, TLV_NRF_EVENT, TLV_WIFI_FRAME, TLV_BLE_ADV, TLV_SCAN_RESULT, ST_BOOT_OK


def _ticks():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)


def _diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except Exception:
        return a - b


class LinkClient:
    def __init__(self):
        self.uart = UART(
            Config.LINK_UART_ID,
            baudrate=Config.LINK_UART_BAUD,
            tx=Config.LINK_UART_TX_PIN,
            rx=Config.LINK_UART_RX_PIN,
            timeout=0,
            timeout_char=0,
        )
        self._dec = SlipDecoder(128)
        self.reset_state()

    def reset_state(self):
        self.last_status_code = -1
        self.last_status_arg = 0
        self.last_status_ms = 0
        self.last_ka_ms = 0
        self.ka_free_kb = 0
        self.ka_flags = 0
        self.ka_state = 0
        self.boot_ok = False
        self.last_rssi = None
        self.last_rssi_freq = 0
        self.rx_frames = 0
        self.rx_crc_fail = 0
        self.events = [None] * 24
        self.event_pos = 0

    def flush_rx(self):
        try:
            n = self.uart.any()
            if n:
                self.uart.read(n)
        except Exception:
            pass
        self._dec = SlipDecoder(128)

    def send(self, msg_type, payload=b""):
        frame = build_tlv(msg_type, payload)
        try:
            self.uart.write(b"\xc0")
            self.uart.write(frame)
            return True
        except Exception:
            return False

    def send_cmd(self, cmd, payload=b""):
        return self.send(cmd, payload)

    def poll(self):
        try:
            n = self.uart.any()
        except Exception:
            return None
        if not n:
            return None
        try:
            data = self.uart.read(min(n, 128))
        except Exception:
            return None
        if not data:
            return None
        last = None
        for byte in data:
            if not self._dec.feed(byte):
                continue
            parsed = self._dec.parse_last()
            if not parsed:
                self.rx_crc_fail += 1
                continue
            mtype, payload = parsed
            self._handle(mtype, payload)
            self.rx_frames += 1
            last = mtype
        return last

    def _push_event(self, kind, a=0, b=0, c=0, data=None):
        raw = ""
        if data:
            try:
                raw = bytes(data[:24]).hex()
            except Exception:
                raw = ""
        self.events[self.event_pos] = {"t": _ticks(), "k": kind, "a": a, "b": b, "c": c, "hex": raw}
        self.event_pos = (self.event_pos + 1) % len(self.events)

    def clear_events(self):
        self.events = [None] * len(self.events)
        self.event_pos = 0

    def event_snapshot(self):
        out = []
        n = len(self.events)
        for i in range(n):
            e = self.events[(self.event_pos + i) % n]
            if e is not None:
                out.append(e)
        return out

    def _handle(self, mtype, payload):
        now = _ticks()
        if mtype == TLV_STATUS and payload is not None and len(payload) >= 1:
            self.last_status_code = payload[0] & 0xFF
            arg = 0
            if len(payload) >= 3:
                arg = ((payload[1] & 0xFF) << 8) | (payload[2] & 0xFF)
            self.last_status_arg = arg
            self.last_status_ms = now
            if self.last_status_code == ST_BOOT_OK:
                self.boot_ok = True
        elif mtype == TLV_KEEPALIVE and payload is not None and len(payload) >= 4:
            self.ka_free_kb = ((payload[0] & 0xFF) << 8) | (payload[1] & 0xFF)
            self.ka_flags = payload[2] & 0xFF
            self.ka_state = payload[3] & 0xFF
            self.last_ka_ms = now
            self.boot_ok = True
        elif mtype == TLV_RSSI and payload is not None and len(payload) >= 5:
            self.last_rssi_freq = (
                ((payload[0] & 0xFF) << 24)
                | ((payload[1] & 0xFF) << 16)
                | ((payload[2] & 0xFF) << 8)
                | (payload[3] & 0xFF)
            )
            r = payload[4] & 0xFF
            if r >= 128:
                r -= 256
            self.last_rssi = r
            self._push_event("rssi", self.last_rssi_freq, r, 0, None)
        elif mtype in (TLV_PKT, TLV_NRF_EVENT, TLV_WIFI_FRAME, TLV_BLE_ADV, TLV_SCAN_RESULT) and payload is not None:
            a = payload[0] if len(payload) > 0 else 0
            b = payload[1] if len(payload) > 1 else 0
            c = payload[2] if len(payload) > 2 else 0
            self._push_event("evt", a, b, c, payload)

    def ka_age_ms(self):
        return _diff(_ticks(), self.last_ka_ms) if self.last_ka_ms else 0

    def status_age_ms(self):
        return _diff(_ticks(), self.last_status_ms) if self.last_status_ms else 0

    def online(self, stale_ms=6000):
        if not self.boot_ok:
            return False
        now = _ticks()
        if self.last_ka_ms and _diff(now, self.last_ka_ms) < stale_ms:
            return True
        if self.last_status_ms and _diff(now, self.last_status_ms) < stale_ms:
            return True
        return False

    async def wait_online(self, timeout_ms=5000):
        import asyncio
        end = _ticks() + timeout_ms
        while _diff(end, _ticks()) > 0:
            self.poll()
            if self.online(stale_ms=timeout_ms):
                return True
            await asyncio.sleep_ms(40)
        return self.online()

    async def wait_status(self, codes, timeout_ms=2000):
        import asyncio
        if not isinstance(codes, (tuple, list)):
            codes = (codes,)
        self.last_status_code = -1
        end = _ticks() + timeout_ms
        while _diff(end, _ticks()) > 0:
            self.poll()
            if self.last_status_code in codes:
                return True
            await asyncio.sleep_ms(30)
        return False

    def close(self):
        self.reset_state()
        try:
            self.uart.deinit()
        except Exception:
            pass
