# config.py — Node B core (no RF drivers yet)
from micropython import const


class Config:
    CPU_FREQ_HZ = const(160_000_000)

    LINK_UART_ID = const(1)
    LINK_UART_TX_PIN = const(20)   # crossed vs Node A
    LINK_UART_RX_PIN = const(21)
    LINK_UART_BAUD = const(921600)

    # CC1101 pins reserved for later phase (not used in core)
    CC_SCK = const(4)
    CC_MOSI = const(3)
    CC_MISO = const(2)
    CC_CSN = const(7)
    CC_GDO0 = const(6)

    WDT_TIMEOUT_MS = const(6000)

    # NRF24L01+ pins (override per wiring)
    NRF_SCK = const(4)
    NRF_MOSI = const(3)
    NRF_MISO = const(2)
    NRF_CSN = const(8)
    NRF_CE = const(9)
    NRF_IRQ = const(10)
