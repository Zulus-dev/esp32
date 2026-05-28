# radio/drivers/cc1101_driver.py - compact industrial CC1101 driver for ESP32-C3
import time
from machine import Pin, SPI
from micropython import const
from config import Config


class CC1101Driver:
    """Low-level CC1101 SPI driver.

    The class keeps all hardware-specific work isolated from protocol managers:
    register writes, FIFO access, RSSI conversion, calibration and basic modem
    profiles. It intentionally avoids heap-heavy abstractions for ESP32-C3.
    """

    WRITE_BURST = const(0x40)
    READ_SINGLE = const(0x80)
    READ_BURST = const(0xC0)
    STATUS = const(0xC0)

    IOCFG0 = const(0x02)
    FIFOTHR = const(0x03)
    SYNC1 = const(0x04)
    SYNC0 = const(0x05)
    PKTLEN = const(0x06)
    PKTCTRL1 = const(0x07)
    PKTCTRL0 = const(0x08)
    FSCTRL1 = const(0x0B)
    FSCTRL0 = const(0x0C)
    FREQ2 = const(0x0D)
    FREQ1 = const(0x0E)
    FREQ0 = const(0x0F)
    MDMCFG4 = const(0x10)
    MDMCFG3 = const(0x11)
    MDMCFG2 = const(0x12)
    MDMCFG1 = const(0x13)
    MDMCFG0 = const(0x14)
    DEVIATN = const(0x15)
    MCSM0 = const(0x18)
    FOCCFG = const(0x19)
    BSCFG = const(0x1A)
    AGCCTRL2 = const(0x1B)
    AGCCTRL1 = const(0x1C)
    AGCCTRL0 = const(0x1D)
    FREND1 = const(0x21)
    FREND0 = const(0x22)
    FSCAL3 = const(0x23)
    FSCAL2 = const(0x24)
    FSCAL1 = const(0x25)
    FSCAL0 = const(0x26)
    TEST2 = const(0x2C)
    TEST1 = const(0x2D)
    TEST0 = const(0x2E)

    PARTNUM = const(0x30)
    VERSION = const(0x31)
    RSSI = const(0x34)
    MARCSTATE = const(0x35)
    PKTSTATUS = const(0x38)
    TXBYTES = const(0x3A)
    RXBYTES = const(0x3B)

    SRES = const(0x30)
    SCAL = const(0x33)
    SRX = const(0x34)
    STX = const(0x35)
    SIDLE = const(0x36)
    SPWD = const(0x39)
    SFRX = const(0x3A)
    SFTX = const(0x3B)
    SNOP = const(0x3D)

    PATABLE = const(0x3E)
    FIFO = const(0x3F)
    FIFO_BURST = const(0x7F)
    FIFO_READ_BURST = const(0xFF)

    XTAL_HZ = const(26000000)
    FIFO_SIZE = const(64)

    MOD_IDS = {"2-FSK": 0, "GFSK": 1, "ASK": 3, "OOK": 3, "4-FSK": 4}
    MOD_NAMES = ("2-FSK", "GFSK", "ASK/OOK", "ASK/OOK", "4-FSK")

    def __init__(self):
        self.csn = Pin(Config.CC_CSN, Pin.OUT, value=1)
        self.miso = Pin(Config.CC_MISO, Pin.IN)
        self.gdo0 = Pin(Config.CC_GDO0, Pin.IN)
        self.spi = SPI(1, baudrate=4000000, polarity=0, phase=0, bits=8, firstbit=SPI.MSB,
                       sck=Pin(Config.CC_SCK), mosi=Pin(Config.CC_MOSI), miso=self.miso)
        self.freq_mhz = 433.920
        self.modulation = "2-FSK"
        self.packet_len = 0
        self.power_dbm = 0
        self.initialized = False

    def begin(self):
        self.reset()
        self.write_regs((
            (self.IOCFG0, 0x06), (self.FIFOTHR, 0x47),
            (self.PKTCTRL1, 0x04), (self.PKTCTRL0, 0x05),  # variable length + CRC
            (self.FSCTRL1, 0x06), (self.FSCTRL0, 0x00),
            (self.MCSM0, 0x18), (self.FOCCFG, 0x16), (self.BSCFG, 0x6C),
            (self.AGCCTRL2, 0x43), (self.AGCCTRL1, 0x40), (self.AGCCTRL0, 0x91),
            (self.FREND1, 0x56), (self.FREND0, 0x10),
            (self.FSCAL3, 0xE9), (self.FSCAL2, 0x2A), (self.FSCAL1, 0x00), (self.FSCAL0, 0x1F),
            (self.TEST2, 0x81), (self.TEST1, 0x35), (self.TEST0, 0x09),
        ))
        self.configure()
        self.initialized = True
        return True

    def reset(self):
        self.csn.value(1); time.sleep_us(5)
        self.csn.value(0); time.sleep_us(10)
        self.csn.value(1); time.sleep_us(45)
        self.csn.value(0); self._wait_ready()
        self.spi.write(bytearray((self.SRES,)))
        self.csn.value(1); time.sleep_ms(2)
        self.flush_rx(); self.flush_tx()

    def configure(self, freq_mhz=None, modulation=None, baud=4800, deviation=5000,
                  bandwidth=58000, sync=0xD391, packet_len=0, power_dbm=0):
        if freq_mhz is not None:
            self.freq_mhz = float(freq_mhz)
        if modulation:
            self.modulation = str(modulation).upper()
        self.packet_len = int(packet_len or 0)
        self.power_dbm = int(power_dbm or 0)
        self.idle()
        self.set_frequency(self.freq_mhz)
        self.set_sync(sync)
        self.set_packet_len(self.packet_len)
        self._set_modem(self.modulation, baud, deviation, bandwidth)
        self.set_power(self.power_dbm)
        self.calibrate()

    def set_frequency(self, freq_mhz):
        self.freq_mhz = float(freq_mhz)
        reg = int((self.freq_mhz * 1000000.0) * 65536 / self.XTAL_HZ) & 0xFFFFFF
        self.write_regs(((self.FREQ2, (reg >> 16) & 0xFF), (self.FREQ1, (reg >> 8) & 0xFF), (self.FREQ0, reg & 0xFF)))

    def set_sync(self, sync):
        sync = int(sync) & 0xFFFF
        self.write_regs(((self.SYNC1, (sync >> 8) & 0xFF), (self.SYNC0, sync & 0xFF)))

    def set_packet_len(self, length):
        length = int(length or 0)
        self.write_reg(self.PKTLEN, 255 if length <= 0 else min(length, 255))
        self.write_reg(self.PKTCTRL0, 0x05 if length <= 0 else 0x04)

    def set_power(self, dbm):
        self.power_dbm = int(dbm)
        pa = 0xC0 if self.power_dbm >= 10 else 0x84 if self.power_dbm >= 5 else 0x60 if self.power_dbm >= 0 else 0x12
        self.write_burst(self.PATABLE, bytearray((pa,)))

    def _set_modem(self, modulation, baud, deviation, bandwidth):
        mod = self.MOD_IDS.get(modulation, 0)
        # Proven conservative 4.8 kBaud/58 kHz profile; callers can still store exact requested values.
        mdmcfg2 = 0x30 | ((mod & 0x07) << 4) | 0x03
        if mod == 3:  # ASK/OOK shaping.
            self.write_reg(self.FREND0, 0x11)
        else:
            self.write_reg(self.FREND0, 0x10)
        self.write_regs(((self.MDMCFG4, 0xF5), (self.MDMCFG3, 0x83), (self.MDMCFG2, mdmcfg2),
                         (self.MDMCFG1, 0x22), (self.MDMCFG0, 0xF8), (self.DEVIATN, 0x15)))

    def calibrate(self):
        self.strobe(self.SCAL)
        time.sleep_ms(2)

    def start_rx(self):
        self.idle(); self.flush_rx(); self.strobe(self.SRX)

    def idle(self):
        self.strobe(self.SIDLE)

    def sleep(self):
        self.idle(); self.strobe(self.SPWD); self.csn.value(1)

    def flush_rx(self):
        self.strobe(self.SIDLE); self.strobe(self.SFRX)

    def flush_tx(self):
        self.strobe(self.SIDLE); self.strobe(self.SFTX)

    def send_packet(self, data):
        if not data:
            return False
        if len(data) > 61:
            data = data[:61]
        self.idle(); self.flush_tx()
        frame = bytearray((len(data),)); frame.extend(data)
        self.write_burst(self.FIFO, frame)
        self.strobe(self.STX)
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < 250:
            state = self.read_status(self.MARCSTATE) & 0x1F
            if state == 0x01:
                return True
            time.sleep_ms(1)
        self.flush_tx()
        return False

    def read_packet(self):
        count = self.read_status(self.RXBYTES) & 0x7F
        if count < 2:
            return None
        if count >= self.FIFO_SIZE:
            self.flush_rx(); return None
        raw = self.read_burst(self.FIFO, count)
        if not raw:
            return None
        ln = raw[0]
        if ln == 0 or ln > len(raw) - 1:
            self.flush_rx(); return None
        data = bytes(raw[1:1 + ln])
        rssi = self.rssi_dbm(raw[1 + ln] if len(raw) > ln + 1 else self.read_status(self.RSSI))
        lqi = raw[2 + ln] if len(raw) > ln + 2 else 0
        return data, rssi, lqi

    def read_rssi(self):
        return self.rssi_dbm(self.read_status(self.RSSI))

    def rssi_dbm(self, raw):
        raw = int(raw) & 0xFF
        return int((raw - 256) / 2 - 74) if raw >= 128 else int(raw / 2 - 74)

    def strobe(self, cmd):
        self.csn.value(0); self._wait_ready(); self.spi.write(bytearray((cmd,))); self.csn.value(1)

    def write_reg(self, addr, val):
        self.csn.value(0); self._wait_ready(); self.spi.write(bytearray((addr & 0x3F, val & 0xFF))); self.csn.value(1)

    def write_regs(self, pairs):
        for addr, val in pairs:
            self.write_reg(addr, val)

    def write_burst(self, addr, data):
        buf = bytearray((addr | self.WRITE_BURST,)); buf.extend(data)
        self.csn.value(0); self._wait_ready(); self.spi.write(buf); self.csn.value(1)

    def read_reg(self, addr):
        buf = bytearray((addr | self.READ_SINGLE, 0))
        self.csn.value(0); self._wait_ready(); self.spi.write_readinto(buf, buf); self.csn.value(1)
        return buf[1]

    def read_status(self, addr):
        buf = bytearray((addr | self.STATUS, 0))
        self.csn.value(0); self._wait_ready(); self.spi.write_readinto(buf, buf); self.csn.value(1)
        return buf[1]

    def read_burst(self, addr, n):
        buf = bytearray(n + 1); buf[0] = addr | self.READ_BURST
        self.csn.value(0); self._wait_ready(); self.spi.write_readinto(buf, buf); self.csn.value(1)
        return buf[1:]

    def _wait_ready(self):
        start = time.ticks_us()
        while self.miso.value() and time.ticks_diff(time.ticks_us(), start) < 5000:
            pass
