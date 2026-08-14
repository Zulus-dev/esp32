# Sub-GHz manager — spectrum + OOK RAW listen/capture/replay (Flipper-like).
# RAM: dual capture slots (ping-pong) via lib.capture_slots; stop → full unload.
#
# Capture rules:
# - FREE → RECORDING → FULL → SENDING → FREE (slot stays FULL until OP_DUMP).
# - Full RAW only on NodeB slots. NodeA pipes STREAM chunks to phone — no 8 KB slab.
# - TLV_PKT ≤48 is UI preview only.
# - Modes mutually exclusive: IDLE | SCAN | LISTEN | CAPTURE_ONCE.
# - cancel → settle → idle before SPI reuse.
#
import asyncio
import gc
from protocol import *
from lib import mempool
from lib.capture_slots import CaptureSlots

MODE_IDLE = 0
MODE_SCAN = 1
MODE_LISTEN = 2
MODE_CAPTURE = 3

from radio.managers.subghz_bands import _SCAN_WIN, _WIDE_FREQS

_RSSI_EMIT_DELTA = 3
_RSSI_EMIT_STRIDE = 4
_SCAN_MAX_POINTS = 400
_CAPTURE_WAIT_MS = 3000
# Max bits = slot bytes * 8; hard wall-clock in driver still caps fill time.
_MAX_CAPTURE_BITS = 65536


