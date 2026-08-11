# NRF24 manager: bounded RX/HID/honeypot modes, events via TLV_NRF_EVENT.
import asyncio, gc
from protocol import *
from lib import mempool

EV_RX=1; EV_HID=2; EV_HONEYPOT=3; EV_ADDR=4

class Manager:
    def __init__(self, link):
        self.link=link; self.drv=None; self.task=None; self.buf=None; self.mode=NRF_MODE_RX; self.channel=2; self._tlv=bytearray(40)
    async def _load(self):
        if self.drv: return
        self.buf=mempool.ensure_rf_workspace("nrf")
        from radio.drivers.nrf24_driver import NRF24
        self.drv=NRF24(); self.drv.init(self.channel, 0, False)
    async def stop(self):
        if self.task: self.task.cancel(); self.task=None
        if self.drv:
            try: self.drv.power_down()
            except Exception: pass
        self.drv=None; mempool.drop_rf()
        try:
            import sys; sys.modules.pop("radio.drivers.nrf24_driver", None)
        except Exception: pass
        gc.collect(); gc.collect()
    def _evt(self, ev, pipe, ch, rssi, data=None, n=0):
        if data is None: n=0
        if n>32: n=32
        p=self._tlv; p[0]=ev; p[1]=pipe; p[2]=ch; p[3]=rssi & 255; p[4]=n
        if n: p[5:5+n]=memoryview(data)[:n]
        self.link.send_tlv(TLV_NRF_EVENT, memoryview(p)[:5+n])
    async def _rx_loop(self):
        self.drv.rx(); buf=self.buf
        while self.task:
            if self.drv.available():
                n=self.drv.read_payload(buf)
                if n:
                    ev=EV_HID if self.mode==NRF_MODE_HID else EV_RX
                    self._evt(ev,0,self.channel,0,buf,n)
            await asyncio.sleep_ms(20)
    async def _discover(self):
        chans=(2,5,8,11,14,17,20,23,26,29,32,35,38,41,44,47,50,53,56,59,62,65,68,71,74,77,80)
        while self.task:
            for ch in chans:
                self.channel=ch; self.drv.set_channel(ch); self.drv.rx(); await asyncio.sleep_ms(8)
                if self.drv.available(): self._evt(EV_ADDR,0,ch,0,None,0)
            await asyncio.sleep_ms(60)
    async def _honeypot(self):
        report=b"\x00\x00\x04\x00\x00\x00\x00\x00"  # HID 'a' test report, only on explicit mode
        while self.task:
            self.drv.tx(report); self._evt(EV_HONEYPOT,0,self.channel,0,report,len(report)); await asyncio.sleep_ms(1000)
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_NRF); return KA_STATE_IDLE
        await self._load()
        if len(payload)>2: self.channel=payload[2]; self.drv.set_channel(self.channel)
        if op==OP_TX_RAW and len(payload)>1:
            self.drv.tx(payload[1:]); self.link.send_status(ST_DONE,MOD_NRF); return KA_STATE_TX
        if op==OP_START:
            if self.task: self.task.cancel()
            self.mode=payload[1] if len(payload)>1 else NRF_MODE_HID
            if self.mode==NRF_MODE_HONEYPOT: self.task=asyncio.create_task(self._honeypot()); st=KA_STATE_HONEYPOT
            elif self.mode==NRF_MODE_RX: self.task=asyncio.create_task(self._discover()); st=KA_STATE_SCAN
            else: self.task=asyncio.create_task(self._rx_loop()); st=KA_STATE_SNIFF
            self.link.send_status(ST_MOD_ON,MOD_NRF); return st
        self.link.send_status(ST_MOD_ON,MOD_NRF); return KA_STATE_IDLE
