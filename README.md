ТЕХНИЧЕСКАЯ ДОКУМЕНТАЦИЯ
Colibry OS — Dual-Node Pentest & Radio Audit Platform
Версия документа: 1.1
Дата: 10 августа 2026
Автор: Senior Embedded Security Engineer

1. Введение
Colibry OS — компактная распределённая программно-аппаратная платформа на базе двух ESP32-C3
для аудита безопасности беспроводных систем и penetration testing в полевых условиях.

Цель:
- Мобильное устройство класса «Flipper Zero + HackRF Mini» с разделением Master / Radio.
- Надёжная работа при сильных ЭМ-помехах и жёстком лимите RAM.

Ключевые возможности:
- Sub-GHz (315/433/868 MHz) — CC1101 (spectrum, RAW listen, replay)
- 2.4 GHz HID audit + Honeypot — NRF24L01
- Wi-Fi Audit (AP scan)
- Bluetooth LE scan
- HMI: OLED + Touch; Web UI (HTTP static + binary radio API)

2. Архитектура
2.1 Физическая
- Node A (Master): HMI, логика, HTTP :80, питание MOSFET
- Node B (Slave): все RF-модули
- Связь A↔B: UART 921600, SLIP + TLV + CRC16-CCITT (только binary codes)

2.2 Программная
- Master: MicroPython + asyncio + lazy load + purge модулей
- Slave: MicroPython + asyncio tasks, exclusive RF workspace
- Phone↔A: HTTP static UI; radio status/cmd/log = application/octet-stream (binary)
- JSON только: wifi_settings.json, menu_config.json, редкие FS/battery ответы
- Декодирование SSID / OOK preview / разбор событий — на телефоне (static/js/radio_bin.js)

2.3 Почему не WebSocket / Binary TCP
Ранее пробовались WS :81 и TCP :9090 — высокий расход RAM и задержки на ESP32-C3.
Текущая схема: HTTP request/response + binary body, single-flight server, без persistent socket.

3. Бинарный radio API (Phone ↔ Node A)
Команды POST /api/radio/cmd  body 8 байт:
  action_u8, mod_u8, freq_u32_be, preset_u8, thresh_u8
Ответ /api/radio/status и /api/radio/cmd:
  magic 0xC01B, ver, flags, link/power/module fields (40 B header) + N×16 B events
Сессии: /sessions/*.txt формат #COLIBRY1 TSV (читаемый без JSON)

4. RAM rules
- Static slabs (mempool) для web TX и RF workspace
- Event ring на A — fixed binary 32×32 B, без dict на RX
- Static TLV TX buffers на Node B
- Capture CC1101 hard-cap ≤450 ms (WDT-safe)

5. Аппаратура
Node A: ESP32-C3 SuperMini, OLED SSD1306, TTP223×2, buzzer
  pin7 Q1 self-latch, pin10 Q2 Node B power, pin5 Q3 RF power
Node B: ESP32-C3, CC1101, NRF24L01+, native Wi-Fi/BLE
