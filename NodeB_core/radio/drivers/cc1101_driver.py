# CC1101 SPI driver — Flipper-compatible OOK async + spectrum + RAW sample.
# MicroPython ESP32-C3. Status: 0xC0|addr. Config write: addr, read: 0x80|addr.
#
# RAW capture rules:
# - Hard wall-clock limit (never block > ~450 ms) — protects Node B WDT.
# - Bit timing via ticks_us (not busy for-range).
# - No heap in hot path.
#
from machine import Pin, SPI
from config import Config
import time

IOCFG0 = 0x02
FIFOTHR = 0x03
PKTLEN = 0x06
PKTCTRL0 = 0x08
FSCTRL1 = 0x0B
FREQ2 = 0x0D
FREQ1 = 0x0E
FREQ0 = 0x0F
MDMCFG4 = 0x10
MDMCFG3 = 0x11
MDMCFG2 = 0x12
MDMCFG1 = 0x13
MDMCFG0 = 0x14
DEVIATN = 0x15
MCSM0 = 0x18
FOCCFG = 0x19
AGCCTRL2 = 0x1B
AGCCTRL1 = 0x1C
AGCCTRL0 = 0x1D
FREND0 = 0x22
FSCAL3 = 0x23
FSCAL2 = 0x24
FSCAL1 = 0x25
FSCAL0 = 0x26
TEST2 = 0x2C
TEST1 = 0x2D
TEST0 = 0x2E
PATABLE = 0x3E
FIFO = 0x3F

SRES = 0x30
SCAL = 0x33
SRX = 0x34
STX = 0x35
SIDLE = 0x36
SPWD = 0x39
SFRX = 0x3A
SFTX = 0x3B

PARTNUM = 0x30
VERSION = 0x31
RSSI = 0x34
MARCSTATE = 0x35
RXBYTES = 0x3B

READ = 0x80
BURST = 0x40
STATUS = 0xC0

# Flipper AM650 — OOK async, BW~650kHz (unknown remotes / alarms)
_PRESET_AM650 = (
    (IOCFG0, 0x0D), (FIFOTHR, 0x47), (PKTCTRL0, 0x32), (FSCTRL1, 0x06),
    (MDMCFG4, 0x17), (MDMCFG3, 0x32), (MDMCFG2, 0x30), (MDMCFG1, 0x00), (MDMCFG0, 0x00),
    (MCSM0, 0x18), (FOCCFG, 0x18), (AGCCTRL2, 0x07), (AGCCTRL1, 0x00), (AGCCTRL0, 0x91),
    (FREND0, 0x11), (FSCAL3, 0xE9), (FSCAL2, 0x2A), (FSCAL1, 0x00), (FSCAL0, 0x1F),
    (TEST2, 0x81), (TEST1, 0x35), (TEST0, 0x09),
)
# Flipper AM270 — OOK async, BW~270kHz
_PRESET_AM270 = (
    (IOCFG0, 0x0D), (FIFOTHR, 0x47), (PKTCTRL0, 0x32), (FSCTRL1, 0x06),
    (MDMCFG4, 0x67), (MDMCFG3, 0x32), (MDMCFG2, 0x30), (MDMCFG1, 0x00), (MDMCFG0, 0x00),
    (MCSM0, 0x18), (FOCCFG, 0x18), (AGCCTRL2, 0x03), (AGCCTRL1, 0x00), (AGCCTRL0, 0x91),
    (FREND0, 0x11), (FSCAL3, 0xE9), (FSCAL2, 0x2A), (FSCAL1, 0x00), (FSCAL0, 0x1F),
    (TEST2, 0x81), (TEST1, 0x35), (TEST0, 0x09),
)
# 2FSK packet-ish
_PRESET_FM = (
    (IOCFG0, 0x06), (FIFOTHR, 0x47), (PKTCTRL0, 0x05), (FSCTRL1, 0x06),
    (MDMCFG4, 0xF5), (MDMCFG3, 0x83), (MDMCFG2, 0x13), (DEVIATN, 0x15),
    (MCSM0, 0x18), (FOCCFG, 0x16), (AGCCTRL2, 0x43), (AGCCTRL1, 0x40), (AGCCTRL0, 0x91),
    (FREND0, 0x10), (FSCAL3, 0xE9), (FSCAL2, 0x2A), (FSCAL1, 0x00), (FSCAL0, 0x1F),
    (TEST2, 0x81), (TEST1, 0x35), (TEST0, 0x09),
)
_PRESET_SCAN = _PRESET_AM650

