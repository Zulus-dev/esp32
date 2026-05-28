# main.py - Colibry OS Node B async radio dispatcher
import asyncio
import gc
import machine
try:
    import micropython
except Exception:
    micropython = None

from config import Config
from hardware.radio_uart import RadioUART
from protocol import *
from radio.ble import BLEManager
from radio.managers.subghz import CC1101Manager
from radio.nrf24 import NRF24Manager
from radio.wifi_audit import WiFiAuditManager


class ColibryRadioNode:
    def __init__(self):
        self.uart = RadioUART()
        self.cc = CC1101Manager(self.uart)
        self.nrf = NRF24Manager(self.uart)
        self.wifi = WiFiAuditManager(self.uart)
        self.ble = BLEManager(self.uart)
        self.running = True
        try:
            self.wdt = machine.WDT(timeout=6000)
        except Exception:
            self.wdt = None

    async def keepalive(self):
        while self.running:
            free = 0
            try:
                free = gc.mem_free() // 1024
            except Exception:
                pass
            self.uart.send_tlv(TLV_KEEPALIVE, b"B|%dK" % free)
            await asyncio.sleep_ms(500)

    async def command_loop(self):
        while self.running:
            item = self.uart.read_tlv()
            if item:
                cmd, payload = item
                await self.handle_command(cmd, payload)
            await asyncio.sleep_ms(1)

    async def handle_command(self, cmd, payload):
        try:
            if cmd == CMD_SET_FREQ:
                await self.cc.set_frequency(payload)
            elif cmd == CMD_SET_MODULATION:
                await self.cc.set_modulation(payload)
            elif cmd in (CMD_START_SCAN, CMD_START_SUBGHZ_SCAN):
                self.cc.start_scan(payload)
            elif cmd == CMD_START_SNIFF:
                self.cc.start_sniff(payload)
            elif cmd in (CMD_REPLAY, CMD_REPLAY_SUBGHZ):
                await self.cc.replay(payload)
            elif cmd == CMD_RAW_TX:
                await self.cc.raw_tx(payload)
            elif cmd == CMD_SET_POWER:
                await self.cc.set_power(payload)
            elif cmd == CMD_SPECTRUM_SWEEP:
                self.cc.spectrum_sweep(payload)
            elif cmd == CMD_JAMMER:
                self.uart.send_tlv(TLV_STATUS, b"JAMMER_DISABLED_CONFIRM_REQUIRED")
            elif cmd == CMD_START_DEAUTH:
                self.wifi.start_deauth(payload)
            elif cmd == CMD_BEACON_SPAM:
                self.wifi.start_beacon_spam(payload)
            elif cmd == CMD_NRF_HONEYPOT:
                self.nrf.start_honeypot(payload)
            elif cmd == CMD_BLE_SNIFF:
                self.ble.start_sniff(payload)
            elif cmd == CMD_STOP_ALL:
                self.stop_all()
                self.uart.send_tlv(TLV_STATUS, b"STOPPED")
            elif cmd == CMD_REBOOT:
                self.uart.send_tlv(TLV_STATUS, b"REBOOTING")
                await asyncio.sleep_ms(100)
                machine.reset()
            elif cmd == CMD_SHUTDOWN:
                self.stop_all()
                self.uart.send_tlv(TLV_STATUS, b"READY_FOR_POWER_OFF")
                self.running = False
            else:
                self.uart.send_tlv(TLV_STATUS, b"UNKNOWN_CMD")
        except Exception as exc:
            self._critical("CMD 0x%02X failed: %s" % (cmd, exc))

    def _critical(self, text):
        self.uart.send_log(text)
        try:
            with open("radio_critical.log", "a") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def stop_all(self):
        self.cc.stop()
        self.wifi.stop()
        self.ble.stop()
        self.nrf.stop()
        gc.collect()

    async def run(self):
        try:
            machine.freq(Config.CPU_FREQ_HZ)
        except Exception:
            pass
        self.uart.send_log("Colibry Radio Node B booted")
        tasks = [
            asyncio.create_task(self.keepalive()),
            asyncio.create_task(self.command_loop()),
        ]
        try:
            while self.running:
                if self.wdt:
                    self.wdt.feed()
                await asyncio.sleep_ms(1000)
        finally:
            self.stop_all()
            for task in tasks:
                task.cancel()
            self.uart.close()


if __name__ == "__main__":
    try:
        asyncio.run(ColibryRadioNode().run())
    finally:
        asyncio.new_event_loop()
