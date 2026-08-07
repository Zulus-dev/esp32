# modules/power/actions.py — OLED power (lazy via hub)
import asyncio
import machine


async def node_b_on(core):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    core.oled.show_message("Node B", "ON...")
    ok = await hub.node_b_on()
    s = hub.snapshot()
    core.oled.show_message(
        "Node B",
        "P:%s O:%s e%d" % (
            "1" if s["power"]["powered"] else "0",
            "1" if s["link"]["online"] else "0",
            s["power"].get("err", 0),
        ),
    )
    return True


async def node_b_off(core):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    core.oled.show_message("Node B", "OFF...")
    await hub.node_b_off()
    try:
        core.reclaim_radio()
    except Exception:
        pass
    core.oled.show_message("Node B", "OFF")
    return True


async def rf_on(core):
    from hardware.radio_hub import get_radio_hub
    from protocol import MOD_SUBGHZ
    hub = get_radio_hub(core)
    core.oled.show_message("RF", "ON...")
    await hub.module_on(MOD_SUBGHZ)
    s = hub.snapshot()
    core.oled.show_message(
        "RF",
        "%s %s e%d" % (
            s["module"]["letter"],
            "1" if s["power"]["rf_on"] else "0",
            s["power"].get("err", 0),
        ),
    )
    return True


async def rf_off(core):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    await hub.module_off(rail_off=True)
    core.oled.show_message("RF", "OFF")
    return True


async def status(core):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    if hub.link:
        hub.link.poll()
    s = hub.snapshot()
    p, m, L = s["power"], s["module"], s["link"]
    core.oled.show_message(
        "Status",
        "P%d O%d RF%d\n%s %s e%d" % (
            1 if p["powered"] else 0,
            1 if L["online"] else 0,
            1 if p["rf_on"] else 0,
            m["name"],
            m["letter"],
            p.get("err", 0),
        ),
    )
    return True


async def reboot(core):
    core.oled.show_message("Reboot", "...")
    await asyncio.sleep_ms(500)
    machine.reset()


async def power_off(core):
    core.oled.show_message("Power Off", "...")
    await asyncio.sleep_ms(300)
    try:
        from hardware.radio_hub import get_radio_hub
        await get_radio_hub(core).node_b_off()
    except Exception:
        try:
            from modules.power.manager import get_power_manager
            await get_power_manager(core).node_b_off()
        except Exception:
            pass
    try:
        core.oled.display.poweroff()
    except Exception:
        pass
    await core.power_off_sequence()
