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
TLV_PKT = const(0x11)          # mod_u8 + freq/channel_u32_be + rssi_i8 + len_u8 + raw<=48
TLV_NRF_EVENT = const(0x12)    # event_u8 + pipe_u8 + channel_u8 + rssi_i8 + len_u8 + raw<=32
TLV_WIFI_FRAME = const(0x13)   # frame_type_u8 + channel_u8 + rssi_i8 + len_u8 + raw<=48
TLV_BLE_ADV = const(0x14)      # addr_type_u8 + addr6 + adv_type_u8 + rssi_i8 + len_u8 + data<=31
TLV_SCAN_RESULT = const(0x15)  # mod_u8 + channel_u16_be + rssi_i8 + len_u8 + compact data<=48
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

# --- Phone ↔ Node A binary (HTTP body / radio status). Not on UART.
# Status response: WEB_MAGIC + fixed header + optional event records.
WEB_MAGIC = const(0xC01B)
WEB_VER = const(1)

# action codes for /api/radio/cmd binary body
WA_NODE_B_ON = const(0x01)
WA_NODE_B_OFF = const(0x02)
WA_MOD_ON = const(0x03)
WA_MOD_OFF = const(0x04)
WA_SCAN = const(0x05)
WA_LISTEN = const(0x06)
WA_CAPTURE = const(0x07)
WA_RSSI_ONCE = const(0x08)
WA_REPLAY = const(0x09)
WA_STOP = const(0x0A)
WA_HID_SNIFF = const(0x0B)
WA_HONEYPOT = const(0x0C)
WA_WIFI_SCAN = const(0x0D)
WA_BLE_SCAN = const(0x0E)
WA_LOG_SAVE = const(0x0F)
WA_LOG_CLEAR = const(0x10)
WA_TX_RAW = const(0x11)  # phone→A: raw OOK bytes after 8-byte header

# event kind (same as link ring)
EV_RSSI = const(1)
EV_PKT = const(2)
EV_ST = const(3)
EV_NRF = const(4)
EV_WIFI = const(5)
EV_BLE = const(6)
EV_SCAN = const(7)

# Fixed status header size (bytes) before events
# magic_u16 ver_u8 flags_u8
# online boot pending powered rf_on err mod_id mod_state letter_u8
# rx_frames_u32 crc_fail_u16 raw_rx_u32 ka_free_u16 ka_age_u16 st_age_u16
# ka_state ka_flags last_rssi_i8 pad
# freq_u32 event_count_u16 n_events_u8 pad
WEB_STATUS_HDR = const(40)
WEB_EVENT_SIZE = const(16)  # compact event for wire: kind,dlen,a_u32,b_i16,c_u16,t_u16  = 1+1+4+2+2+2=12 → pad to 16 with 4 data

