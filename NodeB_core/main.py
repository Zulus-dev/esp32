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
            await asyncio.sleep_ms(2000)

    async def command_loop(self):
        while self.running:
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

    def _mod_off(self):
        self.active_mod = MOD_NONE
        self.mod_state = KA_STATE_IDLE
        gc.collect()

    async def handle_command(self, cmd, payload):
        if cmd == CMD_RF_ON:
            self.rf_rail = True
            self.link.send_status(ST_RF_ON_DONE)
        elif cmd == CMD_RF_OFF:
            self._mod_off()
            self.rf_rail = False
            self.link.send_status(ST_RF_OFF_DONE)
        elif cmd == CMD_PREPARE_SHUTDOWN:
            self._mod_off()
            self.rf_rail = False
            self.link.send_status(ST_PREPARE_DONE)
        elif cmd == CMD_STOP_ALL:
            self._mod_off()
            self.link.send_status(ST_STOPPED)
        elif cmd == CMD_QUERY:
            code = ST_RF_ON_DONE if self.rf_rail else ST_RF_OFF_DONE
            self.link.send_status(code, self.active_mod)
        elif cmd == CMD_REBOOT:
            await asyncio.sleep_ms(30)
            machine.reset()
        elif cmd == CMD_SHUTDOWN:
            self._mod_off()
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
                self._mod_off()
            self.link.send_status(ST_MOD_OFF, mod_id)
            return
        if self.active_mod not in (MOD_NONE, mod_id):
            self._mod_off()
        self.rf_rail = True
        self.active_mod = mod_id
        if op == OP_START and len(payload) > 1:
            mode = payload[1]
            self.mod_state = KA_STATE_SCAN if mode == SUB_MODE_SCAN else KA_STATE_SNIFF
        else:
            self.mod_state = KA_STATE_IDLE
        self.link.send_status(ST_MOD_ON, mod_id)

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
                await asyncio.sleep_ms(5000)
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
