# BLE manager: IRQ-based ADV scan and explicit spoof advertising when supported.
import gc
from protocol import *
from lib import mempool

_IRQ_SCAN_RESULT=5; _IRQ_SCAN_DONE=6

class Manager:
    def __init__(self, link):
        self.link=link; self.ble=None; self.count=0; self.filter=None
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_BLE); return KA_STATE_IDLE
        mempool.ensure_rf_workspace("ble")
        try:
            import bluetooth; self.ble=bluetooth.BLE(); self.ble.active(True); self.ble.irq(self._irq)
        except Exception: self.link.send_status(ST_ERR,ERR_NO_DRIVER); return KA_STATE_IDLE
        if op==OP_START:
            active = len(payload)>1 and payload[1]==BLE_MODE_ACTIVE_SCAN
            try: self.ble.gap_scan(0, 30000, 30000, active)
            except TypeError: self.ble.gap_scan(0, 30000, 30000)
            self.link.send_status(ST_MOD_ON,MOD_BLE); return KA_STATE_SCAN
        if op==OP_TX_RAW and len(payload)>1:
            try: self.ble.gap_advertise(100000, adv_data=payload[1:32]); self.link.send_status(ST_DONE,MOD_BLE); return KA_STATE_TX
            except Exception: self.link.send_status(ST_UNSUPPORTED,MOD_BLE); return KA_STATE_IDLE
        self.link.send_status(ST_MOD_ON,MOD_BLE); return KA_STATE_IDLE
    def _irq(self, event, data):
        if event==_IRQ_SCAN_RESULT:
            try: at, addr, adv_type, rssi, adv = data
            except Exception: return
            n=len(adv); n=31 if n>31 else n
            p=bytearray(10+n); p[0]=at&255
            for i in range(6): p[1+i]=addr[i] if i<len(addr) else 0
            p[7]=adv_type&255; p[8]=rssi&255; p[9]=n
            if n: p[10:]=adv[:n]
            self.link.send_tlv(TLV_BLE_ADV,p); self.count+=1
        elif event==_IRQ_SCAN_DONE:
            self.link.send_status(ST_DONE, self.count if self.count<65535 else 65535)
    async def stop(self):
        try:
            if self.ble: self.ble.gap_scan(None); self.ble.active(False)
        except Exception: pass
        self.ble=None; mempool.drop_rf(); gc.collect(); gc.collect()
