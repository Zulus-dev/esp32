# config.py - ColibryOS hardware map for ESP32-C3 SuperMini
from micropython import const


class Config:
    CPU_FREQ_HZ = const(160_000_000)

    OLED_SCL_PIN = const(9)
    OLED_SDA_PIN = const(8)
    OLED_I2C_FREQ_HZ = const(100_000)
    OLED_WIDTH = const(128)
    OLED_HEIGHT = const(64)
    OLED_STATUS_HEIGHT = const(12)
    OLED_SCL = OLED_SCL_PIN
    OLED_SDA = OLED_SDA_PIN

    BUTTON_UP_PIN = const(2)
    BUTTON_DOWN_PIN = const(3)
    BUTTON_A_PIN = BUTTON_UP_PIN
    BUTTON_B_PIN = BUTTON_DOWN_PIN
    BUTTON_DEBOUNCE_MS = const(25)
    BUTTON_DOUBLE_MS = const(300)
    BUTTON_LONG_MS = const(650)

    BUZZER_PIN = const(4)
    BUZZER_DUTY_U16 = const(22000)
    BUZZER_CLICK_HZ = const(1500)
    BUZZER_ENTER_HZ = const(2000)
    BUZZER_BACK_HZ = const(800)
    BUZZER_WARN_HZ = const(1200)
    BUZZER_ERROR_HZ = const(400)
    BUZZER_BOOT_1_HZ = const(1200)
    BUZZER_BOOT_2_HZ = const(1800)
    FREQ_CLICK = BUZZER_CLICK_HZ
    FREQ_ENTER = BUZZER_ENTER_HZ
    FREQ_BACK = BUZZER_BACK_HZ
    FREQ_WARN = BUZZER_WARN_HZ

    RADIO_UART_ID = const(1)
    RADIO_UART_TX_PIN = const(21)
    RADIO_UART_RX_PIN = const(20)
    RADIO_UART_BAUD = const(921600)

    # Node A power switches:
    # - SELF_LATCH_PIN holds Q1 high after the physical power button boots Node A.
    # - RADIO_POWER_PIN drives Q2 and supplies/removes power to Node B.
    # - RF_POWER_PIN drives Q3 and supplies/removes power to the RF modules.
    SELF_LATCH_PIN = const(7)
    RADIO_POWER_PIN = const(10)
    RF_POWER_PIN = const(5)
    RADIO_BOOT_WAIT_MS = const(1200)
    RADIO_SHUTDOWN_WAIT_MS = const(2000)
    RADIO_ACK_TIMEOUT_MS = const(3000)

    INPUT_QUEUE_DEPTH = const(8)
    IDLE_GC_PERIOD_MS = const(5000)
    MODULE_SETTLE_MS = const(0)
