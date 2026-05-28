# hardware/radio_uart.py - SLIP + TLV UART transport for Node B
from machine import UART, Pin

from config import Config
from protocol import TLV_LOG


class RadioUART:
    END = 0xC0
    ESC = 0xDB
    ESC_END = 0xDC
    ESC_ESC = 0xDD
    MAX_PAYLOAD = 255

    def __init__(self):
        self.uart = UART(
            Config.UART_ID,
            baudrate=Config.UART_BAUD,
            tx=Pin(Config.UART_TX),
            rx=Pin(Config.UART_RX),
        )
        self._rx = bytearray()

    def send_tlv(self, msg_type, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode()
        size = len(payload)
        if size > self.MAX_PAYLOAD:
            payload = payload[: self.MAX_PAYLOAD]
            size = len(payload)
        packet = bytearray((msg_type & 0xFF, size))
        packet.extend(payload)
        crc = self._crc16(packet)
        packet.append((crc >> 8) & 0xFF)
        packet.append(crc & 0xFF)
        self.uart.write(self._slip_encode(packet))

    def read_tlv(self):
        while self.uart.any():
            data = self.uart.read(1)
            if not data:
                return None
            frame = self._feed_byte(data[0])
            if frame is not None:
                return self._parse_packet(frame)
        return None

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

    def send_log(self, text):
        self.send_tlv(TLV_LOG, text)

    def close(self):
        try:
            self.uart.deinit()
        except Exception:
            pass
