# modules/wifi/manager.py — WiFi AP + HTTP server
#
# === RAM RULES ===
# 1. Перед wifi_on: drop RF workspace + link slabs, gc×2 (UART/link не рвать).
# 2. HTTP TX только через mempool web_tx / web_list / web_file.
# 3. После wifi_off: stop server, drop web slabs, purge web.* из sys.modules.
# 4. MemoryError на AP → graceful_shutdown_all + один retry.
# 5. JSON только wifi_settings.json; остальной HTTP — binary.
# 6. Не копить строки статусов / глобальные логи.
#

import asyncio
import gc
import network
import ujson as json


def _reclaim_before_wifi(core):
    """Free RF workspace only. Keep UART/link if Node B is online."""
    try:
        pwr = core.services.get("power")
        if pwr is not None:
            pwr.set_rf(False)
    except Exception:
        pass

    try:
        from lib import mempool
        mempool.ensure_rf_workspace(None)
        for n in ("link_rx", "link_tx"):
            try:
                mempool.release(n, drop=True)
            except Exception:
                pass
    except Exception:
        pass

    gc.collect()
    gc.collect()


async def wifi_on(core):
    _reclaim_before_wifi(core)
    await asyncio.sleep_ms(50)
    gc.collect()

    free_kb = 0
    try:
        free_kb = gc.mem_free() // 1024
    except Exception:
        pass
    core.oled.show_message("WiFi", "AP start\nfree~%dKB" % free_kb)

    wlan = network.WLAN(network.AP_IF)
    try:
        wlan.active(False)
    except Exception:
        pass
    await asyncio.sleep_ms(150)

    try:
        wlan.active(True)
    except MemoryError:
        try:
            from modules.power.manager import get_power_manager
            await get_power_manager(core).graceful_shutdown_all()
        except Exception:
            pass
        _reclaim_before_wifi(core)
        await asyncio.sleep_ms(200)
        gc.collect()
        gc.collect()
        try:
            wlan.active(False)
        except Exception:
            pass
        await asyncio.sleep_ms(100)
        try:
            wlan.active(True)
        except MemoryError:
            core.oled.show_message("WiFi", "OOM\nNeed reboot")
            return True

    await asyncio.sleep_ms(200)
    cfg = _load_settings()
    essid = cfg.get("essid", "ColibryOS") or "ColibryOS"
    password = cfg.get("password", "") or ""
    hidden = bool(cfg.get("hidden", False))

    try:
        wlan.config(essid=essid)
    except Exception as e:
        print("[WIFI] essid:", e)

    try:
        if password and len(password) >= 8:
            wlan.config(password=password)
            try:
                wlan.config(authmode=3)
            except Exception:
                pass
        else:
            try:
                wlan.config(authmode=0)
            except Exception:
                pass
    except Exception as e:
        print("[WIFI] auth:", e)

    try:
        wlan.config(hidden=hidden)
    except Exception:
        pass

    ip = wlan.ifconfig()[0]

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
    try:
        free_kb = gc.mem_free() // 1024
    except Exception:
        free_kb = 0
    core.oled.show_message("WiFi Ready", ip + "\nfree~%dKB" % free_kb)
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
    try:
        wlan.active(False)
    except Exception:
        pass
    try:
        wlan = network.WLAN(network.AP_IF)
        wlan.active(False)
    except Exception:
        pass
    wlan = None

    try:
        from lib import mempool
        mempool.release("web_tx", drop=True)
        mempool.release("web_list", drop=True)
        mempool.release("web_file", drop=True)
    except Exception:
        pass

    core.purge_modules(("web",))
    try:
        import sys
        for n in ("web.server", "web.api", "web"):
            sys.modules.pop(n, None)
    except Exception:
        pass
    gc.collect()
    gc.collect()
    await asyncio.sleep_ms(100)
    gc.collect()
    gc.collect()
    core.update_status(wifi=False, server=False)
    core.oled.show_message("WiFi", "OFF\nFree: %d KB" % (core.free_mem() // 1024))
    return True


def _load_settings():
    try:
        with open("wifi_settings.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"essid": "ColibryOS", "password": "", "hidden": False}
