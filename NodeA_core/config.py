# config.py — Node A core (ESP32-C3 SuperMini)
from micropython import const


class Config:
    CPU_FREQ_HZ = const(160_000_000)

    OLED_SCL_PIN = const(9)
    OLED_SDA_PIN = const(8)
    OLED_I2C_FREQ_HZ = const(100_000)
    OLED_WIDTH = const(128)
    OLED_HEIGHT = const(64)
    OLED_STATUS_HEIGHT = const(12)

    BUTTON_UP_PIN = const(2)
    BUTTON_DOWN_PIN = const(3)
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

    # Future UART to Node B (not used in core phase)
    LINK_UART_ID = const(1)
    LINK_UART_TX_PIN = const(21)
    LINK_UART_RX_PIN = const(20)
    LINK_UART_BAUD = const(921600)

    # Power MOSFETs on Node A
    # Q1 SELF_LATCH — holds Node A after reed switch
    # Q2 NODE_B_POWER — supplies Node B
    # Q3 RF_POWER — supplies RF modules (CC1101 etc.) later
    SELF_LATCH_PIN = const(7)
    NODE_B_POWER_PIN = const(10)
    RF_POWER_PIN = const(5)

    NODE_B_BOOT_WAIT_MS = const(1200)
    # After UART READY_POWER_OFF (or RF cut): hold Q2 for 2 s then cut (docs)
    NODE_B_CUT_DELAY_MS = const(2000)

    # 15 s: STREAM + concurrent HTTP can delay the feed task
    WDT_TIMEOUT_MS = const(15000)
    WDT_FEED_MS = const(1000)

    BATTERY_ADC_PIN = const(6)
    BATTERY_DIVIDER_TOP_OHM = const(100_000)
    BATTERY_DIVIDER_BOTTOM_OHM = const(47_000)
    BATTERY_ADC_REF_MV = const(3300)
    BATTERY_CALIBRATION_PERMILLE = const(1000)
    BATTERY_EMPTY_MV = const(3300)
    BATTERY_FULL_MV = const(4200)
    BATTERY_LOW_PERCENT = const(25)
    BATTERY_CRITICAL_PERCENT = const(10)
    BATTERY_SAMPLE_COUNT = const(8)
    BATTERY_POLL_MS = const(30000)
    BATTERY_ALERT_REPEAT_MS = const(300000)
    BATTERY_HISTORY_DEPTH = const(120)
    BATTERY_TREND_DELTA_MV = const(8)

    INPUT_QUEUE_DEPTH = const(8)
    IDLE_GC_PERIOD_MS = const(5000)
    MODULE_SETTLE_MS = const(0)
