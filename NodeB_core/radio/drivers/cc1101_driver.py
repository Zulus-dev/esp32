# CC1101 SPI driver — compact subset for scan/sniff/replay.
from machine import Pin, SPI
from config import Config

REG_IOCFG0=0x02; REG_FIFOTHR=0x03; REG_PKTLEN=0x06; REG_PKTCTRL1=0x07; REG_PKTCTRL0=0x08
REG_FREQ2=0x0D; REG_MDMCFG4=0x10; REG_MDMCFG3=0x11; REG_MDMCFG2=0x12; REG_DEVIATN=0x15
REG_MCSM0=0x18; REG_FOCCFG=0x19; REG_AGCCTRL2=0x1B; REG_AGCCTRL1=0x1C; REG_AGCCTRL0=0x1D
REG_FREND0=0x22; REG_FSCAL3=0x23; REG_FSCAL2=0x24; REG_FSCAL1=0x25; REG_FSCAL0=0x26; REG_TEST2=0x2C; REG_TEST1=0x2D; REG_TEST0=0x2E
PATABLE=0x3E; FIFO=0x3F
SRES=0x30; SRX=0x34; STX=0x35; SIDLE=0x36; SFRX=0x3A; SFTX=0x3B; SPWD=0x39
PARTNUM=0x30; VERSION=0x31; RSSI=0x34; RXBYTES=0x3B
READ=0x80; BURST=0x40

class CC1101:
    def __init__(self):
        self.spi = SPI(1, baudrate=4000000, polarity=0, phase=0, sck=Pin(Config.CC_SCK), mosi=Pin(Config.CC_MOSI), miso=Pin(Config.CC_MISO))
        self.cs = Pin(Config.CC_CSN, Pin.OUT, value=1)
        self.gdo0 = Pin(Config.CC_GDO0, Pin.IN)
        self.freq_khz = 433920
    def _x(self, a, v=0):
        self.cs(0); r=self.spi.write_readinto(bytes((a,v)), bytearray(2)); self.cs(1); return r[1]
    def strobe(self, s):
        self.cs(0); self.spi.write(bytes((s,))); self.cs(1)
    def wr(self, r, v): self._x(r, v)
    def rd(self, r): return self._x(r|READ, 0)
    def init(self):
        self.strobe(SRES)
        self.wr(REG_IOCFG0,0x06); self.wr(REG_FIFOTHR,0x47); self.wr(REG_PKTCTRL1,0x04); self.wr(REG_PKTCTRL0,0x00)
        self.wr(REG_PKTLEN,0x3D); self.wr(REG_MDMCFG4,0xCA); self.wr(REG_MDMCFG3,0x83); self.wr(REG_MDMCFG2,0x30)
        self.wr(REG_DEVIATN,0x35); self.wr(REG_MCSM0,0x18); self.wr(REG_FOCCFG,0x16)
        self.wr(REG_AGCCTRL2,0x43); self.wr(REG_AGCCTRL1,0x40); self.wr(REG_AGCCTRL0,0x91)
        self.wr(REG_FREND0,0x11); self.wr(REG_FSCAL3,0xE9); self.wr(REG_FSCAL2,0x2A); self.wr(REG_FSCAL1,0x00); self.wr(REG_FSCAL0,0x1F)
        self.wr(REG_TEST2,0x81); self.wr(REG_TEST1,0x35); self.wr(REG_TEST0,0x09); self.set_freq(433920); self.set_power(0xC0)
        return (self.rd(PARTNUM), self.rd(VERSION))
    def set_freq(self, khz):
        self.freq_khz = int(khz); f = int((self.freq_khz * 65536) // 26000)
        self.wr(REG_FREQ2,(f>>16)&255); self.wr(REG_FREQ1 if False else 0x0E,(f>>8)&255); self.wr(0x0F,f&255)
    def set_power(self, p): self.cs(0); self.spi.write(bytes((PATABLE, p & 255))); self.cs(1)
    def set_ook(self): self.wr(REG_MDMCFG2,0x30)
    def set_2fsk(self): self.wr(REG_MDMCFG2,0x00)
    def idle(self): self.strobe(SIDLE)
    def rx(self): self.strobe(SFRX); self.strobe(SRX)
    def rssi(self):
        v=self.rd(RSSI); v = v-256 if v >= 128 else v; return (v//2)-74
    def read_pkt(self, buf):
        n = self.rd(RXBYTES) & 0x7F
        if n <= 0: return 0
        if n > len(buf): n = len(buf)
        self.cs(0); self.spi.write(bytes((FIFO|READ|BURST,))); self.spi.readinto(memoryview(buf)[:n]); self.cs(1); return n
    def tx(self, data):
        self.idle(); self.strobe(SFTX); self.cs(0); self.spi.write(bytes((FIFO|BURST,))); self.spi.write(data); self.cs(1); self.strobe(STX)
    def power_down(self): self.idle(); self.strobe(SPWD)
