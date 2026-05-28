# radio/ble.py - async BLE advertising telemetry facade
import asyncio
from protocol import TLV_STATUS, TLV_BLE_ADV


class BLEManager:
    def __init__(self, uart):
        self.uart = uart
        self.sniffing = False
        self.task = None
        self.ble = None

    def start_sniff(self, payload=None):
        self.sniffing = True
        self.uart.send_tlv(TLV_STATUS, b"BLE_SNIFF_STARTED")
        if self.task is None:
            self.task = asyncio.create_task(self._sniff_loop())

    async def _sniff_loop(self):
        try:
            while self.sniffing:
                # Compact binary-ish UI payload until a platform-specific BLE IRQ decoder is added.
                self.uart.send_tlv(TLV_BLE_ADV, b"ADV|AA:BB:CC:DD:EE:FF|-45")
                await asyncio.sleep_ms(300)
        finally:
            self.task = None

    def stop(self):
        self.sniffing = False
        if self.task:
            self.task.cancel()
            self.task = None
        self.uart.send_log("BLE stopped")
