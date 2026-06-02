# Battery monitor implementation notes

## Hardware connection

The firmware expects a high-impedance divider from the battery/BMS sense point to
Node A ADC:

```text
BMS+/battery sense point -- 100k -- ADC node -- 47k -- GND
                                      |
                                     100nF
                                      |
                                     GND
```

Default firmware constants are in `Mv1/config.py`:

- `BATTERY_ADC_PIN = 6`
- `BATTERY_DIVIDER_TOP_OHM = 100_000`
- `BATTERY_DIVIDER_BOTTOM_OHM = 47_000`
- `BATTERY_EMPTY_MV = 3300`
- `BATTERY_FULL_MV = 4200`

Before connecting Node A, verify with a multimeter that the ADC node never
exceeds the ESP32-C3 ADC-safe range. With 100k/47k, a 5.3 V source is divided to
about 1.69 V.

## GPIO6 caveat

GPIO6 is the default because it is free in the current Node A firmware map. If a
specific ESP32-C3 SuperMini revision does not expose GPIO6 as an ADC-capable pin,
move the sense wire to a free ADC-capable pin and update `BATTERY_ADC_PIN`.

## Optional 1M resistor

A 1M resistor from the ADC node to GND is electrically parallel to the 47k lower
resistor. It is usually redundant because the 47k already pulls the ADC node down
if the top resistor opens. If the 1M resistor is installed anyway, compensate the
small divider error with `BATTERY_CALIBRATION_PERMILLE` after measuring a known
battery voltage.

## Charge percentage calibration

The default percent mapping is a simple linear single-cell Li-ion estimate from
3.30 V to 4.20 V. If the sense point is a regulated 5 V boost output instead of
raw cell/BMS voltage, use the displayed voltage as a supply-health indicator and
adjust `BATTERY_EMPTY_MV` / `BATTERY_FULL_MV` to realistic bus thresholds for the
actual hardware.
