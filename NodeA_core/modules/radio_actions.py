# OLED radio actions — binary WA_* codes only.
async def _cmd(core, action, module="subghz"):
    from hardware.radio_hub import get_radio_hub
    from protocol import MOD_SUBGHZ, MOD_NRF, MOD_WIFI, MOD_BLE
    hub = get_radio_hub(core)
    mod_id = {"subghz": MOD_SUBGHZ, "nrf": MOD_NRF, "wifi": MOD_WIFI, "ble": MOD_BLE}.get(module, MOD_SUBGHZ)
    await hub.command_bin(action, mod_id)
    letter = hub._letter(hub._pwr(), hub._pwr().rf_powered)
    core.oled.show_message("Radio", "%s\nRF %s e%d" % (module, letter, hub.err))
    return True


async def sub_on(core):
    from protocol_web import WA_MOD_ON
    return await _cmd(core, WA_MOD_ON, "subghz")


async def sub_scan(core):
    from protocol_web import WA_SCAN
    return await _cmd(core, WA_SCAN, "subghz")


async def sub_sniff(core):
    from protocol_web import WA_LISTEN
    return await _cmd(core, WA_LISTEN, "subghz")


async def sub_rssi(core):
    from protocol_web import WA_RSSI_ONCE
    return await _cmd(core, WA_RSSI_ONCE, "subghz")


async def sub_replay(core):
    from protocol_web import WA_REPLAY
    return await _cmd(core, WA_REPLAY, "subghz")


async def sub_off(core):
    from protocol_web import WA_MOD_OFF
    return await _cmd(core, WA_MOD_OFF, "subghz")


async def nrf_on(core):
    from protocol_web import WA_MOD_ON
    return await _cmd(core, WA_MOD_ON, "nrf")


async def nrf_hid(core):
    from protocol_web import WA_HID_SNIFF
    return await _cmd(core, WA_HID_SNIFF, "nrf")


async def nrf_honeypot(core):
    from protocol_web import WA_HONEYPOT
    return await _cmd(core, WA_HONEYPOT, "nrf")


async def nrf_off(core):
    from protocol_web import WA_MOD_OFF
    return await _cmd(core, WA_MOD_OFF, "nrf")


async def wifi_scan(core):
    from protocol_web import WA_WIFI_SCAN
    return await _cmd(core, WA_WIFI_SCAN, "wifi")


async def wifi_off(core):
    from protocol_web import WA_MOD_OFF
    return await _cmd(core, WA_MOD_OFF, "wifi")


async def ble_scan(core):
    from protocol_web import WA_BLE_SCAN
    return await _cmd(core, WA_BLE_SCAN, "ble")


async def ble_off(core):
    from protocol_web import WA_MOD_OFF
    return await _cmd(core, WA_MOD_OFF, "ble")
