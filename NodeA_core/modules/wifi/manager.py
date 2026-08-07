# modules/wifi/manager.py — WiFi AP + HTTP server
#
# === RAM RULES (обязательно при любых правках) ===
# 1. Перед wifi_on: drop RF/link slabs, purge radio/link modules, gc×2.
# 2. Не создавать bytearray/list в hot-path HTTP — только mempool slabs.
# 3. Не держать ссылки на server/wlan после wifi_off (None + purge).
# 4. При MemoryError на AP: shutdown Node B → reclaim → один retry.
# 5. Запрещено: копить строки статусов, глобальные list логов, json > web_json slab.
# 6. Новые функции: либо работают в существующем slab, либо register() в mempool.
#

import asyncio
import gc
import network
import ujson as json


def _reclaim_before_wifi(core):
    """Снять radio/link полностью: task + UART + modules. Иначе free не возвращается."""
    try:
        from modules.power.manager import get_power_manager
        pwr = core.services.get("power")
        if pwr is not None:
            pwr.set_rf(False)
    except Exception:
        pass

    # RadioHub.close() stops rx_task and deinit UART
    try:
        hub = core.services.pop("radio", None)
        if hub is not None:
            if hasattr(hub, "close"):
                hub.close()
            elif hasattr(hub, "_drop_link"):
                hub._drop_link()
            hub = None
    except Exception:
        pass
    try:
        link = core.services.pop("link", None)
        if link is not None and hasattr(link, "close"):
            link.close()
        link = None
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

    try:
        core.purge_modules(
            (
                "hardware.radio_hub",
                "hardware.link_uart",
                "hardware.radio_service",
                "hardware.radio_uart",
                "modules.radio",
            )
        )
    except Exception:
        pass

    gc.collect()
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
        # Жёсткий reclaim: вырубить Node B полностью
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
    try:
        from modules.power.manager import get_power_manager
        get_power_manager(core)._push_oled()
        core.oled.set_status(server=True)
    except Exception:
        pass
    try:
        free_kb = gc.mem_free() // 1024
    except Exception:
        free_kb = 0
    core.oled.show_message("WiFi Ready", ip + "\nfree~%dKB" % free_kb)
    await asyncio.sleep_ms(80)
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
    wlan = None

    # drop sticky web slabs — вернуть кучу под radio/link
    try:
        from lib import mempool
        mempool.release("web_tx", drop=True)
        mempool.release("web_hdr", drop=True)
        mempool.release("web_json", drop=True)
    except Exception:
        pass

    core.purge_modules(("web", "hardware.radio_hub", "hardware.link_uart"))
    gc.collect()
    gc.collect()
    await asyncio.sleep_ms(80)
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
