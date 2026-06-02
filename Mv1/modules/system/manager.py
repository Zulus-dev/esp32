# modules/system/manager.py
import asyncio

import gc
import machine
import uos


async def sys_info(core):
    info = uos.uname()
    core.oled.show_message("System Info", "%s\n%s\nESP32-C3" % (info.sysname, info.release))
    return True


async def mem_info(core):
    gc.collect()
    core.oled.show_message("Memory", "Free: %d KB" % (gc.mem_free() // 1024))
    return True


async def battery_status(core):
    battery = core.services.get("battery")
    snap = battery.read() if battery else {"ok": False, "error": "battery_monitor_missing"}
    if not snap.get("ok"):
        core.oled.show_message("Battery", "Unavailable\n%s" % str(snap.get("error", ""))[:32])
        return True
    trend = snap.get("trend", "?")
    arrow = "^" if trend == "UP" else "v" if trend == "DOWN" else "-"
    core.oled.show_message(
        "Battery",
        "%.2f V %d%%\n%s %s\nmin %.2f max %.2f"
        % (
            snap.get("voltage", 0),
            snap.get("percent", 0),
            arrow,
            snap.get("status", "UNKNOWN"),
            snap.get("min_mv", 0) / 1000.0,
            snap.get("max_mv", 0) / 1000.0,
        ),
    )
    return True


async def reboot(core):
    core.oled.show_message("Reboot", "Restarting...")
    await asyncio.sleep_ms(800)
    machine.reset()


async def power_off(core):
    core.oled.show_message("Power Off", "Deep Sleep...")
    await asyncio.sleep_ms(1000)
    try:
        core.oled.display.poweroff()
    except Exception:
        pass
    await core.power_off_sequence()
