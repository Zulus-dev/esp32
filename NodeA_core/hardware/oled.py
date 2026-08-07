# hardware/oled.py — SSD1306 UI
#
# Status bar (left):  S | B | X
#   S = HTTP server ON, else .
#   B = Node B rail (командный Q2 ON), else .
#   X = RF letter if Q3 ON: C=CC1101, N=NRF, W=WiFi-audit, L=BLE; else .
#   Онлайн Node B / link — только по UART (не из опроса GPIO).
#

from machine import SoftI2C, Pin
from lib.ssd1306 import SSD1306_I2C
from config import Config
import gc


class OLED:
    def __init__(self):
        self.status_server = False
        self.status_node_b = False
        self.status_rf = "."  # '.', 'C', 'N', 'W', 'L'
        self.battery_percent = None
        self.battery_status = "UNKNOWN"
        self._last_status = None
        self.i2c = SoftI2C(
            scl=Pin(Config.OLED_SCL_PIN),
            sda=Pin(Config.OLED_SDA_PIN),
            freq=Config.OLED_I2C_FREQ_HZ,
        )
        self.display = SSD1306_I2C(Config.OLED_WIDTH, Config.OLED_HEIGHT, self.i2c)
        print("OLED initialized OK")

    def set_status(
        self,
        *,
        server=None,
        node_b=None,
        rf=None,
        battery=None,
        force=False,
        wifi=None,
        radio=None,
    ):
        # wifi/radio kept as aliases: wifi→server if server not set; radio ignored
        if server is not None:
            self.status_server = bool(server)
        elif wifi is not None:
            self.status_server = bool(wifi)
        if node_b is not None:
            self.status_node_b = bool(node_b)
        if rf is not None:
            if rf is True:
                self.status_rf = "C"
            elif rf is False or rf == "" or rf == ".":
                self.status_rf = "."
            else:
                self.status_rf = str(rf)[:1].upper()
        if battery is not None:
            self.battery_percent = battery.get("percent")
            self.battery_status = battery.get("status", "UNKNOWN")
        state = (
            self.status_server,
            self.status_node_b,
            self.status_rf,
            self.battery_percent,
            self.battery_status,
            gc.mem_free() // 1024,
        )
        if force or state != self._last_status:
            self.draw_status()
            self.display.show()
            self._last_status = state

    def draw_status(self):
        mem_kb = gc.mem_free() // 1024
        self.display.fill_rect(0, 0, Config.OLED_WIDTH, Config.OLED_STATUS_HEIGHT, 0)
        self.display.line(0, Config.OLED_STATUS_HEIGHT, Config.OLED_WIDTH, Config.OLED_STATUS_HEIGHT, 1)
        self.display.text("S" if self.status_server else ".", 2, 2, 1)
        self.display.text("B" if self.status_node_b else ".", 14, 2, 1)
        ch = self.status_rf if self.status_rf and self.status_rf != "." else "."
        self.display.text(ch[:1], 26, 2, 1)
        if self.battery_percent is not None:
            prefix = "!" if self.battery_status in ("LOW", "CRITICAL") else ""
            self.display.text((prefix + str(int(self.battery_percent)) + "%")[:5], 42, 2, 1)
        self.display.text(str(mem_kb) + "K", 92, 2, 1)

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
