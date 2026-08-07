# protocol.py — A↔B binary only. SLIP+TLV+CRC16-CCITT. Identical on both nodes.
# No strings on the wire. Payload = fixed-width codes / ints.
from micropython import const

# --- control (msg_type) ---
CMD_STOP_ALL = const(0x08)
CMD_RF_ON = const(0x14)
CMD_RF_OFF = const(0x15)
CMD_PREPARE_SHUTDOWN = const(0x16)
CMD_QUERY = const(0x17)
CMD_REBOOT = const(0xFE)
CMD_SHUTDOWN = const(0xFF)

# --- module select (msg_type = mod id) ---
CMD_SUBGHZ = const(0x01)
CMD_NRF = const(0x20)
CMD_WIFI_AUD = const(0x30)
CMD_BLE = const(0x40)

# --- module op byte[0] ---
OP_CONFIG = const(0x01)
OP_START = const(0x02)
OP_STOP = const(0x04)
OP_ONCE = const(0x05)

SUB_MODE_SCAN = const(0x01)
SUB_MODE_SNIFF = const(0x02)

# --- TLV types ---
TLV_RSSI = const(0x10)       # freq_u32_be + rssi_i8
TLV_PKT = const(0x11)        # opaque RF payload (driver)
TLV_STATUS = const(0x21)     # code_u8 + arg_u16_be
TLV_KEEPALIVE = const(0xFF)  # free_kb_u16_be + flags_u8 + state_u8

# --- ST codes (STATUS payload[0]) ---
ST_BOOT_OK = const(0x01)
ST_RF_ON_DONE = const(0x02)
ST_RF_OFF_DONE = const(0x03)
ST_PREPARE_DONE = const(0x04)
ST_READY_POWER_OFF = const(0x05)
ST_STOPPED = const(0x08)
ST_UNSUPPORTED = const(0x0A)
ST_ERR = const(0x0B)
ST_MOD_ON = const(0x0C)
ST_MOD_OFF = const(0x0D)

# --- keepalive ---
KA_FLAG_RF_RAIL = const(0x01)
KA_FLAG_MOD_ACTIVE = const(0x02)
KA_STATE_IDLE = const(0x00)
KA_STATE_SCAN = const(0x01)
KA_STATE_SNIFF = const(0x02)

# --- mod ids (logic) ---
MOD_NONE = const(0x00)
MOD_SUBGHZ = const(0x01)
MOD_NRF = const(0x20)
MOD_WIFI = const(0x30)
MOD_BLE = const(0x40)

# --- host-side error codes (not on wire; int only, no str) ---
ERR_NONE = const(0)
ERR_LINK_TIMEOUT = const(1)
ERR_ACK_TIMEOUT = const(2)
ERR_NOT_POWERED = const(3)
ERR_FAULT = const(4)
