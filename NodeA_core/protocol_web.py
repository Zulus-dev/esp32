# protocol_web.py — Phone ↔ Node A binary (HTTP). Not on UART.
# NodeA only. protocol.py stays identical on A and B.
from micropython import const

WEB_MAGIC = const(0xC01B)
WEB_VER = const(1)

# /api/radio/cmd action codes
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
WA_TX_RAW = const(0x11)

EV_RSSI = const(1)
EV_PKT = const(2)
EV_ST = const(3)
EV_NRF = const(4)
EV_WIFI = const(5)
EV_BLE = const(6)
EV_SCAN = const(7)

WEB_STATUS_HDR = const(40)
WEB_EVENT_SIZE = const(16)

# FS list binary magic
FS_LIST_MAGIC = const(0xF51E)
FS_TYPE_FILE = const(0)
FS_TYPE_DIR = const(1)
FS_TYPE_BACK = const(2)

# Generic binary result
BIN_OK = const(0x00)
BIN_ERR = const(0xFF)
