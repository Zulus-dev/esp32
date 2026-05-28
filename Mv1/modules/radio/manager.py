from modules.power.manager import get_power_manager
# modules/radio/manager.py - OLED menu actions for Node B over UART
import asyncio


async def power_on(core):
    pwr = get_power_manager(core)
    core.oled.show_message("Node B", "Powering on...")
    await pwr.ensure_b_online()
    radio = core.services.get("radio")
    core.oled.show_message("Node B", "UART ready\nWait keepalive")
    await asyncio.sleep_ms(300)
    return True


async def status(core):
    radio = core.services.get("radio")
    if radio is None:
        core.oled.show_message("Node B", "Power: OFF")
    else:
        snap = radio.snapshot()
        core.oled.show_message(
            "Node B",
            "P:%s O:%s\n%s" % (
                "ON" if snap.get("powered") else "OFF",
                "YES" if snap.get("online") else "NO",
                snap.get("last_status", ""),
            ),
        )
    await asyncio.sleep_ms(1200)
    return False


async def subghz_scan(core):
    pwr = get_power_manager(core)
    radio = await pwr.ensure_rf_on()
    radio.command(0x0A, _subghz_payload(freq=433.92, mod="OOK"))
    core.oled.show_message("SubGHz", "Scan 433.920\nOOK started")
    await asyncio.sleep_ms(600)
    return True


async def nrf_honeypot(core):
    pwr = get_power_manager(core)
    radio = await pwr.ensure_rf_on()
    radio.command(0x06)
    core.oled.show_message("NRF24", "HID honeypot\nstarted")
    await asyncio.sleep_ms(600)
    return True


async def ble_sniff(core):
    radio = await _ensure(core)
    radio.command(0x07)
    core.oled.show_message("BLE", "ADV sniff\nstarted")
    await asyncio.sleep_ms(600)
    return True


async def stop_all(core):
    radio = core.services.get("radio")
    if radio is not None:
        radio.command(0x08)
    core.oled.show_message("Node B", "STOP sent")
    await asyncio.sleep_ms(500)
    return True


async def power_off(core):
    pwr = get_power_manager(core)
    core.oled.show_message("Node B", "Shutdown...")
    await pwr.graceful_shutdown_all()
    core.oled.show_message("Node B", "Power OFF")
    await asyncio.sleep_ms(500)
    return False


async def _ensure(core):
    radio = core.services.get("radio")
    if radio is None:
        from hardware.radio_service import RadioService
        radio = RadioService(core)
        core.services["radio"] = radio
        await radio.start()
    return radio



async def recent_packets(core):
    radio = core.services.get("radio")
    if radio is None or not radio.packets:
        core.oled.show_message("SubGHz", "No packets")
    else:
        pkt = radio.packets[-1]
        core.oled.show_message("SubGHz Packet", "%.3f %sdBm\n%s" % (pkt.get("freq", 0), pkt.get("rssi_dbm", 0), pkt.get("data_hex", "")[:16]))
    await asyncio.sleep_ms(1200)
    return False


def _subghz_payload(freq=433.92, mod="OOK", baud=4800):
    return ("freq=%.3f;mod=%s;baud=%d" % (freq, mod, baud)).encode()


def _pack_freq(freq):
    khz = int(freq * 1000)
    return bytes(((khz >> 24) & 0xFF, (khz >> 16) & 0xFF, (khz >> 8) & 0xFF, khz & 0xFF))
