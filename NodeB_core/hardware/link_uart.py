# hardware/link_uart.py — Node B link (binary SLIP only)
# Static status/ka buffers. No str/JSON on wire.

from machine import UART

from config import Config
from lib.slip_tlv import SlipDecoder, build_tlv
from protocol import TLV_STATUS, TLV_KEEPALIVE


class LinkUART:
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
        self._status = bytearray(3)
        self._ka = bytearray(4)

    def send_tlv(self, msg_type, payload=b""):
        frame = build_tlv(msg_type, payload)
        try:
            self.uart.write(b"\xc0")
            self.uart.write(frame)
        except Exception:
            pass

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

    def poll_tlv(self):
        n = self.uart.any()
        if not n:
            return None
        data = self.uart.read(min(n, 64))
        if not data:
            return None
        for byte in data:
            if self._dec.feed(byte):
                return self._dec.parse_last()
        return None

    def close(self):
        try:
            self.uart.deinit()
        except Exception:
            pass
