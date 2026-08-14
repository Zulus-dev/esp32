# lib/slip_tlv.py — static TX/RX only. No per-frame heap.
from micropython import const

END = const(0xC0)
ESC = const(0xDB)
ESC_END = const(0xDC)
ESC_ESC = const(0xDD)
MAX_PAYLOAD = const(64)
MAX_FRAME = const(72)
MAX_SLIP = const(160)

_CRC_TAB = None
_TX_PKT = bytearray(MAX_FRAME)
_TX_SLIP = bytearray(MAX_SLIP)


def _init_crc_tab():
    global _CRC_TAB
    if _CRC_TAB is not None:
        return
    tab = bytearray(512)
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        tab[i * 2] = crc & 0xFF
        tab[i * 2 + 1] = (crc >> 8) & 0xFF
    _CRC_TAB = tab


def crc16_ccitt(data, length=None):
    _init_crc_tab()
    tab = _CRC_TAB
    crc = 0xFFFF
    n = length if length is not None else len(data)
    for i in range(n):
        idx = ((crc >> 8) ^ (data[i] & 0xFF)) & 0xFF
        tv = tab[idx * 2] | (tab[idx * 2 + 1] << 8)
        crc = ((crc << 8) ^ tv) & 0xFFFF
    return crc


def build_tlv(msg_type, payload=b""):
    size = len(payload) if payload is not None else 0
    if size > MAX_PAYLOAD:
        size = MAX_PAYLOAD
    pkt = _TX_PKT
    pkt[0] = msg_type & 0xFF
    pkt[1] = size
    if size:
        pkt[2:2 + size] = payload[:size]
    crc = crc16_ccitt(pkt, 2 + size)
    pkt[2 + size] = (crc >> 8) & 0xFF
    pkt[3 + size] = crc & 0xFF
    frame_len = 4 + size
    out = _TX_SLIP
    n = 0
    for i in range(frame_len):
        byte = pkt[i]
        if byte == END:
            out[n] = ESC
            out[n + 1] = ESC_END
            n += 2
        elif byte == ESC:
            out[n] = ESC
            out[n + 1] = ESC_ESC
            n += 2
        else:
            out[n] = byte
            n += 1
    out[n] = END
    n += 1
    return memoryview(out)[:n]


class SlipDecoder:
    def __init__(self, max_size=80):
        self._rx = bytearray(max_size)
        self._len = 0
        self._esc = False

    def feed(self, byte):
        if byte == END:
            if self._len == 0:
                return False
            self._esc = False
            return True
        if self._esc:
            self._esc = False
            if byte == ESC_END:
                byte = END
            elif byte == ESC_ESC:
                byte = ESC
            else:
                self._len = 0
                return False
        elif byte == ESC:
            self._esc = True
            return False
        if self._len < len(self._rx):
            self._rx[self._len] = byte
            self._len += 1
        else:
            self._len = 0
        return False

    def parse_last(self):
        n = self._len
        self._len = 0
        if n < 4:
            return None
        pkt = self._rx
        size = pkt[1]
        if size != n - 4:
            return None
        crc_rx = (pkt[n - 2] << 8) | pkt[n - 1]
        if crc16_ccitt(pkt, n - 2) != crc_rx:
            return None
        return pkt[0], memoryview(pkt)[2:2 + size]
