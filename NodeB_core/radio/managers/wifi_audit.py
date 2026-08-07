# Wi-Fi audit manager: MicroPython-safe AP scan; raw injection is reported unsupported if firmware lacks API.
import asyncio, gc
from protocol import *
from lib import mempool

class Manager:
    def __init__(self, link): self.link=link; self.wlan=None; self.task=None
    async def handle(self,payload):
        op=payload[0] if payload else OP_CONFIG
        if op==OP_STOP: await self.stop(); self.link.send_status(ST_MOD_OFF,MOD_WIFI); return KA_STATE_IDLE
        mempool.ensure_rf_workspace("wifi")
        try:
            import network; self.wlan=network.WLAN(network.STA_IF); self.wlan.active(True)
        except Exception: self.link.send_status(ST_ERR,ERR_NO_DRIVER); return KA_STATE_IDLE
        if op==OP_START:
            mode=payload[1] if len(payload)>1 else WIFI_MODE_SCAN
            if mode in (WIFI_MODE_DEAUTH, WIFI_MODE_BEACON, WIFI_MODE_MON):
                self.link.send_status(ST_UNSUPPORTED, mode); return KA_STATE_IDLE
            if self.task: self.task.cancel()
            self.task=asyncio.create_task(self._scan_once()); self.link.send_status(ST_MOD_ON,MOD_WIFI); return KA_STATE_SCAN
        self.link.send_status(ST_MOD_ON,MOD_WIFI); return KA_STATE_IDLE
    async def _scan_once(self):
        try: aps=self.wlan.scan()
        except Exception: aps=()
        c=0
        for ap in aps:
            if c>=24: break
            ssid,bssid,ch,rssi,auth,hidden=ap[0],ap[1],ap[2],ap[3],ap[4],ap[5]
            if len(ssid)>24: ssid=ssid[:24]
            p=bytearray(6+len(ssid)); p[0]=MOD_WIFI; p[1]=0; p[2]=ch&255; p[3]=rssi&255; p[4]=auth&255; p[5]=len(ssid); p[6:]=ssid
            self.link.send_tlv(TLV_SCAN_RESULT,p); c+=1; await asyncio.sleep_ms(8)
        self.link.send_status(ST_DONE, c)
    async def stop(self):
        if self.task: self.task.cancel(); self.task=None
        try:
            if self.wlan: self.wlan.active(False)
        except Exception: pass
        self.wlan=None; mempool.drop_rf(); gc.collect(); gc.collect()
