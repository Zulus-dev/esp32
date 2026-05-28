# hardware/radio_uart.py - SLIP + TLV UART event bus for the Radio ESP32-C3 board
import asyncio
from machine import UART, Pin
from config import Config


class RadioUART:
    """Binary TLV transport over SLIP framed UART.

    Packet before SLIP escaping:
        type(1) | length(1) | payload(N) | crc16_ccitt(2, big-endian)

    SLIP uses 0xC0 as END marker, so either board can resync immediately after
    noise or reboot. This class is intentionally lazy: the kernel does not import
    it until a radio feature module asks for the transport.
    """

    END = 0xC0
    ESC = 0xDB
    ESC_END = 0xDC
    ESC_ESC = 0xDD
    MAX_PAYLOAD = 255

    def __init__(self, rx_queue=None):
        self.uart = UART(
            Config.RADIO_UART_ID,
            baudrate=Config.RADIO_UART_BAUD,
            tx=Pin(Config.RADIO_UART_TX_PIN),
            rx=Pin(Config.RADIO_UART_RX_PIN),
        )
        self.rx_queue = rx_queue
        self._rx = bytearray()

    def send_tlv(self, msg_type, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode()
        size = len(payload)
        if size > self.MAX_PAYLOAD:
            raise ValueError("radio_payload_too_large")
        packet = bytearray((msg_type & 0xFF, size))
        packet.extend(payload)
        crc = self._crc16(packet)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)
        self.uart.write(self._slip_encode(packet))

    def read_tlv(self):
        """Poll UART and return one (type, payload) tuple, or None."""
        while self.uart.any():
            data = self.uart.read(1)
            if not data:
                return None
            frame = self._feed_byte(data[0])
            if frame is not None:
                return self._parse_packet(frame)
        return None

    async def listen(self, callback=None):
        """Async listener for Node B. Publishes packets without blocking web UI."""
        while True:
            item = self.read_tlv()
            if item is not None:
                if self.rx_queue is not None:
                    self.rx_queue.put_nowait(item)
                if callback is not None:
                    res = callback(item)
                    if hasattr(res, "__await__") or type(res).__name__ in ("generator", "coroutine"):
                        await res
            await asyncio.sleep_ms(2)

    def _feed_byte(self, byte):
        if byte == self.END:
            if not self._rx:
                return None
            frame = bytes(self._rx)
            self._rx = bytearray()
            return frame
        if byte == self.ESC:
            self._rx.append(byte)
            return None
        if self._rx and self._rx[-1] == self.ESC:
            self._rx.pop()
            if byte == self.ESC_END:
                self._rx.append(self.END)
            elif byte == self.ESC_ESC:
                self._rx.append(self.ESC)
            else:
                self._rx = bytearray()
        else:
            self._rx.append(byte)
        return None

    def _parse_packet(self, packet):
        if len(packet) < 4:
            return None
        msg_type = packet[0]
        size = packet[1]
        if size != len(packet) - 4:
            return None
        crc_rx = (packet[-2] << 8) | packet[-1]
        if self._crc16(packet[:-2]) != crc_rx:
            return None
        return msg_type, packet[2:-2]

    def _slip_encode(self, packet):
        out = bytearray()
        for byte in packet:
            if byte == self.END:
                out.extend((self.ESC, self.ESC_END))
            elif byte == self.ESC:
                out.extend((self.ESC, self.ESC_ESC))
            else:
                out.append(byte)
        out.append(self.END)
        return bytes(out)

    def _crc16(self, data):
        crc = 0xFFFF
        for byte in data:
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return crc

    # Backward-compatible wrappers for older callers.
    def send(self, payload):
        self.send_tlv(0x01, payload)

    def read_frame(self):
        item = self.read_tlv()
        return None if item is None else item[1]

    def close(self):
        try:
            self.uart.deinit()
        except Exception:
            pass
