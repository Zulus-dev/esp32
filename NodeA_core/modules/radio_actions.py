# OLED radio actions — binary command path, minimal status for display.
async def _cmd(core, action, module="subghz"):
    from hardware.radio_hub import get_radio_hub
    from protocol import MOD_SUBGHZ, MOD_NRF, MOD_WIFI, MOD_BLE
    hub = get_radio_hub(core)
    mod_id = {"subghz": MOD_SUBGHZ, "nrf": MOD_NRF, "wifi": MOD_WIFI, "ble": MOD_BLE}.get(module, MOD_SUBGHZ)
    await hub.command_bin(action, mod_id)
    letter = hub._letter(hub._pwr(), hub._pwr().rf_powered)
    core.oled.show_message("Radio", "%s\nRF %s e%d" % (module, letter, hub.err))
    return True
async def sub_on(core): return await _cmd(core,"module_on","subghz")
async def sub_scan(core): return await _cmd(core,"scan","subghz")
async def sub_sniff(core): return await _cmd(core,"sniff","subghz")
async def sub_rssi(core): return await _cmd(core,"rssi_once","subghz")
async def sub_replay(core): return await _cmd(core,"replay","subghz")
async def sub_off(core): return await _cmd(core,"module_off","subghz")
async def nrf_on(core): return await _cmd(core,"module_on","nrf")
async def nrf_hid(core): return await _cmd(core,"hid_sniff","nrf")
async def nrf_honeypot(core): return await _cmd(core,"honeypot","nrf")
async def nrf_off(core): return await _cmd(core,"module_off","nrf")
async def wifi_scan(core): return await _cmd(core,"wifi_scan","wifi")
async def wifi_off(core): return await _cmd(core,"module_off","wifi")
async def ble_scan(core): return await _cmd(core,"ble_scan","ble")
async def ble_off(core): return await _cmd(core,"module_off","ble")
