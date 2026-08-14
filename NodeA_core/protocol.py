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
OP_CONFIG = const(0x01)  # payload: op + mode + packed params
OP_START = const(0x02)
OP_SET_REG = const(0x03)
OP_STOP = const(0x04)
OP_ONCE = const(0x05)
OP_TX_RAW = const(0x06)
OP_REPLAY = const(0x07)
OP_DUMP = const(0x08)  # stream pending NodeB capture; A pipes chunks

SUB_MODE_SCAN = const(0x01)
SUB_MODE_SNIFF = const(0x02)
SUB_MODE_RSSI = const(0x03)
SUB_MODE_TX = const(0x04)

NRF_MODE_HID = const(0x01)
NRF_MODE_HONEYPOT = const(0x02)
NRF_MODE_RX = const(0x03)
NRF_MODE_TX = const(0x04)

WIFI_MODE_SCAN = const(0x01)
WIFI_MODE_MON = const(0x02)
WIFI_MODE_DEAUTH = const(0x03)
WIFI_MODE_BEACON = const(0x04)

BLE_MODE_SCAN = const(0x01)
BLE_MODE_ACTIVE_SCAN = const(0x02)
BLE_MODE_ADV = const(0x03)

# --- TLV types / binary layouts ---
TLV_RSSI = const(0x10)         # freq_u32_be + rssi_i8
TLV_PKT = const(0x11)          # preview only: mod_u8 + freq_u32be + rssi_i8 + len_u8 (no RAW)
TLV_NRF_EVENT = const(0x12)    # event_u8 + pipe_u8 + channel_u8 + rssi_i8 + len_u8 + raw<=32
TLV_WIFI_FRAME = const(0x13)   # frame_type_u8 + channel_u8 + rssi_i8 + len_u8 + raw<=48
TLV_BLE_ADV = const(0x14)      # addr_type_u8 + addr6 + adv_type_u8 + rssi_i8 + len_u8 + data<=31
TLV_SCAN_RESULT = const(0x15)  # mod_u8 + channel_u16_be + rssi_i8 + len_u8 + compact data<=48
# Full capture session (complete-then-chunked). SLIP MAX_PAYLOAD=64.
# META: mod_u8 + freq_u32be + rssi_i8 + total_len_u16be + nbits_u16be + flags_u8 + slot_u8
# DATA: seq_u8 + raw[≤55]
# END:  total_len_u16be + nbits_u16be + slot_u8
TLV_STREAM_META = const(0x30)
TLV_STREAM_DATA = const(0x31)
TLV_STREAM_END = const(0x32)
TLV_STATUS = const(0x21)       # code_u8 + arg_u16_be
TLV_KEEPALIVE = const(0xFF)    # free_kb_u16_be + flags_u8 + state_u8

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
ST_DONE = const(0x0E)

# --- keepalive ---
KA_FLAG_RF_RAIL = const(0x01)
KA_FLAG_MOD_ACTIVE = const(0x02)
KA_STATE_IDLE = const(0x00)
KA_STATE_SCAN = const(0x01)
KA_STATE_SNIFF = const(0x02)
KA_STATE_TX = const(0x03)
KA_STATE_HONEYPOT = const(0x04)

# --- mod ids (logic) ---
MOD_NONE = const(0x00)
MOD_SUBGHZ = const(0x01)
MOD_NRF = const(0x20)
MOD_WIFI = const(0x30)
MOD_BLE = const(0x40)

# --- int errors ---
ERR_NONE = const(0)
ERR_LINK_TIMEOUT = const(1)
ERR_ACK_TIMEOUT = const(2)
ERR_NOT_POWERED = const(3)
ERR_FAULT = const(4)
ERR_NO_DRIVER = const(5)
ERR_BAD_ARG = const(6)
ERR_BUSY = const(7)
ERR_HW = const(8)
ERR_UNSUPPORTED = const(9)

# --- capture contract (v2.1) ---
# Full RAW lives on NodeB only (CAPTURE_SLOTS × CAPTURE_SLOT_SIZE).
# Listen: RECORD → notify (STREAM_META+END, no DATA) → capture_seq++ on A.
# Phone: on seq change → GET /capture → OP_DUMP → DATA chunks (actual_len only).
# After successful OP_DUMP stream NodeB FREEs the slot.
# NodeA is a pure translator: short STREAM chunks, no full-frame slab.
CAPTURE_SLOT_SIZE = const(8192)   # NodeB ping-pong slot
CAPTURE_SLOTS = const(2)          # ping-pong on NodeB only
STREAM_DATA_MAX = const(55)       # 1 seq + ≤55 data inside SLIP payload 64
PREVIEW_MAX = const(16)           # TLV_PKT UI spike only (not RAW path)
# NodeA pipe ring: pending STREAM_DATA only (not full frame)
STREAM_PIPE_CHUNKS = const(32)    # 32 × (1+55) ≈ 1.8 KB — OP_DUMP burst (integrity)