_PRESETS = {0: _PRESET_AM650, 1: _PRESET_AM270, 2: _PRESET_FM, 3: _PRESET_SCAN}

# Sample period for OOK async (us). 25 us ~ 40 kHz — enough for 200-600 us remote bits.
_SAMPLE_US = 25
# Absolute ceiling for any single capture call (ms). Must stay well under Node B WDT.
_CAPTURE_HARD_MS = 450


class CC1101:
    def __init__(self):
        self.spi = SPI(
            1, baudrate=4_000_000, polarity=0, phase=0,
            sck=Pin(Config.CC_SCK), mosi=Pin(Config.CC_MOSI), miso=Pin(Config.CC_MISO),
        )
        self.cs = Pin(Config.CC_CSN, Pin.OUT, value=1)
        self.gdo0 = Pin(Config.CC_GDO0, Pin.IN)
        self.freq_khz = 433920
        self.preset_id = 0
        self._xfer = bytearray(2)
        self._pa = 0xC0

    def _x(self, hdr, val=0):
        buf = self._xfer
        buf[0] = hdr & 0xFF
        buf[1] = val & 0xFF
        self.cs(0)
        try:
            self.spi.write_readinto(buf, buf)
        finally:
            self.cs(1)
        return buf[1]

    def strobe(self, cmd):
        self.cs(0)
        try:
            self.spi.write(bytes((cmd & 0xFF,)))
        finally:
            self.cs(1)

    def wr(self, reg, val):
        self._x(reg & 0x3F, val)

    def rd(self, reg):
        return self._x(READ | (reg & 0x3F), 0)

    def rd_status(self, reg):
        return self._x(STATUS | (reg & 0x3F), 0)

    def init(self):
        self.strobe(SRES)
        time.sleep_ms(2)
        self.apply_preset(0)
        self.set_freq(433920)
        pn = self.rd_status(PARTNUM)
        ver = self.rd_status(VERSION)
        if pn == 0xFF and ver == 0xFF:
            raise OSError("cc1101_not_found")
        return (pn, ver)

    def apply_preset(self, pid):
        pid = int(pid) & 0xFF
        regs = _PRESETS.get(pid, _PRESET_AM650)
        self.preset_id = pid if pid in _PRESETS else 0
        self.idle()
        for reg, val in regs:
            self.wr(reg, val)
        self.set_power(self._pa)
        return self.preset_id

    def set_power(self, p):
        self._pa = p & 0xFF
        self.cs(0)
        try:
            self.spi.write(bytes((PATABLE | BURST, 0x00, self._pa)))
        finally:
            self.cs(1)

    def set_freq(self, khz):
        """Program carrier (kHz). Caller should be in IDLE for reliable PLL lock."""
        self.freq_khz = int(khz)
        # FREQ = f_carrier_Hz * 2^16 / f_xosc ; f_xosc = 26 MHz
        f = int((self.freq_khz * 65536) // 26000)
        self.wr(FREQ2, (f >> 16) & 255)
        self.wr(FREQ1, (f >> 8) & 255)
        self.wr(FREQ0, f & 255)

    def set_modem(self, mod=3, datarate=4800, deviation=5000, bw=203000):
        if mod == 3:
            self.apply_preset(0)
        else:
            self.apply_preset(2)

    def idle(self):
        self.strobe(SIDLE)

    def rx(self):
        self.strobe(SFRX)
        self.strobe(SRX)

    def rssi(self):
        v = self.rd_status(RSSI)
        if v >= 128:
            v -= 256
        return (v // 2) - 74

    def read_pkt(self, buf):
        n = self.rd_status(RXBYTES) & 0x7F
        if n <= 0:
            return 0
        if n > len(buf):
            n = len(buf)
        self.cs(0)
        try:
            self.spi.write(bytes((FIFO | READ | BURST,)))
            self.spi.readinto(memoryview(buf)[:n])
        finally:
            self.cs(1)
        return n

    def wait_rssi(self, thresh_dbm, timeout_ms=80):
        """Poll RSSI until >= thresh or timeout. Returns (hit, peak_rssi)."""
        t0 = time.ticks_ms()
        peak = -128
        while time.ticks_diff(time.ticks_ms(), t0) < timeout_ms:
            r = self.rssi()
            if r > peak:
                peak = r
            if r >= thresh_dbm:
                return True, peak
            time.sleep_ms(1)
        return False, peak

    def raw_capture(self, out_buf, max_bits, thresh_dbm=-72, timeout_ms=80, gap_end_ms=28, sample_us=_SAMPLE_US):
        """
        Wait RSSI>=thresh (bounded), then sample GDO0 into packed bits.
        Hard wall-clock: min(timeout + capture, _CAPTURE_HARD_MS).
        Returns (n_bits, peak_rssi). Zero heap.
        """
        if max_bits > len(out_buf) * 8:
            max_bits = len(out_buf) * 8
        if max_bits < 8:
            return (0, -128)

        hard_ms = _CAPTURE_HARD_MS
        if timeout_ms > hard_ms - 50:
            timeout_ms = hard_ms - 50
        if timeout_ms < 10:
            timeout_ms = 10

        self.rx()
        t_hard = time.ticks_ms()
        hit, peak = self.wait_rssi(thresh_dbm, timeout_ms)
        if not hit:
            return (0, peak)

        nbits = 0
        last_high = time.ticks_ms()
        byte_i = 0
        bit_i = 7
        acc = 0
        period = sample_us if sample_us >= 10 else 10
        next_t = time.ticks_us()

        while nbits < max_bits:
            if time.ticks_diff(time.ticks_ms(), t_hard) >= hard_ms:
                break
            now = time.ticks_us()
            if time.ticks_diff(now, next_t) < 0:
                while time.ticks_diff(time.ticks_us(), next_t) < 0:
                    pass
            next_t = time.ticks_add(next_t, period)

            level = 1 if self.gdo0.value() else 0
            if level:
                last_high = time.ticks_ms()
            acc |= level << bit_i
            bit_i -= 1
            nbits += 1
            if bit_i < 0:
                out_buf[byte_i] = acc
                byte_i += 1
                if byte_i >= len(out_buf):
                    break
                acc = 0
                bit_i = 7
            if nbits > 48 and time.ticks_diff(time.ticks_ms(), last_high) > gap_end_ms:
                break

        if bit_i != 7 and byte_i < len(out_buf):
            out_buf[byte_i] = acc
        r = self.rssi()
        if r > peak:
            peak = r
        return (nbits, peak)

    def tx_async_bits(self, data, n_bits, bit_us=280):
        """Simple OOK TX by bit-banging GDO0 while radio in async TX."""
        self.idle()
        g = Pin(Config.CC_GDO0, Pin.OUT, value=0)
        self.wr(IOCFG0, 0x0D)
        self.strobe(SFTX)
        self.strobe(STX)
        time.sleep_ms(1)
        for bi in range(n_bits):
            byte = data[bi >> 3]
            bit = (byte >> (7 - (bi & 7))) & 1
            g.value(bit)
            t_end = time.ticks_us() + bit_us
            while time.ticks_diff(time.ticks_us(), t_end) < 0:
                pass
        g.value(0)
        self.idle()
        self.gdo0 = Pin(Config.CC_GDO0, Pin.IN)
        return n_bits

    def tx(self, data):
        self.idle()
        self.strobe(SFTX)
        self.cs(0)
        try:
            self.spi.write(bytes((FIFO | BURST,)))
            self.spi.write(data)
        finally:
            self.cs(1)
        self.strobe(STX)

    def power_down(self):
        self.idle()
        self.strobe(SPWD)

    def release_bus(self):
        """SPI/CS/GDO0 → high-Z (input) so Q3 cut does not back-feed Node B pins."""
        try:
            self.cs.value(1)
        except Exception:
            pass
        try:
            self.spi.deinit()
        except Exception:
            pass
        for pin_no in (Config.CC_SCK, Config.CC_MOSI, Config.CC_MISO, Config.CC_CSN, Config.CC_GDO0):
            try:
                Pin(pin_no, Pin.IN)
            except Exception:
                pass
        self.spi = None
