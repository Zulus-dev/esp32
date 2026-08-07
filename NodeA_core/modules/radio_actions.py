# OLED radio actions — lazy commands through RadioHub, short status only.
async def _cmd(core, action, module="subghz"):
    from hardware.radio_hub import get_radio_hub
    hub = get_radio_hub(core)
    await hub.command({"action": action, "module": module})
    s = hub.snapshot(); m=s["module"]; p=s["power"]
    core.oled.show_message("Radio", "%s %s\nRF%d e%d" % (m["name"], m["state"], 1 if p["rf_on"] else 0, p.get("err",0)))
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
