# Sub-GHz manager: CC1101 lazy driver, one bounded task, last packet slab.
import asyncio, gc
from protocol import *
from lib import mempool

class Manager:
    def __init__(self, link):
        self.link=link; self.drv=None; self.task=None; self.buf=None; self.last_len=0; self.freq=433920
    async def _load(self):
        if self.drv: return True
        self.buf = mempool.ensure_rf_workspace("cc1101")
        from radio.drivers.cc1101_driver import CC1101
        self.drv=CC1101(); self.drv.init(); return True
    async def stop(self):
        if self.task:
            self.task.cancel(); self.task=None
        if self.drv:
            try: self.drv.power_down()
            except Exception: pass
        self.drv=None; mempool.drop_rf()
        for n in ("radio.drivers.cc1101_driver",):
            try:
                import sys; sys.modules.pop(n, None)
            except Exception: pass
        gc.collect(); gc.collect()
    def _send_rssi(self, fk, r):
        b=bytearray(5); b[0]=(fk>>24)&255; b[1]=(fk>>16)&255; b[2]=(fk>>8)&255; b[3]=fk&255; b[4]=r&255
        self.link.send_tlv(TLV_RSSI,b)
    async def _scan(self):
        freqs=(315000,433920,868350,915000)
        while self.task:
            for f in freqs:
                self.drv.set_freq(f); self.drv.rx(); await asyncio.sleep_ms(45); self._send_rssi(f,self.drv.rssi())
            await asyncio.sleep_ms(120)
    async def _sniff(self):
        self.drv.set_freq(self.freq); self.drv.rx(); buf=self.buf
        while self.task:
            n=self.drv.read_pkt(buf)
            if n:
                self.last_len = min(n, 48)
                p=bytearray(7+self.last_len); p[0]=MOD_SUBGHZ; f=self.freq
                p[1]=(f>>24)&255; p[2]=(f>>16)&255; p[3]=(f>>8)&255; p[4]=f&255; p[5]=self.drv.rssi()&255; p[6]=self.last_len
                p[7:]=memoryview(buf)[:self.last_len]
                self.link.send_tlv(TLV_PKT,p)
            await asyncio.sleep_ms(25)
    async def handle(self, payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP:
            await self.stop(); self.link.send_status(ST_MOD_OFF, MOD_SUBGHZ); return KA_STATE_IDLE
        await self._load()
        if len(payload)>=6:
            self.freq=(payload[2]<<24)|(payload[3]<<16)|(payload[4]<<8)|payload[5]
        if op==OP_ONCE:
            self.drv.set_freq(self.freq); self.drv.rx(); await asyncio.sleep_ms(25); self._send_rssi(self.freq,self.drv.rssi()); self.link.send_status(ST_DONE, MOD_SUBGHZ); return KA_STATE_IDLE
        if op==OP_REPLAY and self.last_len:
            self.drv.tx(memoryview(self.buf)[:self.last_len]); self.link.send_status(ST_DONE, MOD_SUBGHZ); return KA_STATE_TX
        if op==OP_TX_RAW and len(payload)>1:
            self.drv.tx(payload[1:]); self.link.send_status(ST_DONE, MOD_SUBGHZ); return KA_STATE_TX
        if op==OP_START and len(payload)>1:
            if self.task: self.task.cancel()
            mode=payload[1]
            self.task=asyncio.create_task(self._scan() if mode==SUB_MODE_SCAN else self._sniff())
            self.link.send_status(ST_MOD_ON, MOD_SUBGHZ)
            return KA_STATE_SCAN if mode==SUB_MODE_SCAN else KA_STATE_SNIFF
        self.link.send_status(ST_MOD_ON, MOD_SUBGHZ); return KA_STATE_IDLE
