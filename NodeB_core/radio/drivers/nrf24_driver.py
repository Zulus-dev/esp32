# NRF24L01+ SPI driver: Enhanced ShockBurst RX/TX, dynamic payload, scan helpers.
from machine import Pin, SPI
from config import Config

R_REGISTER=0x00; W_REGISTER=0x20; R_RX_PAYLOAD=0x61; W_TX_PAYLOAD=0xA0; FLUSH_TX=0xE1; FLUSH_RX=0xE2; ACTIVATE=0x50; R_RX_PL_WID=0x60; NOP=0xFF
CONFIG=0x00; EN_AA=0x01; EN_RXADDR=0x02; SETUP_AW=0x03; SETUP_RETR=0x04; RF_CH=0x05; RF_SETUP=0x06; STATUS=0x07; RX_ADDR_P0=0x0A; TX_ADDR=0x10; RX_PW_P0=0x11; FIFO_STATUS=0x17; DYNPD=0x1C; FEATURE=0x1D
MASK_RX_DR=0x40; MASK_TX_DS=0x20; MASK_MAX_RT=0x10; PWR_UP=0x02; PRIM_RX=0x01

class NRF24:
    def __init__(self):
        self.spi=SPI(1, baudrate=8000000, polarity=0, phase=0, sck=Pin(Config.NRF_SCK), mosi=Pin(Config.NRF_MOSI), miso=Pin(Config.NRF_MISO))
        self.cs=Pin(Config.NRF_CSN, Pin.OUT, value=1); self.ce=Pin(Config.NRF_CE, Pin.OUT, value=0)
        self.irq=Pin(Config.NRF_IRQ, Pin.IN)
        self.addr=b"\xe7\xe7\xe7\xe7\xe7"; self.channel=2
    def _cmd(self, c, data=None, read=0):
        self.cs(0); self.spi.write(bytes((c,)))
        out=None
        if data is not None: self.spi.write(data)
        if read: out=bytearray(read); self.spi.readinto(out)
        self.cs(1); return out
    def rd(self,r): return self._cmd(R_REGISTER|r, read=1)[0]
    def wr(self,r,v): self._cmd(W_REGISTER|r, bytes((v&255,)))
    def wr_buf(self,r,b): self._cmd(W_REGISTER|r, b)
    def init(self, channel=2, rate=1, autoack=True):
        self.ce(0); self.channel=channel; self.wr(CONFIG,0x0C); self.wr(EN_AA,0x3F if autoack else 0); self.wr(EN_RXADDR,0x3F)
        self.wr(SETUP_AW,0x03); self.wr(SETUP_RETR,0x2F if autoack else 0); self.set_rate(rate); self.set_channel(channel)
        self.wr_buf(RX_ADDR_P0,self.addr); self.wr_buf(TX_ADDR,self.addr); self.wr(RX_PW_P0,32)
        self._cmd(ACTIVATE, b"\x73"); self.wr(FEATURE,0x07); self.wr(DYNPD,0x3F); self.flush(); return self.rd(STATUS)
    def set_channel(self,ch):
        if ch<0: ch=0
        if ch>125: ch=125
        self.channel=ch; self.wr(RF_CH,ch)
    def set_rate(self,rate):
        # rate: 0=250k, 1=1M, 2=2M, PA high
        v=0x06
        if rate==0: v=0x26
        elif rate==2: v=0x0E
        self.wr(RF_SETUP,v)
    def set_addr(self,addr):
        if len(addr)>5: addr=addr[:5]
        self.addr=addr; self.wr_buf(RX_ADDR_P0,addr); self.wr_buf(TX_ADDR,addr)
    def flush(self): self._cmd(FLUSH_RX); self._cmd(FLUSH_TX); self.wr(STATUS,0x70)
    def rx(self): self.ce(0); self.wr(CONFIG,0x0F); self.ce(1)
    def power_down(self): self.ce(0); self.wr(CONFIG,0x0C)
    def available(self): return (self.rd(STATUS)&MASK_RX_DR) or not (self.rd(FIFO_STATUS)&0x01)
    def read_payload(self,buf):
        n=self._cmd(R_RX_PL_WID, read=1)[0]
        if n<1 or n>32: self.flush(); return 0
        if n>len(buf): n=len(buf)
        self.cs(0); self.spi.write(bytes((R_RX_PAYLOAD,))); self.spi.readinto(memoryview(buf)[:n]); self.cs(1); self.wr(STATUS,MASK_RX_DR); return n
    def tx(self,payload):
        self.ce(0); self.wr(CONFIG,0x0E); self._cmd(FLUSH_TX); self._cmd(W_TX_PAYLOAD,payload[:32]); self.ce(1)
        return True
