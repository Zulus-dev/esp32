# modules/tools/manager.py
import asyncio


async def i2c_scan(core):
    core.oled.show_message("I2C Scan", "Scanning...")
    await asyncio.sleep_ms(150)
    devices = core.oled.i2c.scan()
    if devices:
        msg = "Found:\n" + ", ".join(hex(addr) for addr in devices)
    else:
        msg = "No devices"
    core.oled.show_message("I2C Result", msg)
    return True
