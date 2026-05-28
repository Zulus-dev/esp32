# hardware/radio_service.py - async Node B supervisor for menu and web API
import asyncio
import gc
import time

from machine import Pin

from config import Config
from hardware.radio_uart import RadioUART


TLV_KEEPALIVE = 0xFF
TLV_SUBGHZ_PACKET = 0x10
TLV_NRF_HID_EVENT = 0x11
TLV_WIFI_FRAME = 0x12
TLV_BLE_ADV = 0x13
TLV_RSSI_REPORT = 0x20
TLV_STATUS = 0x21
TLV_LOG = 0xF0

CMD_SET_FREQ = 0x01
CMD_START_SUBGHZ_SCAN = 0x02
CMD_REPLAY_SUBGHZ = 0x03
CMD_STOP_ALL = 0x08
CMD_SET_MODULATION = 0x09
CMD_START_SCAN = 0x0A
CMD_START_SNIFF = 0x0B
CMD_REPLAY = 0x0C
CMD_RAW_TX = 0x0D
CMD_SET_POWER = 0x0E
CMD_SPECTRUM_SWEEP = 0x0F

CMD_RF_ON = 0x14
CMD_RF_OFF = 0x15
CMD_PREPARE_SHUTDOWN = 0x16
CMD_QUERY_POWER_STATE = 0x17

_EVENT_NAMES = {
    TLV_KEEPALIVE: "keepalive",
    TLV_SUBGHZ_PACKET: "subghz",
    TLV_NRF_HID_EVENT: "nrf_hid",
    TLV_WIFI_FRAME: "wifi",
    TLV_BLE_ADV: "ble_adv",
    TLV_RSSI_REPORT: "rssi",
    TLV_STATUS: "status",
    TLV_LOG: "log",
}


class RadioService:
    CMD_RF_ON = CMD_RF_ON
    CMD_RF_OFF = CMD_RF_OFF
    CMD_PREPARE_SHUTDOWN = CMD_PREPARE_SHUTDOWN
    CMD_QUERY_POWER_STATE = CMD_QUERY_POWER_STATE
    """Non-blocking UART bridge to Node B.

    The service keeps only a small ring of decoded telemetry for OLED/web views;
    raw payloads remain binary on the UART and are converted to compact hex/text
    only at the UI boundary.
    """

    def __init__(self, core, max_events=24):
        self.core = core
        self.max_events = max_events
        self.powered = False
        self.online = False
        self.last_seen_ms = 0
        self.last_status = "OFF"
        self.events = []
        self.uart = None
        self._task = None
        self._power = None
        self._restart_task = None
        self.packets = []
        self.rssi = []
        self.link_lost = False
        self._link_lost_since_ms = 0
        self._shutting_down = False

    async def start(self):
        if self.powered:
            return
        self._shutting_down = False
        self.link_lost = False
        self._link_lost_since_ms = 0
        self._power = Pin(Config.RADIO_POWER_PIN, Pin.OUT, value=1)
        self.powered = True
        self.last_status = "BOOTING"
        self.core.update_status(radio=True)
        await asyncio.sleep_ms(Config.RADIO_BOOT_WAIT_MS)
        self.uart = RadioUART()
        self._task = asyncio.create_task(self._listen_loop())
        self._add_event(TLV_STATUS, b"NODE_B_POWER_ON")

    def command(self, msg_type, payload=b""):
        if not self.uart:
            raise ValueError("radio_uart_not_ready")
        self.uart.send_tlv(msg_type, payload)
        self.last_status = "CMD 0x%02X" % msg_type

    async def restart(self):
        await self.stop(hard_power=True)
        await asyncio.sleep_ms(Config.RADIO_POWER_CYCLE_MS)
        await self.start()

    async def shutdown(self):
        self._shutting_down = True
        ready = False
        if self.uart:
            try:
                self.command(0xFF)
                ready = await self.wait_status(("READY_FOR_POWER_OFF",), timeout_ms=Config.RADIO_SHUTDOWN_WAIT_MS)
            except Exception as exc:
                self._add_event(TLV_LOG, ("shutdown warn: %s" % exc).encode())
            if ready:
                await asyncio.sleep_ms(Config.RADIO_READY_CUT_DELAY_MS)
            else:
                self._add_event(TLV_LOG, b"shutdown timeout; forced power cut")
        await self.stop(hard_power=True)

    async def stop(self, hard_power=False):
        if hard_power:
            self._shutting_down = True
        if self.uart:
            try:
                self.uart.send_tlv(0x08)
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            self._task = None
        if self.uart:
            self.uart.close()
            self.uart = None
        if hard_power and self._power:
            self._power.value(0)
        self.powered = False if hard_power else self.powered
        self.online = False
        if hard_power:
            self.link_lost = False
            self._link_lost_since_ms = 0
        self.last_status = "OFF" if hard_power else "STOPPED"
        self.core.update_status(radio=False if hard_power else self.powered)
        gc.collect()

    async def _listen_loop(self):
        while True:
            for _ in range(8):
                item = self.uart.read_tlv() if self.uart else None
                if item is None:
                    break
                msg_type, payload = item
                self._add_event(msg_type, payload)
                if msg_type == TLV_KEEPALIVE:
                    self.online = True
                    self.link_lost = False
                    self._link_lost_since_ms = 0
                    self.last_seen_ms = _ticks_ms()
                elif msg_type in (TLV_STATUS, TLV_LOG):
                    self.last_status = _payload_text(payload)

            now = _ticks_ms()
            if self.online and self.last_seen_ms and _ticks_diff(now, self.last_seen_ms) > Config.RADIO_LINK_STALE_MS:
                self.online = False
                self.last_status = "LINK_STALE"
            if self.powered and self.last_seen_ms and _ticks_diff(now, self.last_seen_ms) > Config.RADIO_LINK_LOST_MS:
                if not self.link_lost:
                    self._link_lost_since_ms = now
                self.link_lost = True
                self.last_status = "LINK_LOST"
            if (
                self.link_lost
                and not self._shutting_down
                and self._restart_task is None
                and self._link_lost_since_ms
                and _ticks_diff(now, self._link_lost_since_ms) > Config.RADIO_LINK_RESTART_MS
            ):
                self._restart_task = asyncio.create_task(self._restart_after_link_lost())
            await asyncio.sleep_ms(2)

    async def _restart_after_link_lost(self):
        try:
            self._add_event(TLV_LOG, b"link lost; power-cycling Node B")
            await self.restart()
        except Exception as exc:
            self.last_status = "RESTART_FAILED"
            self._add_event(TLV_LOG, ("restart failed: %s" % exc).encode())
        finally:
            self._restart_task = None

    def _add_event(self, msg_type, payload):
        decoded = _decode_radio_payload(msg_type, payload)
        item = {
            "t": _ticks_ms(),
            "type": msg_type,
            "name": _EVENT_NAMES.get(msg_type, "0x%02X" % msg_type),
            "text": decoded.get("text", _payload_text(payload)),
            "hex": _payload_hex(payload, 64),
        }
        item.update(decoded)
        if msg_type == TLV_SUBGHZ_PACKET:
            self.packets.append(item)
            if len(self.packets) > 20:
                self.packets.pop(0)
        elif msg_type == TLV_RSSI_REPORT:
            self.rssi.append(item)
            if len(self.rssi) > 48:
                self.rssi.pop(0)
        self.events.append(item)
        if len(self.events) > self.max_events:
            self.events.pop(0)

    async def wait_status(self, expected, timeout_ms=3000):
        deadline = _ticks_ms() + timeout_ms
        while _ticks_ms() < deadline:
            if self.last_status in expected:
                return True
            await asyncio.sleep_ms(20)
        return False

    def snapshot(self):
        return {
            "ok": True,
            "powered": self.powered,
            "online": self.online,
            "last_status": self.last_status,
            "last_seen_ms": self.last_seen_ms,
            "events": self.events,
            "packets": self.packets,
            "rssi": self.rssi,
            "link_lost": self.link_lost,
        }


