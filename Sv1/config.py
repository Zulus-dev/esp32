from micropython import const

class Config:
    CPU_FREQ_HZ = const(160_000_000)

    # UART to Master
    UART_ID = const(1)
    UART_TX = const(21)
    UART_RX = const(20)
    UART_BAUD = const(921600)

    # CC1101
    CC_SCK = const(4)
    CC_MOSI = const(5)
    CC_MISO = const(6)
    CC_CSN = const(7)
    CC_GDO0 = const(8)
    SUBGHZ_DEFAULT_FREQ_MHZ = 433.920
    SUBGHZ_MAX_UART_PPS = const(18)

    # NRF24L01
    NRF_CE = const(0)
    NRF_CSN = const(1)
    NRF_SCK = const(2)
    NRF_MOSI = const(3)
    NRF_MISO = const(9)

    LED_PIN = const(10)
