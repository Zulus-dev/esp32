import gc
from protocol import *
from lib import mempool
class Manager:
    def __init__(self, link): self.link=link; self.ble=None
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_BLE); return KA_STATE_IDLE
        mempool.ensure_rf_workspace("ble")
        try:
            import bluetooth; self.ble=bluetooth.BLE(); self.ble.active(True)
            if op==OP_START: self.ble.gap_scan(0, 30000, 30000)
        except Exception: self.link.send_status(ST_ERR,ERR_NO_DRIVER); return KA_STATE_IDLE
        self.link.send_status(ST_MOD_ON,MOD_BLE); return KA_STATE_SCAN
    async def stop(self):
        try:
            if self.ble: self.ble.gap_scan(None); self.ble.active(False)
        except Exception: pass
        self.ble=None; mempool.drop_rf(); gc.collect(); gc.collect()
