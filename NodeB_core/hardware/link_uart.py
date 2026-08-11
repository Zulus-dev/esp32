# hardware/link_uart.py — Node B (aligned with working M_S_v1.3/radio_uart.py)
from machine import UART, Pin
from array import array

from config import Config
from lib.slip_tlv import SlipDecoder, build_tlv
from protocol import TLV_STATUS, TLV_KEEPALIVE

_PENDING_DEPTH = 2
_PENDING_PAYLOAD = 48


class LinkUART:
    def __init__(self):
        self.uart = UART(
            Config.LINK_UART_ID,
            baudrate=Config.LINK_UART_BAUD,
            tx=Pin(Config.LINK_UART_TX_PIN),
            rx=Pin(Config.LINK_UART_RX_PIN),
        )
        self._dec = SlipDecoder(80)
        self._chunk = bytearray(48)
        self._p_type = array("B", [0] * _PENDING_DEPTH)
        self._p_len = array("B", [0] * _PENDING_DEPTH)
        self._p_data = bytearray(_PENDING_DEPTH * _PENDING_PAYLOAD)
        self._p_head = 0
        self._p_tail = 0
        self._p_count = 0
        self._status = bytearray(3)
        self._ka = bytearray(4)

    def send_tlv(self, msg_type, payload=b""):
        # Same as M_S_v1.3: write memoryview from static buffer directly
        frame = build_tlv(msg_type, payload)
        self.uart.write(frame)

    def send_status(self, code, arg=0):
        b = self._status
        b[0] = code & 0xFF
        b[1] = (arg >> 8) & 0xFF
        b[2] = arg & 0xFF
        self.send_tlv(TLV_STATUS, b)

    def send_keepalive(self, free_kb, flags=0, state=0):
        b = self._ka
        if free_kb > 65535:
            free_kb = 65535
        b[0] = (free_kb >> 8) & 0xFF
        b[1] = free_kb & 0xFF
        b[2] = flags & 0xFF
        b[3] = state & 0xFF
        self.send_tlv(TLV_KEEPALIVE, b)

    def _pending_push(self, msg_type, payload_mv):
        if self._p_count >= _PENDING_DEPTH:
            self._p_tail = (self._p_tail + 1) % _PENDING_DEPTH
            self._p_count -= 1
        i = self._p_head
        n = len(payload_mv) if payload_mv is not None else 0
        if n > _PENDING_PAYLOAD:
            n = _PENDING_PAYLOAD
        self._p_type[i] = msg_type & 0xFF
        self._p_len[i] = n
        off = i * _PENDING_PAYLOAD
        if n:
            self._p_data[off:off + n] = payload_mv[:n]
        self._p_head = (i + 1) % _PENDING_DEPTH
        self._p_count += 1

    def _pending_pop(self):
        if self._p_count == 0:
            return None
        i = self._p_tail
        n = self._p_len[i]
        off = i * _PENDING_PAYLOAD
        item = (self._p_type[i], memoryview(self._p_data)[off:off + n])
        self._p_tail = (i + 1) % _PENDING_DEPTH
        self._p_count -= 1
        return item

    def poll_tlv(self):
        """Return (type, payload) or None — same semantics as M_S_v1.3 read_tlv."""
        if self._p_count:
            item = self._pending_pop()
            if item is None:
                return None
            # copy payload — caller may await before next poll
            return item[0], bytes(item[1])

        n = self.uart.any()
        if not n:
            return None

        nbytes = None
        if hasattr(self.uart, "readinto"):
            nbytes = self.uart.readinto(self._chunk)
        if not nbytes:
            raw = self.uart.read(64 if n > 64 else n)
            if not raw:
                return None
            mv = raw
        else:
            mv = memoryview(self._chunk)[:nbytes]

        first = None
        for b in mv:
            if self._dec.feed(b):
                parsed = self._dec.parse_last()
                if parsed is not None:
                    msg_type, payload = parsed
                    if first is None:
                        self._pending_push(msg_type, payload)
                        first = self._pending_pop()
                    else:
                        self._pending_push(msg_type, payload)
        if first is None:
            return None
        return first[0], bytes(first[1])

    def close(self):
        try:
            self.uart.deinit()
        except Exception:
            pass
        self._p_head = 0
        self._p_tail = 0
        self._p_count = 0
