import gc
from protocol import *
from lib import mempool
class Manager:
    def __init__(self, link): self.link=link; self.wlan=None
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_WIFI); return KA_STATE_IDLE
        mempool.ensure_rf_workspace("wifi")
        try:
            import network; self.wlan=network.WLAN(network.STA_IF); self.wlan.active(True)
        except Exception: self.link.send_status(ST_ERR,ERR_NO_DRIVER); return KA_STATE_IDLE
        self.link.send_status(ST_MOD_ON,MOD_WIFI); return KA_STATE_SCAN
    async def stop(self):
        try:
            if self.wlan: self.wlan.active(False)
        except Exception: pass
        self.wlan=None; mempool.drop_rf(); gc.collect(); gc.collect()