class Manager:
    def __init__(self, link):
        self.link = link
        self.drv = None
        self.task = None
        self.mode = MODE_IDLE
        self.buf = None          # legacy alias: last record buf (replay)
        self.caps = CaptureSlots()
        self.last_len = 0
        self.last_bits = 0
        self.freq = 433920
        self.start = 433000
        self.stop_f = 434800
        self.step = 25000
        self.preset = 0
        self.thresh = -72
        self.peak_rssi = -128
        self._last_sent_rssi = -128
        self._noise = -90
        self._tlv = bytearray(64)
        self._scan_wide = False

    async def _load(self):
        if self.drv:
            return True
        gc.collect()
        s0, s1, z = mempool.ensure_capture_slots()
        if s0 is None or s1 is None or z <= 0:
            self.link.send_status(ST_ERR, ERR_HW)
            return False
        self.caps.attach(s0, s1, z)
        self.buf = s0  # default alias until first capture
        from radio.drivers.cc1101_driver import CC1101
        self.drv = CC1101()
        pn, ver = self.drv.init()
        self.link.send_status(ST_MOD_ON, (pn & 0xFF) | ((ver & 0xFF) << 8))
        return True

    async def _cancel_task(self):
        t = self.task
        self.task = None
        self.mode = MODE_IDLE
        if t is None:
            return
        try:
            t.cancel()
        except Exception:
            pass
        await asyncio.sleep_ms(15)
        if self.drv:
            try:
                self.drv.idle()
            except Exception:
                pass

    def _feed_wdt(self):
        try:
            import machine
            # soft feed if kernel installed WDT on this thread context
            w = getattr(machine, "WDT", None)
        except Exception:
            pass
        gc.collect()

    async def stop(self):
        await self._cancel_task()
        if self.drv:
            try:
                self.drv.power_down()
            except Exception:
                pass
            try:
                self.drv.release_bus()
            except Exception:
                pass
        self.drv = None
        self.mode = MODE_IDLE
        self.caps.clear()
        self.buf = None
        mempool.drop_rf()
        try:
            import sys
            sys.modules.pop("radio.drivers.cc1101_driver", None)
        except Exception:
            pass
        gc.collect()
        gc.collect()

    def _send_rssi(self, fk, r, force=False):
        if not force:
            if abs(r - self._last_sent_rssi) < _RSSI_EMIT_DELTA:
                return False
        self._last_sent_rssi = r
        b = self._tlv
        b[0] = (fk >> 24) & 255
        b[1] = (fk >> 16) & 255
        b[2] = (fk >> 8) & 255
        b[3] = fk & 255
        b[4] = r & 255
        self.link.send_tlv(TLV_RSSI, memoryview(b)[:5])
        return True

    def _commit_capture(self, n_bytes, rssi_dbm, n_bits=0):
        """
        Finish RECORDING on NodeB dual-slot.
        Notify only (META+END, no DATA) — full RAW stays on B until OP_DUMP.
        NodeA is transport only: phone GET → OP_DUMP → chunk pipe → FREE.
        """
        n = int(n_bytes) if n_bytes else 0
        nb = int(n_bits) if n_bits else (n * 8)
        i = self.caps.finish_record(n, nb, MOD_SUBGHZ, self.freq, rssi_dbm, 0)
        if i < 0:
            return
        self.last_len = self.caps.last_len
        self.last_bits = self.caps.last_bits
        self.buf = self.caps.last_buf()
        self.caps.notify_ready(self.link)

    def _apply_band(self, center_khz, half_khz, step_khz):
        """Set scan window. half_khz==0 → Wide known-frequency table."""
        self.freq = int(center_khz) if center_khz else self.freq
        half = int(half_khz)
        if half <= 0:
            self._scan_wide = True
            self.start = 300000
            self.stop_f = 928000
            self.step = 0
            if not self.freq:
                self.freq = 433920
            return
        self._scan_wide = False
        self.step = max(5000, int(step_khz))
        half = max(self.step, half)
        self.start = max(300000, self.freq - half)
        self.stop_f = min(928000, self.freq + half)
        span = self.stop_f - self.start
        if span // self.step < 7:
            self.step = max(5000, span // 12) if span > 0 else self.step

    async def _probe_rssi(self, f):
        """IDLE → freq → RX → peak RSSI. Returns dBm."""
        self.drv.idle()
        self.drv.set_freq(f)
        self.drv.rx()
        await asyncio.sleep_ms(8)
        r = self.drv.rssi()
        r2 = self.drv.rssi()
        if r2 > r:
            r = r2
        await asyncio.sleep_ms(4)
        r3 = self.drv.rssi()
        if r3 > r:
            r = r3
        return r

    async def _scan(self):
        """Cyclic frequency finder. Wide = known ISM centers; band = linear window."""
        self.drv.apply_preset(0)
        self.drv.idle()
        while self.task:
            self._last_sent_rssi = -128
            points = 0
            if self._scan_wide:
                seq = _WIDE_FREQS
            else:
                # build linear list without heap-heavy range on tight RAM
                seq = None
            if seq is not None:
                for f in seq:
                    if not self.task:
                        break
                    try:
                        r = await self._probe_rssi(f)
                        force = (points % _RSSI_EMIT_STRIDE) == 0 or points == 0 or r >= self.thresh
                        self._send_rssi(f, r, force=force)
                        if r > self.peak_rssi:
                            self.peak_rssi = r
                    except Exception:
                        try:
                            self.drv.idle()
                        except Exception:
                            pass
                    points += 1
                    if (points & 3) == 0:
                        self._feed_wdt()
                        await asyncio.sleep_ms(0)
            else:
                f = self.start
                while f <= self.stop_f and points < _SCAN_MAX_POINTS:
                    if not self.task:
                        break
                    try:
                        r = await self._probe_rssi(f)
                        force = (points % _RSSI_EMIT_STRIDE) == 0 or points == 0 or r >= self.thresh
                        self._send_rssi(f, r, force=force)
                        if r > self.peak_rssi:
                            self.peak_rssi = r
                    except Exception:
                        try:
                            self.drv.idle()
                        except Exception:
                            pass
                    f += self.step
                    points += 1
                    if (points & 3) == 0:
                        await asyncio.sleep_ms(0)
            await asyncio.sleep_ms(40)

    def _transitions(self, buf, nbits):
        """Count 0<->1 edges in packed bits — rejects constant noise fill."""
        if nbits < 8:
            return 0
        prev = 1 if (buf[0] & 0x80) else 0
        edges = 0
        for i in range(1, nbits):
            by = buf[i >> 3]
            bit = 1 if (by & (0x80 >> (i & 7))) else 0
            if bit != prev:
                edges += 1
                prev = bit
        return edges

    async def _listen(self):
        """
        Flipper-like Read RAW (docs.flipper.net / RAW threshold RSSI):
          - gate = user RSSI threshold only
          - on RSSI >= gate → record GDO0 until gap (silence)
          - keep if nbits >= min (no edges/duty invent-filters)
          - notify META+END only; phone OP_DUMP streams actual_len then FREE
        Decode happens on phone after full frame — not during RX.
        """
        pid = self.preset if self.preset in (0, 1) else 0
        self.drv.apply_preset(pid)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        self.drv.rx()
        self._last_sent_rssi = -128
        was_below = True
        rssi_tick = 0
        # Flipper: after signal drops below threshold, keep recording briefly then stop.
        # We end on GDO0 silence gap (gap_end_ms), not a second software filter.
        _GAP_END_MS = 120
        _MIN_BITS = 48
        while self.task:
            try:
                if self.caps.busy:
                    await asyncio.sleep_ms(30)
                    continue
                r = self.drv.rssi()
                gate = self.thresh
                if r < gate:
                    was_below = True
                    rssi_tick = (rssi_tick + 1) & 3
                    if rssi_tick == 0:
                        self._send_rssi(self.freq, r, force=False)
                else:
                    self._send_rssi(self.freq, r, force=True)
                    if was_below:
                        was_below = False
                        si, buf = self.caps.begin_record()
                        if si < 0 or buf is None:
                            await asyncio.sleep_ms(40)
                        else:
                            # Already above gate — short wait_rssi, long gap end
                            nbits, peak = self.drv.raw_capture(
                                buf,
                                max_bits=min(len(buf) * 8, _MAX_CAPTURE_BITS),
                                thresh_dbm=gate,
                                timeout_ms=30,
                                gap_end_ms=_GAP_END_MS,
                            )
                            nbytes = (nbits + 7) // 8
                            # Only minimum length — Flipper keeps RAW, decode later
                            if nbits >= _MIN_BITS and nbytes > 0:
                                self._commit_capture(nbytes, peak if peak > -128 else r, nbits)
                                self._send_rssi(self.freq, peak if peak > -128 else r, force=True)
                            else:
                                self.caps.abort_record()
                            self._feed_wdt()
                            self.drv.idle()
                            self.drv.set_freq(self.freq)
                            self.drv.rx()
                            await asyncio.sleep_ms(50)
            except Exception:
                pass
            await asyncio.sleep_ms(12)

    async def _capture_once(self):
        """Wait up to _CAPTURE_WAIT_MS for a press, then RAW into a free slot."""
        self.drv.apply_preset(self.preset if self.preset in (0, 1) else 0)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        peak = -128
        elapsed = 0
        chunk = 350
        self.mode = MODE_CAPTURE
        si, buf = self.caps.begin_record()
        if si < 0 or buf is None:
            self.link.send_status(ST_ERR, ERR_BUSY)
            self.mode = MODE_IDLE
            return
        while elapsed < _CAPTURE_WAIT_MS:
            nbits, p = self.drv.raw_capture(
                buf, max_bits=min(len(buf) * 8, _MAX_CAPTURE_BITS),
                thresh_dbm=self.thresh,
                timeout_ms=chunk,
                gap_end_ms=30,
            )
            if p > peak:
                peak = p
            if nbits >= 48:
                self._commit_capture((nbits + 7) // 8, peak, nbits)
                self.link.send_status(ST_DONE, nbits if nbits < 65535 else 65535)
                self._feed_wdt()
                self.mode = MODE_IDLE
                return
            elapsed += chunk
            self._feed_wdt()
            await asyncio.sleep_ms(5)
        self.caps.abort_record()
        self._send_rssi(self.freq, peak, force=True)
        self.link.send_status(ST_DONE, 0)
        self.mode = MODE_IDLE

    async def handle(self, payload):
        op = payload[0] if payload else OP_CONFIG
        if op == OP_STOP:
            await self.stop()
            self.link.send_status(ST_MOD_OFF, MOD_SUBGHZ)
            return KA_STATE_IDLE

        if not await self._load():
            return KA_STATE_IDLE

        # Freq in payload[2..5] for CONFIG / ONCE / START / REPLAY (when present)
        if len(payload) >= 6:
            self.freq = (
                (payload[2] << 24) | (payload[3] << 16) | (payload[4] << 8) | payload[5]
            )

        if op == OP_ONCE and len(payload) >= 7 and payload[6]:
            tval = payload[6] & 0x7F
            if 40 <= tval <= 100:
                self.thresh = -tval

        if len(payload) > 1 and op == OP_CONFIG:
            self.preset = payload[1] & 0xFF
            self.drv.apply_preset(self.preset if self.preset in (0, 1, 2) else 0)
            self.drv.set_freq(self.freq)
            self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
            return KA_STATE_IDLE

        # Do NOT apply scan window here — only in SUB_MODE_SCAN (was breaking Listen freq)

        if op == OP_ONCE:
            await self._cancel_task()
            mode = payload[1] if len(payload) > 1 else 0
            if mode == 1:
                await self._capture_once()
            else:
                try:
                    self.drv.apply_preset(self.preset if self.preset in (0, 1) else 0)
                    self.drv.idle()
                    self.drv.set_freq(self.freq)
                    self.drv.rx()
                    await asyncio.sleep_ms(25)
                    self._send_rssi(self.freq, self.drv.rssi(), force=True)
                except Exception:
                    pass
                self.link.send_status(ST_DONE, MOD_SUBGHZ)
            self.mode = MODE_IDLE
            return KA_STATE_IDLE

        if op == OP_REPLAY and self.last_bits > 0:
            await self._cancel_task()
            try:
                self.drv.apply_preset(0)
                self.drv.set_freq(self.freq)
                rb = self.caps.last_buf() or self.buf
                if rb is not None:
                    self.drv.tx_async_bits(rb, self.last_bits, bit_us=280)
            except Exception:
                pass
            self.link.send_status(ST_DONE, MOD_SUBGHZ)
            return KA_STATE_TX

        if op == OP_DUMP:
            # Phone pull: stream only actual_len, then FREE slot on NodeB
            try:
                self.caps.dump_pending(self.link)
            except Exception:
                pass
            self.link.send_status(ST_DONE, MOD_SUBGHZ)
            # Keep listen mode if task still running
            if self.task and self.mode == MODE_LISTEN:
                return KA_STATE_SNIFF
            return KA_STATE_IDLE

        if op == OP_TX_RAW and len(payload) > 1:
            await self._cancel_task()
            try:
                self.drv.apply_preset(0)
                self.drv.set_freq(self.freq)
                data = payload[1:]
                self.drv.tx_async_bits(data, len(data) * 8, bit_us=280)
            except Exception:
                pass
            self.link.send_status(ST_DONE, MOD_SUBGHZ)
            return KA_STATE_TX

        if op == OP_START and len(payload) > 1:
            await self._cancel_task()
            mode = payload[1]
            if len(payload) >= 7:
                pid = payload[6]
                if pid in (0, 1, 2, 3):
                    self.preset = pid
            if len(payload) >= 8:
                tval = payload[7] & 0x7F
                if 40 <= tval <= 100:
                    self.thresh = -tval
            if mode == SUB_MODE_SCAN:
                half, step = _SCAN_WIN.get(self.preset, (2000, 25000))
                self._apply_band(self.freq, half, step)
                self.peak_rssi = -128
                self.mode = MODE_SCAN
                self.task = asyncio.create_task(self._scan())
                self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
                return KA_STATE_SCAN
            self.mode = MODE_LISTEN
            self.task = asyncio.create_task(self._listen())
            self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
            return KA_STATE_SNIFF

        self.drv.apply_preset(self.preset if self.preset in (0, 1, 2) else 0)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
        return KA_STATE_IDLE
