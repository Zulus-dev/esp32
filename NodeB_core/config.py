# config.py — Node B core
# UART pins MUST match M_S_v1.3 working map: both nodes TX=21 RX=20
# (physical wires cross A↔B). Do NOT "software-cross" here.
from micropython import const


class Config:
    CPU_FREQ_HZ = const(160_000_000)

    LINK_UART_ID = const(1)
    LINK_UART_TX_PIN = const(21)  # same as M_S_v1.3 NodeB UART_TX
    LINK_UART_RX_PIN = const(20)  # same as M_S_v1.3 NodeB UART_RX
    LINK_UART_BAUD = const(921600)

    # CC1101 (from M_S_v1.3 NodeB — adjust if board differs)
    CC_SCK = const(3)
    CC_MOSI = const(1)
    CC_MISO = const(2)
    CC_CSN = const(0)
    CC_GDO0 = const(8)

    WDT_TIMEOUT_MS = const(12000)

    # NRF24L01+ (from M_S_v1.3)
    NRF_CE = const(9)
    NRF_CSN = const(5)
    NRF_SCK = const(4)
    NRF_MOSI = const(6)
    NRF_MISO = const(7)
    NRF_IRQ = const(10)
