# radio/managers/subghz.py - async high-level Sub-GHz manager template
import asyncio
import gc
import time

from protocol import TLV_LOG, TLV_RSSI_REPORT, TLV_STATUS, TLV_SUBGHZ_PACKET
from radio.drivers.cc1101_driver import CC1101Driver


_DEFAULT_FREQS = (315.000, 433.920, 868.350, 915.000)
_MOD_ID = {"2-FSK": 0, "GFSK": 1, "ASK": 3, "OOK": 3, "4-FSK": 4}


class CC1101Manager:
    """Async CC1101 radio manager used as a template for future radio modules."""

    def __init__(self, uart):
        self.uart = uart
        self.driver = None
        self.task = None
        self.mode = None
        self.freq_mhz = 433.920
        self.modulation = "OOK"
        self.baud = 4800
        self.deviation = 5000
        self.bandwidth = 58000
        self.sync = 0xD391
        self.packet_len = 0
        self.power_dbm = 0
        self.buffer = []
        self.captures = []
        self.last_tx_ms = 0
        self.tx_interval_ms = 55  # <= 18 telemetry packets/sec over UART.

    def _ensure_driver(self):
        if self.driver is None:
            self.driver = CC1101Driver()
            self.driver.begin()
        return self.driver

    async def set_frequency(self, payload):
        cfg = self._parse_payload(payload)
        if "freq" in cfg:
            self.freq_mhz = _float(cfg.get("freq"), self.freq_mhz)
        elif payload and len(payload) >= 4:
            khz = (payload[0] << 24) | (payload[1] << 16) | (payload[2] << 8) | payload[3]
            self.freq_mhz = khz / 1000.0
        self._configure()
        self._status("FREQ:%.3f" % self.freq_mhz)

    async def set_modulation(self, payload):
        cfg = self._parse_payload(payload)
        self._apply_config(cfg)
        self._configure()
        self._status("MOD:%s" % self.modulation)

    async def set_power(self, payload):
        cfg = self._parse_payload(payload)
        self.power_dbm = int(_float(cfg.get("power", cfg.get("dbm", self.power_dbm)), self.power_dbm))
        self._ensure_driver().set_power(self.power_dbm)
        self._status("POWER:%ddBm" % self.power_dbm)

    def start_scan(self, payload=None):
        self._start("scan", payload)

    def start_sniff(self, payload=None):
        self._start("sniff", payload)

    def spectrum_sweep(self, payload=None):
        self._start("sweep", payload)

    async def raw_tx(self, payload):
        cfg = self._parse_payload(payload)
        self._apply_config(cfg)
        data = _hex_to_bytes(cfg.get("data", "")) or payload
        if data and b";" in data:
            data = _hex_to_bytes(cfg.get("data", ""))
        await self._transmit(data, "RAW_TX_SENT")

    async def replay(self, payload):
        cfg = self._parse_payload(payload)
        self._apply_config(cfg)
        data = _hex_to_bytes(cfg.get("data", "")) or payload
        if data and b";" in data:
            data = _hex_to_bytes(cfg.get("data", ""))
        await self._transmit(data, "REPLAY_SENT")

    def _start(self, mode, payload):
        self.stop(silent=True)
        self.mode = mode
        self._apply_config(self._parse_payload(payload))
        self._configure()
        self._status(("SUBGHZ_%s_STARTED" % mode).upper())
        self.task = asyncio.create_task(self._run_loop())

    def _configure(self):
        drv = self._ensure_driver()
        drv.configure(self.freq_mhz, self.modulation, self.baud, self.deviation,
                      self.bandwidth, self.sync, self.packet_len, self.power_dbm)

    def _apply_config(self, cfg):
        if not cfg:
            return
        self.freq_mhz = _float(cfg.get("freq", self.freq_mhz), self.freq_mhz)
        self.modulation = str(cfg.get("mod", cfg.get("modulation", self.modulation))).upper()
        self.baud = int(_float(cfg.get("baud", self.baud), self.baud))
        self.deviation = int(_float(cfg.get("dev", cfg.get("deviation", self.deviation)), self.deviation))
        self.bandwidth = int(_float(cfg.get("bw", cfg.get("bandwidth", self.bandwidth)), self.bandwidth))
        self.sync = int(str(cfg.get("sync", self.sync)), 0) if "sync" in cfg else self.sync
        self.packet_len = int(_float(cfg.get("len", cfg.get("packet_len", self.packet_len)), self.packet_len))
        self.power_dbm = int(_float(cfg.get("power", self.power_dbm), self.power_dbm))

    async def _run_loop(self):
        try:
            if self.mode == "sweep":
                await self._sweep_loop()
            else:
                await self._rx_loop(scan=(self.mode == "scan"))
        except Exception as exc:
            self._log("CC1101 loop failed: %s" % exc)
            self._status("SUBGHZ_ERROR:%s" % exc)
        finally:
            self.mode = None
            self.task = None
            gc.collect()

    async def _rx_loop(self, scan=False):
        freqs = _DEFAULT_FREQS if scan else (self.freq_mhz,)
        idx = 0
        drv = self._ensure_driver()
        while self.mode in ("scan", "sniff"):
            if scan:
                self.freq_mhz = freqs[idx]
                idx = (idx + 1) % len(freqs)
                self._configure()
            drv.start_rx()
            end = time.ticks_add(time.ticks_ms(), 160 if scan else 40)
            while time.ticks_diff(end, time.ticks_ms()) > 0 and self.mode in ("scan", "sniff"):
                pkt = drv.read_packet()
                if pkt:
                    data, rssi, _lqi = pkt
                    self._capture(data, rssi)
                else:
                    rssi = drv.read_rssi()
                    if scan and rssi > -95:
                        self._push(TLV_RSSI_REPORT, self._pack_rssi(rssi))
                await self._flush(limit=2)
                await asyncio.sleep_ms(8)
            await asyncio.sleep_ms(0)

    async def _sweep_loop(self):
        cfg_freqs = (315.000, 390.000, 433.920, 868.350, 915.000)
        drv = self._ensure_driver()
        while self.mode == "sweep":
            for freq in cfg_freqs:
                if self.mode != "sweep":
                    break
                self.freq_mhz = freq
                self._configure()
                drv.start_rx()
                await asyncio.sleep_ms(18)
                self._push(TLV_RSSI_REPORT, self._pack_rssi(drv.read_rssi()))
                await self._flush(limit=1)

    def _capture(self, data, rssi):
        item = (self.freq_mhz, rssi, time.ticks_ms(), self.modulation, data[:48])
        self.captures.append(item)
        if len(self.captures) > 12:
            self.captures.pop(0)
        self._push(TLV_SUBGHZ_PACKET, self._pack_packet(data, rssi))

    async def _transmit(self, data, ok_status):
        if not data:
            raise ValueError("empty_tx_data")
        drv = self._ensure_driver()
        self._configure()
        ok = drv.send_packet(data[:61])
        self._status(ok_status if ok else "TX_TIMEOUT")
        await asyncio.sleep_ms(0)

    def _pack_packet(self, data, rssi):
        data = data[:48]
        khz = int(self.freq_mhz * 1000)
        ts = time.ticks_ms() & 0xFFFFFFFF
        rssi_b = rssi & 0xFF
        mod = _MOD_ID.get(self.modulation, 0)
        return bytes(((khz >> 24) & 255, (khz >> 16) & 255, (khz >> 8) & 255, khz & 255,
                      rssi_b, len(data), (ts >> 24) & 255, (ts >> 16) & 255, (ts >> 8) & 255, ts & 255, mod)) + data

    def _pack_rssi(self, rssi):
        khz = int(self.freq_mhz * 1000)
        ts = time.ticks_ms() & 0xFFFFFFFF
        return bytes(((khz >> 24) & 255, (khz >> 16) & 255, (khz >> 8) & 255, khz & 255,
                      rssi & 255, (ts >> 24) & 255, (ts >> 16) & 255, (ts >> 8) & 255, ts & 255))

    def _push(self, typ, data):
        self.buffer.append((typ, data))
        if len(self.buffer) > 24:
            self.buffer.pop(0)

    async def _flush(self, limit=4):
        sent = 0
        while self.buffer and sent < limit:
            now = time.ticks_ms()
            wait = self.tx_interval_ms - time.ticks_diff(now, self.last_tx_ms)
            if wait > 0:
                await asyncio.sleep_ms(wait)
            typ, data = self.buffer.pop(0)
            self.uart.send_tlv(typ, data)
            self.last_tx_ms = time.ticks_ms()
            sent += 1

    def _parse_payload(self, payload):
        if not payload or len(payload) == 4:
            return {}
        try:
            text = payload.decode() if isinstance(payload, bytes) else str(payload)
        except Exception:
            return {}
        cfg = {}
        for part in text.replace(",", ";").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                cfg[k.strip().lower()] = v.strip()
        return cfg

    def _status(self, text):
        self.uart.send_tlv(TLV_STATUS, text)

    def _log(self, text):
        self.uart.send_tlv(TLV_LOG, text)

    def stop(self, silent=False):
        self.mode = None
        if self.task:
            self.task.cancel()
            self.task = None
        self.buffer = []
        if self.driver:
            try:
                self.driver.idle(); self.driver.flush_rx(); self.driver.flush_tx(); self.driver.sleep()
            except Exception as exc:
                self._log("CC1101 stop warn: %s" % exc)
            self.driver = None
        if not silent:
            self._log("CC1101 stopped")
        gc.collect()


def _float(value, default):
    try:
        return float(value)
    except Exception:
        return default


def _hex_to_bytes(text):
    if not text:
        return b""
    text = str(text).replace(" ", "").replace(":", "")
    if len(text) & 1:
        text = "0" + text
    out = bytearray()
    try:
        for i in range(0, len(text), 2):
            out.append(int(text[i:i + 2], 16))
    except Exception:
        return b""
    return bytes(out)
