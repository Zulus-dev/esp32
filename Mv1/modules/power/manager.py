import asyncio
from hardware.radio_service import RadioService
from config import Config


class PowerManager:
    STATE_B_OFF = "B_OFF"
    STATE_B_BOOTING = "B_BOOTING"
    STATE_B_ONLINE = "B_ONLINE"
    STATE_RF_OFF = "RF_OFF"
    STATE_RF_ON = "RF_ON"
    STATE_SHUTDOWN_PENDING = "SHUTDOWN_PENDING"
    STATE_FAULT = "FAULT"

    def __init__(self, core):
        self.core = core
        self._lock = asyncio.Lock()
        self.state = self.STATE_B_OFF
        self.last_event = "init"
        self.last_error = ""
        self.recover_attempts = 0

    def _radio(self):
        radio = self.core.services.get("radio")
        if radio is None:
            radio = RadioService(self.core)
            self.core.services["radio"] = radio
        return radio

    async def _ensure_b_online_locked(self):
        radio = self._radio()
        self.state = self.STATE_B_BOOTING
        await radio.start()
        ok = await radio.wait_status(("B_BOOT_OK",), timeout_ms=5000)
        if not ok:
            self.state = self.STATE_FAULT
            self.last_error = "boot_timeout"
            raise ValueError("node_b_boot_timeout")
        self.state = self.STATE_B_ONLINE
        self.last_event = "ensure_b_online"
        return radio

    async def ensure_b_online(self):
        async with self._lock:
            return await self._ensure_b_online_locked()

    async def ensure_rf_on(self):
        async with self._lock:
            radio = await self._ensure_b_online_locked()
            radio.command(RadioService.CMD_RF_ON)
            ok = await radio.wait_status(("RF_ON_DONE",), timeout_ms=Config.RADIO_ACK_TIMEOUT_MS)
            if not ok:
                self.state = self.STATE_FAULT
                self.last_error = "rf_on_timeout"
                raise ValueError("rf_on_timeout")
            self.state = self.STATE_RF_ON
            self.last_event = "ensure_rf_on"
            return radio

    async def ensure_rf_off(self):
        async with self._lock:
            radio = self._radio()
            if not radio.powered:
                self.state = self.STATE_B_OFF
                return True
            radio.command(RadioService.CMD_RF_OFF)
            ok = await radio.wait_status(("RF_OFF_DONE",), timeout_ms=Config.RADIO_ACK_TIMEOUT_MS)
            if ok:
                self.state = self.STATE_RF_OFF
                self.last_event = "ensure_rf_off"
            else:
                self.last_error = "rf_off_timeout"
            return ok

    async def graceful_shutdown_all(self):
        async with self._lock:
            radio = self._radio()
            if not radio.powered:
                self.state = self.STATE_B_OFF
                return True
            self.state = self.STATE_SHUTDOWN_PENDING
            radio.command(RadioService.CMD_PREPARE_SHUTDOWN)
            await radio.wait_status(("PREPARE_SHUTDOWN_DONE",), timeout_ms=Config.RADIO_ACK_TIMEOUT_MS)
            radio.command(RadioService.CMD_RF_OFF)
            await radio.wait_status(("RF_OFF_DONE",), timeout_ms=Config.RADIO_ACK_TIMEOUT_MS)
            await radio.shutdown()
            self.core.services.pop("radio", None)
            self.state = self.STATE_B_OFF
            self.last_event = "graceful_shutdown_all"
            return True

    async def emergency_power_cut(self, reason=""):
        async with self._lock:
            radio = self.core.services.get("radio")
            if radio:
                await radio.stop(hard_power=True)
                self.core.services.pop("radio", None)
            self.state = self.STATE_FAULT
            self.last_error = reason or "emergency_cut"

    def snapshot(self):
        return {"state": self.state, "last_event": self.last_event, "last_error": self.last_error, "recover_attempts": self.recover_attempts}


def get_power_manager(core):
    mgr = core.services.get("power")
    if mgr is None:
        mgr = PowerManager(core)
        core.services["power"] = mgr
    return mgr
