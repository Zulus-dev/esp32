# hardware/oled.py
from machine import SoftI2C, Pin
from lib.ssd1306 import SSD1306_I2C
from config import Config
import gc


class OLED:
    """Small SSD1306 UI driver with event-driven status bar rendering."""

    def __init__(self):
        self.status_wifi = False
        self.status_server = False
        self.status_radio = False
        self._last_status = None
        self.i2c = SoftI2C(
            scl=Pin(Config.OLED_SCL_PIN),
            sda=Pin(Config.OLED_SDA_PIN),
            freq=Config.OLED_I2C_FREQ_HZ,
        )
        self.display = SSD1306_I2C(Config.OLED_WIDTH, Config.OLED_HEIGHT, self.i2c)
        print("OLED initialized OK")

    def set_status(self, *, wifi=None, server=None, radio=None, force=False):
        if wifi is not None:
            self.status_wifi = wifi
        if server is not None:
            self.status_server = server
        if radio is not None:
            self.status_radio = radio
        state = (self.status_wifi, self.status_server, self.status_radio, gc.mem_free() // 1024)
        if force or state != self._last_status:
            self.draw_status(*state)
            self.display.show()
            self._last_status = state

    def draw_status(self, wifi_on=None, srv_on=None, radio_on=None, mem_kb=None):
        wifi_on = self.status_wifi if wifi_on is None else wifi_on
        srv_on = self.status_server if srv_on is None else srv_on
        radio_on = self.status_radio if radio_on is None else radio_on
        mem_kb = gc.mem_free() // 1024 if mem_kb is None else mem_kb
        self.display.fill_rect(0, 0, Config.OLED_WIDTH, Config.OLED_STATUS_HEIGHT, 0)
        self.display.line(0, Config.OLED_STATUS_HEIGHT, Config.OLED_WIDTH, Config.OLED_STATUS_HEIGHT, 1)
        self.display.text("W" if wifi_on else ".", 2, 2, 1)
        self.display.text("S" if srv_on else ".", 14, 2, 1)
        self.display.text("R" if radio_on else ".", 26, 2, 1)
        self.display.text(str(mem_kb) + "K", 92, 2, 1)
        self._last_status = (wifi_on, srv_on, radio_on, mem_kb)

    def show_menu(self, items, selected_idx):
        self.display.fill(0)
        self.draw_status()
        total = len(items)
        if total == 0:
            self.display.text("Empty menu", 24, 30, 1)
            self.display.show()
            return

        start_idx = selected_idx - 1
        if start_idx < 0:
            start_idx = 0
        if start_idx > max(0, total - 3):
            start_idx = max(0, total - 3)

        for row in range(3):
            idx = start_idx + row
            if idx >= total:
                break
            y = 16 + row * 16
            name = items[idx].get("name", "???")[:15]
            if idx == selected_idx:
                self.display.fill_rect(0, y - 1, Config.OLED_WIDTH, 12, 1)
                self.display.text(name, 8, y + 1, 0)
            else:
                self.display.text(name, 8, y + 1, 1)
        self.display.show()

    def show_message(self, title, message=""):
        self.display.fill(0)
        self.display.rect(0, 0, Config.OLED_WIDTH, Config.OLED_HEIGHT, 1)
        self.display.fill_rect(0, 0, Config.OLED_WIDTH, 14, 1)
        self.display.text(title[:15], 5, 3, 0)
        lines = str(message).split("\n")
        for i, line in enumerate(lines):
            if i > 4:
                break
            self.display.text(line[:16], 5, 18 + i * 10, 1)
        self.display.show()
