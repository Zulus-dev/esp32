# radio/nrf24.py - async NRF24 HID honeypot facade
import asyncio
from protocol import TLV_STATUS, TLV_NRF_HID_EVENT


class NRF24Manager:
    def __init__(self, uart):
        self.uart = uart
        self.honeypot_active = False
        self.task = None

    def start_honeypot(self, payload=None):
        self.honeypot_active = True
        self.uart.send_tlv(TLV_STATUS, b"NRF_HONEYPOT_STARTED")
        if self.task is None:
            self.task = asyncio.create_task(self._honeypot_loop())

    async def _honeypot_loop(self):
        try:
            while self.honeypot_active:
                self.uart.send_tlv(TLV_NRF_HID_EVENT, b"KEY:A")
                await asyncio.sleep_ms(250)
        finally:
            self.task = None

    def stop(self):
        self.honeypot_active = False
        if self.task:
            self.task.cancel()
            self.task = None
        self.uart.send_log("NRF24 stopped")
