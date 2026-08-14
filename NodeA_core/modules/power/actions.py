# modules/power/actions.py — OLED power (binary hub fields, no dict snapshot)
import asyncio
import machine


async def node_b_on(core):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    core.oled.show_message("Node B", "ON...")
    await hub.node_b_on()
    pwr = hub._pwr()
    online = 1 if (hub.link and hub.link.online()) else 0
    core.oled.show_message(
        "Node B",
        "P:%d O:%d e%d" % (1 if pwr.powered else 0, online, hub.err),
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
    pwr = hub._pwr()
    letter = hub._letter(pwr, pwr.rf_powered)
    core.oled.show_message(
        "RF",
        "%s %d e%d" % (letter, 1 if pwr.rf_powered else 0, hub.err),
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
    pwr = hub._pwr()
    online = 1 if (hub.link and hub.link.online()) else 0
    letter = hub._letter(pwr, pwr.rf_powered)
    names = {0: "none", 1: "subghz", 0x20: "nrf", 0x30: "wifi", 0x40: "ble"}
    core.oled.show_message(
        "Status",
        "P%d O%d RF%d\n%s %s e%d" % (
            1 if pwr.powered else 0,
            online,
            1 if pwr.rf_powered else 0,
            names.get(hub.active_mod, "none"),
            letter,
            hub.err,
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
