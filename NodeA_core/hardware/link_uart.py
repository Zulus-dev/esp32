# link_uart.py — Node A. Fixed rings only. No growing alloc on RX.
# Arena (once):
#   dec 80 + chunk 64 + pend 2*64 + ev 12*12 + rssi 16*5 + pipe 8*56 ≈ 1 KB
# Full RAW lives on NodeB. NodeA only pipes short STREAM chunks to phone (OP_DUMP).
# No full-frame relay_cap on NodeA.
import time
from machine import UART, Pin
from array import array
from config import Config
from lib.slip_tlv import SlipDecoder, build_tlv
from protocol import (
    TLV_STATUS, TLV_KEEPALIVE, TLV_RSSI, TLV_PKT,
    TLV_NRF_EVENT, TLV_WIFI_FRAME, TLV_BLE_ADV, TLV_SCAN_RESULT,
    TLV_STREAM_META, TLV_STREAM_DATA, TLV_STREAM_END,
    ST_BOOT_OK, ST_MOD_ON, ST_RF_ON_DONE, ST_RF_OFF_DONE,
    STREAM_PIPE_CHUNKS, STREAM_DATA_MAX,
)

_PEND_N = 2
_PEND_SZ = 64
_EV_N = 12
_EV_STR = 12  # kind,dlen,a0..a3,b0,b1,c0,c1  (10) + pad 2 — no data in ring
# Recent RSSI samples for spectrum waterfall (phone-side). 16 × 5 B = 80 B.
_RSSI_N = 16
_RSSI_STR = 5            # freq_u32be + rssi_i8
# STREAM pipe: short chunks only (seq+payload), not full 8 KB frame
_PIPE_N = int(STREAM_PIPE_CHUNKS)
_PIPE_STR = 1 + int(STREAM_DATA_MAX)  # seq + ≤55 data

K_PKT, K_ST, K_NRF, K_WIFI, K_BLE, K_SCAN = 2, 3, 4, 5, 6, 7


def _ticks():
    return time.ticks_ms()


def _diff(a, b):
    return time.ticks_diff(a, b)


