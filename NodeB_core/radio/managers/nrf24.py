import gc
from protocol import *
from lib import mempool
class Manager:
    def __init__(self, link): self.link=link; self.active=False
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_NRF); return KA_STATE_IDLE
        mempool.ensure_rf_workspace("nrf"); self.active=True; self.link.send_status(ST_MOD_ON,MOD_NRF)
        return KA_STATE_HONEYPOT if (len(payload)>1 and payload[1]==NRF_MODE_HONEYPOT) else KA_STATE_SNIFF
    async def stop(self):
        self.active=False; mempool.drop_rf(); gc.collect(); gc.collect()
