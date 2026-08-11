# main.py — Node B: binary link + exclusive module arm
#
# RAM RULES:
# 1. Kernel only: UART + cmd loop + KA. RF drivers lazy later.
# 2. STOP / RF_OFF / shutdown → clear mod state + gc.
# 3. Wire = codes only (protocol.py). No str/JSON.
# 4. KA every 2 s. Command poll ~10–20 ms idle.

import asyncio
import gc
import machine

from config import Config
from hardware.link_uart import LinkUART
from protocol import *


class ColibryNodeB:
    def __init__(self):
        self.link = LinkUART()
        self.running = True
        self.rf_rail = False
        self.active_mod = MOD_NONE
        self.mod_state = KA_STATE_IDLE
        self._manager = None
        self._manager_mod = MOD_NONE
        self._boot_status_left = 12
        try:
            self.wdt = machine.WDT(timeout=Config.WDT_TIMEOUT_MS)
        except Exception:
            self.wdt = None

    def _ka_flags(self):
        f = 0
        if self.rf_rail:
            f |= KA_FLAG_RF_RAIL
        if self.active_mod != MOD_NONE:
            f |= KA_FLAG_MOD_ACTIVE
        return f

    async def keepalive(self):
        while self.running:
            free_kb = 0
            try:
                free_kb = gc.mem_free() // 1024
            except Exception:
                pass
            self.link.send_keepalive(free_kb, self._ka_flags(), self.mod_state)
            if self._boot_status_left > 0:
                self.link.send_status(ST_BOOT_OK)
                self._boot_status_left -= 1
            # faster beacon while A is still attaching
            await asyncio.sleep_ms(400 if self._boot_status_left > 0 else 2000)

    async def command_loop(self):
        while self.running:
            if self.wdt:
                try:
                    self.wdt.feed()
                except Exception:
                    pass
            n = 0
            for _ in range(4):
                item = self.link.poll_tlv()
                if not item:
                    break
                cmd, payload = item
                # copy scalar fields before next poll overwrites memoryview
                pl = bytes(payload) if payload else b""
                await self.handle_command(cmd, pl)
                n += 1
            await asyncio.sleep_ms(8 if n else 20)

    async def _mod_off(self):
        if self._manager:
            try:
                await self._manager.stop()
            except Exception:
                pass
        self._manager = None
        self._manager_mod = MOD_NONE
        self.active_mod = MOD_NONE
        self.mod_state = KA_STATE_IDLE
        self._purge_rf_modules()
        gc.collect(); gc.collect()

    def _purge_rf_modules(self):
        try:
            import sys
            for n in tuple(sys.modules.keys()):
                if n.startswith("radio.managers.") or n.startswith("radio.drivers."):
                    sys.modules.pop(n, None)
        except Exception:
            pass
        try:
            from lib import mempool
            mempool.drop_rf()
        except Exception:
            pass

    async def handle_command(self, cmd, payload):
        if cmd == CMD_RF_ON:
            self.rf_rail = True
            self.link.send_status(ST_RF_ON_DONE)
        elif cmd == CMD_RF_OFF:
            await self._mod_off()
            self.rf_rail = False
            self.link.send_status(ST_RF_OFF_DONE)
        elif cmd == CMD_PREPARE_SHUTDOWN:
            await self._mod_off()
            self.rf_rail = False
            self.link.send_status(ST_PREPARE_DONE)
        elif cmd == CMD_STOP_ALL:
            await self._mod_off()
            self.link.send_status(ST_STOPPED)
        elif cmd == CMD_QUERY:
            code = ST_RF_ON_DONE if self.rf_rail else ST_RF_OFF_DONE
            self.link.send_status(code, self.active_mod)
        elif cmd == CMD_REBOOT:
            await asyncio.sleep_ms(30)
            machine.reset()
        elif cmd == CMD_SHUTDOWN:
            await self._mod_off()
            self.rf_rail = False
            self.link.send_status(ST_READY_POWER_OFF)
            await asyncio.sleep_ms(40)
            self.running = False
            try:
                machine.deepsleep()
            except Exception:
                pass
        elif cmd in (CMD_SUBGHZ, CMD_NRF, CMD_WIFI_AUD, CMD_BLE):
            await self._handle_module(cmd, payload)
        else:
            self.link.send_status(ST_UNSUPPORTED)

    async def _handle_module(self, mod_id, payload):
        op = payload[0] if payload else OP_CONFIG
        if op == OP_STOP:
            if self.active_mod == mod_id:
                await self._mod_off()
            else:
                self.link.send_status(ST_MOD_OFF, mod_id)
            return
        if self.active_mod not in (MOD_NONE, mod_id):
            await self._mod_off()
        self.rf_rail = True
        try:
            mgr = self._manager
            if mgr is None or self._manager_mod != mod_id:
                if mod_id == MOD_SUBGHZ:
                    from radio.managers.subghz import Manager
                elif mod_id == MOD_NRF:
                    from radio.managers.nrf24 import Manager
                elif mod_id == MOD_WIFI:
                    from radio.managers.wifi_audit import Manager
                elif mod_id == MOD_BLE:
                    from radio.managers.ble import Manager
                else:
                    self.link.send_status(ST_UNSUPPORTED)
                    return
                mgr = Manager(self.link)
                self._manager = mgr
                self._manager_mod = mod_id
            self.active_mod = mod_id
            self.mod_state = await mgr.handle(payload)
        except Exception:
            self.link.send_status(ST_ERR, ERR_HW)
            await self._mod_off()

    async def run(self):
        try:
            machine.freq(Config.CPU_FREQ_HZ)
        except Exception:
            pass
        # 3x BOOT_OK — A may open UART slightly late
        for _ in range(3):
            self.link.send_status(ST_BOOT_OK)
            await asyncio.sleep_ms(60)
        tasks = (
            asyncio.create_task(self.keepalive()),
            asyncio.create_task(self.command_loop()),
        )
        try:
            while self.running:
                if self.wdt:
                    self.wdt.feed()
                gc.collect()
                await asyncio.sleep_ms(2000)
        finally:
            for t in tasks:
                try:
                    t.cancel()
                except Exception:
                    pass
            self.link.close()


if __name__ == "__main__":
    try:
        asyncio.run(ColibryNodeB().run())
    except Exception:
        machine.reset()