class LinkClient:
    def __init__(self):
        self.uart = UART(
            Config.LINK_UART_ID,
            baudrate=Config.LINK_UART_BAUD,
            tx=Pin(Config.LINK_UART_TX_PIN),
            rx=Pin(Config.LINK_UART_RX_PIN),
        )
        self._dec = SlipDecoder(80)
        self._chunk = bytearray(64)
        self._p_type = array("B", [0] * _PEND_N)
        self._p_len = array("B", [0] * _PEND_N)
        self._p_data = bytearray(_PEND_N * _PEND_SZ)
        self._p_head = 0
        self._p_tail = 0
        self._p_count = 0
        self._ev = bytearray(_EV_N * _EV_STR)
        self._ev_pos = 0
        self._ev_filled = 0
        # RSSI sample ring for spectrum waterfall
        self._rssi_ring = bytearray(_RSSI_N * _RSSI_STR)
        self._rssi_pos = 0
        self._rssi_filled = 0
        # STREAM pipe (translator): meta + short DATA ring — no full-frame slab
        self._pipe_data = bytearray(_PIPE_N * _PIPE_STR)
        self._pipe_len = array("B", [0] * _PIPE_N)
        self._pipe_head = 0
        self._pipe_tail = 0
        self._pipe_count = 0
        # Full RAW only on NodeB (dual slots). NodeA = short pipe ring only.
        self.capture_ready = False  # NodeB has FULL frame; phone may OP_DUMP
        self.capture_seq = 0        # bumps on notify STREAM_END (no DATA)
        self._pipe_active = False   # True while HTTP /capture drains OP_DUMP
        self._pipe_meta = False
        self._pipe_end = False
        self._pipe_overflow = False
        self._pipe_freq = 0
        self._pipe_rssi = 0
        self._pipe_bits = 0
        self._pipe_total = 0
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
        self.raw_rx_bytes = 0
        self.event_count = 0
        self._ev_pos = 0
        self._ev_filled = 0
        self._p_head = 0
        self._p_tail = 0
        self._p_count = 0
        self._rssi_pos = 0
        self._rssi_filled = 0
        try:
            for i in range(len(self._rssi_ring)):
                self._rssi_ring[i] = 0
        except Exception:
            pass

    def flush_rx(self):
        try:
            n = self.uart.any()
            if n:
                self.uart.read(n)
        except Exception:
            pass
        self._dec._len = 0
        self._dec._esc = False
        self._p_head = self._p_tail = self._p_count = 0

    def send(self, msg_type, payload=b""):
        try:
            self.uart.write(build_tlv(msg_type, payload))
            return True
        except Exception:
            return False

    def send_cmd(self, cmd, payload=b""):
        return self.send(cmd, payload)

    def _pend_push(self, msg_type, mv):
        if self._p_count >= _PEND_N:
            self._p_tail = (self._p_tail + 1) % _PEND_N
            self._p_count -= 1
        i = self._p_head
        n = len(mv) if mv is not None else 0
        if n > _PEND_SZ:
            n = _PEND_SZ
        self._p_type[i] = msg_type & 0xFF
        self._p_len[i] = n
        off = i * _PEND_SZ
        if n:
            self._p_data[off:off + n] = mv[:n]
        self._p_head = (i + 1) % _PEND_N
        self._p_count += 1

    def _pend_pop(self):
        if self._p_count == 0:
            return None
        i = self._p_tail
        n = self._p_len[i]
        off = i * _PEND_SZ
        item = (self._p_type[i], memoryview(self._p_data)[off:off + n])
        self._p_tail = (i + 1) % _PEND_N
        self._p_count -= 1
        return item

    def poll(self):
        while self._p_count:
            it = self._pend_pop()
            if not it:
                break
            self._handle(it[0], it[1])
            self.rx_frames += 1
        n = self.uart.any()
        if not n:
            return None
        nbytes = self.uart.readinto(self._chunk) if hasattr(self.uart, "readinto") else 0
        if not nbytes:
            raw = self.uart.read(64 if n > 64 else n)
            if not raw:
                return None
            mv = raw
            nbytes = len(raw)
        else:
            mv = memoryview(self._chunk)[:nbytes]
        self.raw_rx_bytes += nbytes
        last = None
        for b in mv:
            if self._dec.feed(b):
                parsed = self._dec.parse_last()
                if parsed is None:
                    self.rx_crc_fail += 1
                    continue
                self._pend_push(parsed[0], parsed[1])
        while self._p_count:
            it = self._pend_pop()
            if not it:
                break
            self._handle(it[0], it[1])
            self.rx_frames += 1
            last = it[0]
        return last

    def _push_ev(self, kind, a=0, b=0, c=0):
        off = self._ev_pos * _EV_STR
        ev = self._ev
        ev[off] = kind & 0xFF
        ev[off + 1] = 0
        a &= 0xFFFFFFFF
        ev[off + 2] = (a >> 24) & 0xFF
        ev[off + 3] = (a >> 16) & 0xFF
        ev[off + 4] = (a >> 8) & 0xFF
        ev[off + 5] = a & 0xFF
        b &= 0xFFFF
        ev[off + 6] = (b >> 8) & 0xFF
        ev[off + 7] = b & 0xFF
        c &= 0xFFFF
        ev[off + 8] = (c >> 8) & 0xFF
        ev[off + 9] = c & 0xFF
        self._ev_pos = (self._ev_pos + 1) % _EV_N
        if self._ev_filled < _EV_N:
            self._ev_filled += 1
        if self.event_count < 65535:
            self.event_count += 1

    def _push_rssi_sample(self, freq, rssi):
        """Ring of recent RSSI points for spectrum waterfall on phone."""
        off = self._rssi_pos * _RSSI_STR
        r = self._rssi_ring
        f = freq & 0xFFFFFFFF
        r[off] = (f >> 24) & 0xFF
        r[off + 1] = (f >> 16) & 0xFF
        r[off + 2] = (f >> 8) & 0xFF
        r[off + 3] = f & 0xFF
        r[off + 4] = rssi & 0xFF
        self._rssi_pos = (self._rssi_pos + 1) % _RSSI_N
        if self._rssi_filled < _RSSI_N:
            self._rssi_filled += 1

    def pack_rssi_samples(self, out):
        """Copy recent RSSI samples into out (5 B each). Returns count."""
        n = self._rssi_filled
        if n > _RSSI_N:
            n = _RSSI_N
        if n == 0 or out is None or len(out) < 5:
            return 0
        take = n if (n * 5) <= len(out) else (len(out) // 5)
        start = (self._rssi_pos - take) % _RSSI_N
        w = 0
        for i in range(take):
            off = ((start + i) % _RSSI_N) * _RSSI_STR
            base = w * 5
            out[base:base + 5] = self._rssi_ring[off:off + 5]
            w += 1
        return w

    def clear_events(self):
        self._ev_pos = 0
        self._ev_filled = 0
        self.event_count = 0
        self._rssi_pos = 0
        self._rssi_filled = 0
        try:
            for i in range(len(self._rssi_ring)):
                self._rssi_ring[i] = 0
        except Exception:
            pass

    def clear_sticky(self):
        """Drop sticky RSSI + close stream pipe. No full-frame slab on A."""
        self._rssi_pos = 0
        self._rssi_filled = 0
        self.last_rssi = None
        self.capture_ready = False
        self._pipe_reset()
        try:
            for i in range(len(self._rssi_ring)):
                self._rssi_ring[i] = 0
        except Exception:
            pass

    def drop_relay(self):
        self.clear_sticky()

    def clear_last_raw(self):
        self.clear_sticky()

    def _pipe_reset(self):
        self._pipe_head = 0
        self._pipe_tail = 0
        self._pipe_count = 0
        self._pipe_meta = False
        self._pipe_end = False
        self._pipe_overflow = False
        self._pipe_freq = 0
        self._pipe_rssi = 0
        self._pipe_bits = 0
        self._pipe_total = 0

    def pipe_begin(self):
        """Arm translator for OP_DUMP session (HTTP /capture)."""
        self._pipe_reset()
        self._pipe_active = True

    def pipe_end(self):
        self._pipe_active = False

    def _pipe_push(self, payload):
        """
        Queue one STREAM_DATA chunk (seq + bytes).
        If ring full: drop oldest and mark overflow — frame must be rejected by API.
        """
        if payload is None or len(payload) < 1:
            return
        n = len(payload)
        if n > _PIPE_STR:
            n = _PIPE_STR
        if self._pipe_count >= _PIPE_N:
            self._pipe_tail = (self._pipe_tail + 1) % _PIPE_N
            self._pipe_count -= 1
            self._pipe_overflow = True
        i = self._pipe_head
        off = i * _PIPE_STR
        self._pipe_data[off:off + n] = payload[:n]
        self._pipe_len[i] = n
        self._pipe_head = (i + 1) % _PIPE_N
        self._pipe_count += 1

    def pipe_pop(self):
        """Return memoryview of next DATA chunk or None."""
        if self._pipe_count == 0:
            return None
        i = self._pipe_tail
        n = self._pipe_len[i]
        off = i * _PIPE_STR
        mv = memoryview(self._pipe_data)[off:off + n]
        self._pipe_tail = (i + 1) % _PIPE_N
        self._pipe_count -= 1
        return mv

    def full_capture(self):
        """No full-frame on NodeA — use pipe + OP_DUMP."""
        return None

    def pack_events_wire(self, out, max_n=8):
        """Copy ring into out as 12-byte records. Returns count."""
        n = self._ev_filled
        if n == 0:
            return 0
        take = max_n if max_n < n else n
        start = (self._ev_pos - take) % _EV_N
        w = 0
        for i in range(take):
            off = ((start + i) % _EV_N) * _EV_STR
            base = w * 12
            if base + 12 > len(out):
                break
            if self._ev[off] == 0:
                continue
            out[base:base + 12] = self._ev[off:off + 12]
            w += 1
        return w

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
            elif self.last_status_code not in (ST_BOOT_OK, ST_MOD_ON, ST_RF_ON_DONE, ST_RF_OFF_DONE):
                self._push_ev(K_ST, self.last_status_code, arg, 0)
        elif mtype == TLV_KEEPALIVE and payload is not None and len(payload) >= 4:
            self.ka_free_kb = ((payload[0] & 0xFF) << 8) | (payload[1] & 0xFF)
            self.ka_flags = payload[2] & 0xFF
            self.ka_state = payload[3] & 0xFF
            self.last_ka_ms = now
            self.boot_ok = True
        elif mtype == TLV_RSSI and payload is not None and len(payload) >= 5:
            self.last_rssi_freq = (
                ((payload[0] & 0xFF) << 24) | ((payload[1] & 0xFF) << 16)
                | ((payload[2] & 0xFF) << 8) | (payload[3] & 0xFF)
            )
            r = payload[4] & 0xFF
            if r >= 128:
                r -= 256
            self.last_rssi = r
            self._push_rssi_sample(self.last_rssi_freq, r)
        elif mtype == TLV_PKT and payload is not None:
            # No payload store on NodeA. Event only (freq + rssi + len meta).
            # Full data arrives via STREAM → relay → phone GET /capture.
            freq = 0
            if len(payload) >= 5:
                freq = ((payload[1] & 0xFF) << 24) | ((payload[2] & 0xFF) << 16) | ((payload[3] & 0xFF) << 8) | (payload[4] & 0xFF)
            b = payload[5] if len(payload) > 5 else 0
            if b >= 128:
                b -= 256
            c = payload[6] if len(payload) > 6 else 0
            self._push_ev(K_PKT, freq, b, c)
        elif mtype == TLV_STREAM_META and payload is not None and len(payload) >= 11:
            # Meta only on A — full RAW stays on NodeB.
            freq = (
                ((payload[1] & 0xFF) << 24) | ((payload[2] & 0xFF) << 16)
                | ((payload[3] & 0xFF) << 8) | (payload[4] & 0xFF)
            )
            r = payload[5] & 0xFF
            if r >= 128:
                r -= 256
            total = ((payload[6] & 0xFF) << 8) | (payload[7] & 0xFF)
            nbits = ((payload[8] & 0xFF) << 8) | (payload[9] & 0xFF)
            self._pipe_freq = freq
            self._pipe_rssi = r
            self._pipe_total = total
            self._pipe_bits = nbits
            if self._pipe_active:
                self._pipe_meta = True
                self._pipe_end = False
                self._pipe_head = self._pipe_tail = 0
                self._pipe_count = 0
                self._pipe_overflow = False
        elif mtype == TLV_STREAM_DATA and payload is not None and len(payload) >= 1:
            # Only while HTTP OP_DUMP is draining — short ring, no 8KB slab
            if self._pipe_active:
                self._pipe_push(payload)
        elif mtype == TLV_STREAM_END and payload is not None:
            if len(payload) >= 4:
                total = ((payload[0] & 0xFF) << 8) | (payload[1] & 0xFF)
                nbits = ((payload[2] & 0xFF) << 8) | (payload[3] & 0xFF)
                if total:
                    self._pipe_total = total
                if nbits:
                    self._pipe_bits = nbits
            if self._pipe_active:
                # OP_DUMP session end
                self._pipe_end = True
            else:
                # Notify-only: frame ready on NodeB → phone pulls OP_DUMP once
                self.capture_ready = True
                self.capture_seq = (self.capture_seq + 1) & 0xFFFF
                if self.capture_seq == 0:
                    self.capture_seq = 1
        elif mtype == TLV_NRF_EVENT and payload is not None:
            self._push_ev(K_NRF,
                          payload[0] if len(payload) > 0 else 0,
                          payload[2] if len(payload) > 2 else 0,
                          payload[4] if len(payload) > 4 else 0)
        elif mtype == TLV_WIFI_FRAME and payload is not None:
            self._push_ev(K_WIFI,
                          payload[0] if len(payload) > 0 else 0,
                          payload[1] if len(payload) > 1 else 0,
                          payload[2] if len(payload) > 2 else 0)
        elif mtype == TLV_BLE_ADV and payload is not None:
            c = payload[8] if len(payload) > 8 else 0
            if c >= 128:
                c -= 256
            self._push_ev(K_BLE,
                          payload[0] if len(payload) > 0 else 0,
                          payload[7] if len(payload) > 7 else 0, c)
        elif mtype == TLV_SCAN_RESULT and payload is not None:
            c = payload[3] if len(payload) > 3 else 0
            if c >= 128:
                c -= 256
            self._push_ev(K_SCAN,
                          payload[0] if len(payload) > 0 else 0,
                          payload[2] if len(payload) > 2 else 0, c)

    def ka_age_ms(self):
        return _diff(_ticks(), self.last_ka_ms) if self.last_ka_ms else 0

    def status_age_ms(self):
        return _diff(_ticks(), self.last_status_ms) if self.last_status_ms else 0

    def online(self, stale_ms=8000):
        if not self.boot_ok:
            return False
        now = _ticks()
        if self.last_ka_ms and _diff(now, self.last_ka_ms) < stale_ms:
            return True
        if self.last_status_ms and _diff(now, self.last_status_ms) < stale_ms:
            return True
        return False

    async def wait_online(self, timeout_ms=10000):
        import asyncio
        end = _ticks() + timeout_ms
        while _diff(end, _ticks()) > 0:
            self.poll()
            if self.online(stale_ms=timeout_ms):
                return True
            await asyncio.sleep_ms(30)
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
            await asyncio.sleep_ms(25)
        return False

    def close(self):
        self.reset_state()
        try:
            self.uart.deinit()
        except Exception:
            pass
