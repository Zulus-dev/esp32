# radio/wifi_audit.py - async Wi-Fi audit facade
import asyncio
from protocol import TLV_STATUS, TLV_WIFI_FRAME


class WiFiAuditManager:
    def __init__(self, uart):
        self.uart = uart
        self.task = None
        self.mode = None
        self.buffer = []

    def start_deauth(self, payload):
        self.mode = "deauth"
        self.uart.send_tlv(TLV_STATUS, b"DEAUTH_STARTED")
        if self.task is None:
            self.task = asyncio.create_task(self._run_loop())

    def start_beacon_spam(self, payload):
        self.mode = "beacon"
        self.uart.send_tlv(TLV_STATUS, b"BEACON_SPAM_STARTED")
        if self.task is None:
            self.task = asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        try:
            while self.mode:
                if self.mode == "deauth":
                    self._push(TLV_WIFI_FRAME, b"DEAUTH|00:11:22:33:44:55")
                    delay = 100
                else:
                    self._push(TLV_WIFI_FRAME, b"BEACON|Colibry")
                    delay = 180
                await self._flush_buffer(limit=4)
                await asyncio.sleep_ms(delay)
        finally:
            self.task = None

    def _push(self, tlv_type, data):
        self.buffer.append((tlv_type, data))
        if len(self.buffer) > 32:
            self.buffer.pop(0)

    async def _flush_buffer(self, limit=8):
        count = 0
        while self.buffer and count < limit:
            tlv_type, data = self.buffer.pop(0)
            self.uart.send_tlv(tlv_type, data)
            count += 1
            await asyncio.sleep_ms(0)

    def stop(self):
        self.mode = None
        if self.task:
            self.task.cancel()
            self.task = None
        self.buffer = []
        self.uart.send_log("WiFi audit stopped")
