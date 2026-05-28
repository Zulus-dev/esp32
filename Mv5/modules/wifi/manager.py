# modules/wifi/manager.py - lazy WiFi + web server control module
import asyncio

import network
import ujson as json
import gc


async def wifi_on(core):
    gc_collect()
    core.oled.show_message("WiFi", "Starting AP...")
    wlan = network.WLAN(network.AP_IF)
    wlan.active(True)
    cfg = _load_settings()
    password = cfg.get("password", "")
    if password:
        wlan.config(
            essid=cfg.get("essid", "ColibryOS"),
            password=password,
            hidden=cfg.get("hidden", False),
        )
    else:
        wlan.config(essid=cfg.get("essid", "ColibryOS"), hidden=cfg.get("hidden", False))

    ip = wlan.ifconfig()[0]
    core.update_status(wifi=True)
    core.oled.show_message("Web Server", "Starting...\n" + ip)

    server = core.services.get("web")
    if server is None:
        from web.server import AsyncServer
        server = AsyncServer(core=core)
        core.services["web"] = server
    server.core = core
    if server.api is not None:
        server.api.set_core(core)
    await server.start()

    core.update_status(wifi=True, server=True)
    core.oled.show_message("WiFi Ready", ip + "\nHTTP server ON")
    await asyncio.sleep_ms(100)
    return True


async def wifi_off(core):
    server = core.services.get("web")
    if server is not None:
        await server.stop()
        core.services.pop("web", None)
        server = None

    wlan = network.WLAN(network.AP_IF)
    try:
        wlan.disconnect()
    except Exception:
        pass
    wlan.active(False)
    wlan = None
    core.purge_modules(("web",))
    gc_collect()
    await asyncio.sleep_ms(100)
    gc_collect()
    core.update_status(wifi=False, server=False)
    core.oled.show_message("WiFi", "Disabled\nFree: %d KB" % (core.free_mem() // 1024))
    return True


def _load_settings():
    try:
        with open("wifi_settings.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"essid": "ColibryOS", "password": "", "hidden": False}


def gc_collect():
    gc.collect()
    gc.collect()
