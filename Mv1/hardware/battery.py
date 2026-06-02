# hardware/battery.py - low-overhead ADC battery monitor for Node A
import time

from machine import ADC, Pin
from config import Config


class BatteryMonitor:
    """ADC voltage monitor for the BMS/battery sense divider on Node A.

    The hardware divider is expected to be connected to the BMS/battery output
    before the DC-DC modules. Defaults match 100k over 47k plus 100nF to GND.
    The monitor keeps a tiny in-RAM history for trend/min/max without storing
    large web graphs on the ESP32-C3 heap.
    """

    def __init__(self):
        self.adc = None
        self.ok = False
        self.error = "not_initialized"
        self.voltage_mv = 0
        self.percent = 0
        self.status = "UNKNOWN"
        self.trend = "?"
        self.min_mv = 0
        self.max_mv = 0
        self.samples = []
        self.updated_ms = 0
        try:
            self.adc = ADC(Pin(Config.BATTERY_ADC_PIN))
            try:
                self.adc.atten(ADC.ATTN_11DB)
            except Exception:
                pass
            try:
                self.adc.width(ADC.WIDTH_12BIT)
            except Exception:
                pass
            self.ok = True
            self.error = ""
        except Exception as exc:
            self.error = str(exc)

    def read(self):
        if not self.ok or self.adc is None:
            return self.snapshot()
        total = 0
        count = max(1, int(Config.BATTERY_SAMPLE_COUNT))
        for _ in range(count):
            total += self._read_adc_mv()
        sense_mv = total // count
        self.voltage_mv = self._divider_to_source_mv(sense_mv)
        self.percent = self._percent(self.voltage_mv)
        self._update_history(self.voltage_mv)
        self.status = self._status(self.percent)
        self.trend = self._trend()
        self.updated_ms = _ticks_ms()
        return self.snapshot()

    def snapshot(self):
        return {
            "ok": self.ok,
            "error": self.error,
            "voltage_mv": self.voltage_mv,
            "voltage": self.voltage_mv / 1000.0,
            "percent": self.percent,
            "status": self.status,
            "trend": self.trend,
            "min_mv": self.min_mv,
            "max_mv": self.max_mv,
            "samples": self.samples,
            "updated_ms": self.updated_ms,
        }

    def _read_adc_mv(self):
        try:
            return int(self.adc.read_uv() // 1000)
        except Exception:
            raw = int(self.adc.read_u16())
            return (raw * int(Config.BATTERY_ADC_REF_MV)) // 65535

    def _divider_to_source_mv(self, sense_mv):
        top = int(Config.BATTERY_DIVIDER_TOP_OHM)
        bottom = int(Config.BATTERY_DIVIDER_BOTTOM_OHM)
        cal = int(Config.BATTERY_CALIBRATION_PERMILLE)
        if bottom <= 0:
            return 0
        return (int(sense_mv) * (top + bottom) * cal) // (bottom * 1000)

    def _percent(self, mv):
        empty = int(Config.BATTERY_EMPTY_MV)
        full = int(Config.BATTERY_FULL_MV)
        if mv <= empty:
            return 0
        if mv >= full:
            return 100
        return ((mv - empty) * 100) // (full - empty)

    def _status(self, pct):
        if not self.ok:
            return "UNAVAILABLE"
        if pct <= int(Config.BATTERY_CRITICAL_PERCENT):
            return "CRITICAL"
        if pct <= int(Config.BATTERY_LOW_PERCENT):
            return "LOW"
        return "NORMAL"

    def _update_history(self, mv):
        if self.min_mv == 0 or mv < self.min_mv:
            self.min_mv = mv
        if mv > self.max_mv:
            self.max_mv = mv
        self.samples.append([_ticks_ms(), mv])
        depth = int(Config.BATTERY_HISTORY_DEPTH)
        if len(self.samples) > depth:
            self.samples = self.samples[-depth:]

    def _trend(self):
        if len(self.samples) < 4:
            return "?"
        first = self.samples[-4][1]
        last = self.samples[-1][1]
        delta = last - first
        if delta > int(Config.BATTERY_TREND_DELTA_MV):
            return "UP"
        if delta < -int(Config.BATTERY_TREND_DELTA_MV):
            return "DOWN"
        return "STABLE"


def _ticks_ms():
    try:
        return time.ticks_ms()
    except Exception:
        return int(time.time() * 1000)
