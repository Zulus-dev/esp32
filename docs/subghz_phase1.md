# CC1101 Sub-GHz Phase 1 integration notes

## Найденные узкие места до рефакторинга

- Node B `radio/cc1101.py` генерировал тестовые пакеты и не работал с реальным FIFO/GDO/RSSI CC1101, поэтому проверка Node B с подключённым модулем не могла подтвердить реальный радио-тракт.
- Команды TLV покрывали только частоту, scan и replay; не было отдельных команд для modulation, targeted sniff, raw TX, output power и spectrum sweep.
- Node A хранил телеметрию как текст/hex без декодирования полей Sub-GHz, поэтому Web/OLED не могли эффективно показывать RSSI, частоту, модуляцию и payload.
- Не было throttling телеметрии Sub-GHz; при сильных помехах UART/Web могли получить burst больше возможностей ESP32-C3.
- CPU Node B был выставлен на 240 MHz, что не соответствует философии ESP32-C3 SuperMini и увеличивает энергопотребление.

## Новая архитектура Phase 1

```text
Node A Web/OLED/API
        |
        | SLIP + TLV
        v
Node B dispatcher (Sv1/main.py)
        |
        v
radio/managers/subghz.py  -- async state machine, buffers, throttling, captures
        |
        v
radio/drivers/cc1101_driver.py -- SPI registers, FIFO, RSSI, calibration, TX/RX
```

## TLV payloads

- `TLV_SUBGHZ_PACKET`: `freq_khz(4) | rssi_i8(1) | len(1) | timestamp_ms(4) | modulation(1) | data`.
- `TLV_RSSI_REPORT`: `freq_khz(4) | rssi_i8(1) | timestamp_ms(4)`.
- Manager limits telemetry to about 18 packets/second to protect UART, Web UI and RAM.

## Integration checklist

1. Flash/copy `Sv1` to Node B and `Mv3` to Node A.
2. Wire CC1101 to Node B pins from `Sv1/config.py`: SCK=4, MOSI=5, MISO=6, CSN=7, GDO0=8.
3. Wire Node A/Node B UART at 921600 baud using the existing TX/RX cross-link and common GND.
4. In the OLED menu: `Radio B -> Power on -> SubGHz scan`.
5. In Web UI: open `/radio.html`, click `Power on`, apply modem settings, then use `Continuous scan`, `Targeted sniff`, or `Spectrum sweep`.
6. Use `Replay`/`Raw TX` only after explicit confirmation and only in an authorized lab environment.

## Recommendations for next phases

- Add board-specific RF presets validated with SmartRF Studio exports for common protocols (KeeLoq/PT2262/Nice/Came/Chamberlain).
- Move protocol decoders to Web/Node A first to keep Node B RAM free; only promote tiny hot-path decoders to Node B when latency requires it.
- Add persistent `radio_settings.json` once Node B storage layout is finalized.
- Add hardware validation tests with a known CC1101 transmitter and a controlled attenuator before enabling any jammer workflow.
