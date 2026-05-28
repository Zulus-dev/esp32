# protocol.py - compact TLV command map for Colibry Node A <-> Node B
from micropython import const

# Master -> Slave commands. Values 0x01..0x08 keep backward compatibility.
CMD_SET_FREQ           = const(0x01)
CMD_START_SUBGHZ_SCAN  = const(0x02)  # legacy alias for CMD_START_SCAN
CMD_REPLAY_SUBGHZ      = const(0x03)  # legacy alias for CMD_REPLAY
CMD_START_DEAUTH       = const(0x04)
CMD_BEACON_SPAM        = const(0x05)
CMD_NRF_HONEYPOT       = const(0x06)
CMD_BLE_SNIFF          = const(0x07)
CMD_STOP_ALL           = const(0x08)

# Phase-1 Sub-GHz template commands.
CMD_SET_MODULATION     = const(0x09)
CMD_START_SCAN         = const(0x0A)
CMD_START_SNIFF        = const(0x0B)
CMD_REPLAY             = const(0x0C)
CMD_RAW_TX             = const(0x0D)
CMD_SET_POWER          = const(0x0E)
CMD_SPECTRUM_SWEEP     = const(0x0F)
CMD_JAMMER             = const(0x40)  # gated by UI/manager confirmation; disabled by default

CMD_REBOOT             = const(0xFE)
CMD_SHUTDOWN           = const(0xFF)

# Slave -> Master telemetry.
TLV_KEEPALIVE          = const(0xFF)
TLV_SUBGHZ_PACKET      = const(0x10)  # freq_khz(4)|rssi_i8|len(1)|ts_ms(4)|mod(1)|data
TLV_NRF_HID_EVENT      = const(0x11)
TLV_WIFI_FRAME         = const(0x12)
TLV_BLE_ADV            = const(0x13)
TLV_RSSI_REPORT        = const(0x20)  # freq_khz(4)|rssi_i8|ts_ms(4)
TLV_STATUS             = const(0x21)
TLV_LOG                = const(0xF0)