def _ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def _payload_text(payload):
    try:
        return payload.decode()[:64]
    except Exception:
        return _payload_hex(payload, 32)


def _payload_hex(payload, limit):
    return "".join("%02X" % b for b in payload[:limit])


def _decode_radio_payload(msg_type, payload):
    if msg_type == TLV_SUBGHZ_PACKET and len(payload) >= 11:
        khz = _u32(payload, 0)
        rssi = _i8(payload[4])
        ln = min(payload[5], max(0, len(payload) - 11))
        ts = _u32(payload, 6)
        mod = payload[10]
        data = payload[11:11 + ln]
        return {
            "freq": khz / 1000,
            "rssi_dbm": rssi,
            "len": ln,
            "timestamp": ts,
            "modulation": _mod_name(mod),
            "data_hex": _payload_hex(data, 64),
            "ascii": _ascii(data),
            "text": "%.3fMHz %sdBm %s %dB" % (khz / 1000, rssi, _mod_name(mod), ln),
        }
    if msg_type == TLV_RSSI_REPORT and len(payload) >= 9:
        khz = _u32(payload, 0)
        rssi = _i8(payload[4])
        return {"freq": khz / 1000, "rssi_dbm": rssi, "timestamp": _u32(payload, 5),
                "text": "%.3fMHz %sdBm" % (khz / 1000, rssi)}
    return {}


def _u32(buf, pos):
    return (buf[pos] << 24) | (buf[pos + 1] << 16) | (buf[pos + 2] << 8) | buf[pos + 3]


def _i8(value):
    return value - 256 if value & 0x80 else value


def _mod_name(value):
    return ("2-FSK", "GFSK", "ASK/OOK", "ASK/OOK", "4-FSK")[value] if value < 5 else "MOD%d" % value


def _ascii(data):
    out = []
    for b in data[:32]:
        out.append(chr(b) if 32 <= b <= 126 else ".")
    return "".join(out)
