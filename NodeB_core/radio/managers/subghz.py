# Sub-GHz manager — spectrum + OOK RAW listen/capture/replay (Flipper-like).
# RAM: exclusive rf workspace, one task, stop → full unload.
#
# Reliability rules:
# - Never leave a cancelled task racing SPI: cancel → short settle → idle.
# - Spectrum throttles TLV_RSSI (delta / stride) to avoid UART flood under WiFi load.
# - Listen yields to asyncio between polls; capture wall-clock limited in driver.
#
import asyncio
import gc
from protocol import *
from lib import mempool

# preset_id → (half_khz for scan window, step_khz)
# half=0 → full CC1101 band 300–928 MHz (Wide continuous)
_SCAN_WIN = {
    0: (2000, 25000),   # band window around center (315/433/868/915)
    1: (2000, 25000),
    2: (1500, 25000),
    3: (0, 500000),     # Wide: full 300–928, step 500 kHz
}

# Spectrum: emit on delta, stride, or above detect threshold
_RSSI_EMIT_DELTA = 2
_RSSI_EMIT_STRIDE = 2
_SCAN_MAX_POINTS = 400   # safety cap per pass (cyclic)


class Manager:
    def __init__(self, link):
        self.link = link
        self.drv = None
        self.task = None
        self.buf = None
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
        self._noise = -90  # running noise floor estimate (dBm)
        self._tlv = bytearray(64)  # static TLV payload

    async def _load(self):
        if self.drv:
            return True
        self.buf = mempool.ensure_rf_workspace("cc1101")
        from radio.drivers.cc1101_driver import CC1101
        self.drv = CC1101()
        pn, ver = self.drv.init()
        self.link.send_status(ST_MOD_ON, (pn & 0xFF) | ((ver & 0xFF) << 8))
        return True

    async def _cancel_task(self):
        t = self.task
        self.task = None
        if t is None:
            return
        try:
            t.cancel()
        except Exception:
            pass
        # let cancelled coroutine unwind before touching SPI
        await asyncio.sleep_ms(15)
        if self.drv:
            try:
                self.drv.idle()
            except Exception:
                pass

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

    def _send_pkt(self, n_bytes, rssi_dbm, n_bits=0):
        n = min(n_bytes, 48)
        p = self._tlv
        p[0] = MOD_SUBGHZ
        f = self.freq
        p[1] = (f >> 24) & 255
        p[2] = (f >> 16) & 255
        p[3] = (f >> 8) & 255
        p[4] = f & 255
        p[5] = rssi_dbm & 255
        p[6] = n
        if n:
            p[7:7 + n] = memoryview(self.buf)[:n]
        self.link.send_tlv(TLV_PKT, memoryview(p)[:7 + n])
        self.last_len = n
        self.last_bits = n_bits or (n * 8)

    def _apply_band(self, center_khz, half_khz, step_khz):
        """Set scan window. half_khz==0 → full CC1101 range (Wide)."""
        self.freq = int(center_khz) if center_khz else self.freq
        self.step = max(5000, int(step_khz))
        half = int(half_khz)
        if half <= 0:
            # Wide continuous 300–928 MHz
            self.start = 300000
            self.stop_f = 928000
            self.step = max(100000, self.step)
            if not self.freq:
                self.freq = 600000
            return
        half = max(self.step, half)
        self.start = max(300000, self.freq - half)
        self.stop_f = min(928000, self.freq + half)
        span = self.stop_f - self.start
        if span // self.step < 7:
            self.step = max(5000, span // 12) if span > 0 else self.step

    async def _scan(self):
        """Cyclic frequency finder: step [start, stop], emit RSSI. Stop only via task cancel.

        CC1101: FREQ registers must be written in IDLE; then RX + settle before RSSI.
        Peak over short dwell so brief remote presses are less likely to be missed.
        """
        self.drv.apply_preset(0)
        self.drv.idle()
        while self.task:
            f = self.start
            points = 0
            self._last_sent_rssi = -128
            while f <= self.stop_f and points < _SCAN_MAX_POINTS:
                try:
                    # IDLE → set_freq → RX (FS_AUTOCAL on IDLE→RX via MCSM0)
                    self.drv.idle()
                    self.drv.set_freq(f)
                    self.drv.rx()
                    await asyncio.sleep_ms(8)
                    # peak RSSI over dwell (~3 samples)
                    r = self.drv.rssi()
                    r2 = self.drv.rssi()
                    if r2 > r:
                        r = r2
                    await asyncio.sleep_ms(4)
                    r3 = self.drv.rssi()
                    if r3 > r:
                        r = r3
                    force = (
                        (points % _RSSI_EMIT_STRIDE) == 0
                        or points == 0
                        or r >= self.thresh
                    )
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
        Park on freq. Capture on rising edge above noise+margin only.
        Reject constant/noise fills via edges + duty-cycle checks.
        """
        pid = self.preset if self.preset in (0, 1) else 0
        self.drv.apply_preset(pid)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        self.drv.rx()
        buf = self.buf
        self._last_sent_rssi = -128
        self._noise = -90
        was_below = True
        while self.task:
            try:
                r = self.drv.rssi()
                gate = self.thresh
                if self._noise + 12 > gate:
                    gate = self._noise + 12
                if r < gate:
                    self._noise = (self._noise * 7 + r) // 8
                    was_below = True
                    self._send_rssi(self.freq, r, force=False)
                else:
                    self._send_rssi(self.freq, r, force=True)
                    # only on rising edge (was quiet, now above gate)
                    if was_below:
                        was_below = False
                        nbits, peak = self.drv.raw_capture(
                            buf, max_bits=min(len(buf) * 8, 256),
                            thresh_dbm=gate - 2,
                            timeout_ms=40,
                            gap_end_ms=28,
                        )
                        edges = self._transitions(buf, nbits)
                        ones = 0
                        nbytes = (nbits + 7) // 8
                        for i in range(nbytes):
                            ones += bin(buf[i]).count("1")
                        duty = (ones * 100) // max(nbits, 1)
                        # real OOK remote: structured edges, not 0%/100% fill, peak above noise
                        ok = (
                            nbits >= 64
                            and edges >= 12
                            and edges * 4 < nbits  # not toggling every bit (noise)
                            and 15 <= duty <= 85
                            and peak >= self._noise + 12
                        )
                        if ok:
                            self._send_pkt(nbytes, peak, nbits)
                            self._send_rssi(self.freq, peak, force=True)
                        self.drv.idle()
                        self.drv.set_freq(self.freq)
                        self.drv.rx()
                        await asyncio.sleep_ms(80)
            except Exception:
                pass
            await asyncio.sleep_ms(12)

    async def _capture_once(self):
        self.drv.apply_preset(self.preset if self.preset in (0, 1) else 0)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        # Bounded: driver hard-caps ~450 ms; here we ask for up to 400 ms wait
        nbits, peak = self.drv.raw_capture(
            self.buf, max_bits=min(len(self.buf) * 8, 256),
            thresh_dbm=self.thresh,
            timeout_ms=400,
            gap_end_ms=30,
        )
        if nbits >= 16:
            self._send_pkt((nbits + 7) // 8, peak, nbits)
            self.link.send_status(ST_DONE, nbits if nbits < 65535 else 65535)
        else:
            self._send_rssi(self.freq, peak, force=True)
            self.link.send_status(ST_DONE, 0)

    async def handle(self, payload):
        op = payload[0] if payload else OP_CONFIG
        if op == OP_STOP:
            await self.stop()
            self.link.send_status(ST_MOD_OFF, MOD_SUBGHZ)
            return KA_STATE_IDLE

        await self._load()

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
            mode = payload[1] if len(payload) > 1 else 0
            if mode == 1:
                await self._capture_once()
            else:
                try:
                    self.drv.apply_preset(self.preset if self.preset in (0, 1, 3) else 0)
                    self.drv.set_freq(self.freq)
                    self.drv.rx()
                    await asyncio.sleep_ms(25)
                    self._send_rssi(self.freq, self.drv.rssi(), force=True)
                except Exception:
                    pass
                self.link.send_status(ST_DONE, MOD_SUBGHZ)
            return KA_STATE_IDLE

        if op == OP_REPLAY and self.last_bits > 0:
            try:
                self.drv.apply_preset(0)
                self.drv.set_freq(self.freq)
                self.drv.tx_async_bits(self.buf, self.last_bits, bit_us=280)
            except Exception:
                pass
            self.link.send_status(ST_DONE, MOD_SUBGHZ)
            return KA_STATE_TX

        if op == OP_TX_RAW and len(payload) > 1:
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
                self.task = asyncio.create_task(self._scan())
                self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
                return KA_STATE_SCAN
            # Listen: lock exact frequency (no scan window side-effects)
            self.task = asyncio.create_task(self._listen())
            self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
            return KA_STATE_SNIFF

        self.drv.apply_preset(self.preset if self.preset in (0, 1, 2) else 0)
        self.drv.idle()
        self.drv.set_freq(self.freq)
        self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
        return KA_STATE_IDLE
