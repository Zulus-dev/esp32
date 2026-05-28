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
    machine.deepsleep()
