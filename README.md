
ТЕХНИЧЕСКАЯ ДОКУМЕНТАЦИЯ Colibry OS — Dual-Node Pentest & Radio Audit Platform Версия документа: 1.0 Дата: 13 мая 2026 Автор: Senior Embedded Security Engineer





Введение Colibry OS — это компактная распределённая программно-аппаратная платформа на базе двух ESP32-C3, предназначенная для аудита безопасности беспроводных систем и проведения penetration testing в полевых условиях. Цель проекта:

Создать высокомобильное устройство класса «Flipper Zero + HackRF Mini» с разделением обязанностей между управляющим и радио-модулем. Обеспечить надёжную, отказоустойчивую работу в условиях сильных электромагнитных помех.

Ключевые возможности:

Sub-GHz (315/433/868 MHz) — CC1101 (сканирование, перехват, replay) 2.4 GHz HID audit (мыши/клавиатуры) + Honeypot — NRF24L01 Wi-Fi Audit (Monitor Mode, Deauth, Beacon Spam) Bluetooth LE Sniffing и spoofing Удобный HMI (OLED + Touch) + Web-интерфейс

ТЕХНИЧЕСКАЯ ДОКУМЕНТАЦИЯ Colibry OS — Dual-Node Pentest & Radio Audit Platform Автор: Senior Embedded Security Engineer





Введение Colibry OS — это компактная распределённая программно-аппаратная платформа на базе двух ESP32-C3, предназначенная для аудита безопасности беспроводных систем и проведения penetration testing в полевых условиях. Цель проекта:

Создать высокомобильное устройство класса «Flipper Zero + HackRF Mini» с разделением обязанностей между управляющим и радио-модулем. Обеспечить надёжную, отказоустойчивую работу в условиях сильных электромагнитных помех.

Ключевые возможности:

Sub-GHz (315/433/868 MHz) — CC1101 (сканирование, перехват, replay) 2.4 GHz HID audit (мыши/клавиатуры) + Honeypot — NRF24L01 Wi-Fi Audit (Monitor Mode, Deauth, Beacon Spam) Bluetooth LE Sniffing и spoofing Удобный HMI (OLED + Touch) + Web-интерфейс





Архитектура системы 2.1. Физическая структура

Node A (Master) — «Мозг» (HMI, логика, Web-сервер, управление питанием) Node B (Slave) — «Радио-интерфейс» (все RF-модули) Связь между узлами: UART 921600 baud (Full-Duplex)

2.2. Программная архитектура

Master: MicroPython + asyncio + lazy loading + модульная выгрузка Slave: MicroPython + asyncio + RTOS-подобные задачи (отдельные asyncio Tasks) Протокол взаимодействия: SLIP + TLV + CRC16-CCITT

Принцип lazy loading (оба узла):

Загружается только ядро (UART + диспетчер) По команде загружается конкретный модуль После STOP — полная выгрузка модуля из памяти (sys.modules + gc.collect())





Программная архитектура Master (Node A)

Kernel (main.py): ColibryCore с lazy module execution Menu: JSON-driven Web Server: Async lightweight HTTP + File API + Explorer Buttons: IRQ + ThreadSafeFlag + multi-click detection Power Management: MOSFET control + keep-alive watchdog

Особенности:

Полная выгрузка модулей после выполнения Минимизация использования RAM (~400KB на ESP32-C3) WebSocket-ready архитектура (для будущего реал-тайм обновления)





Требования к аппаратной части Node A : ESP32-C3 SuperMini OLED SSD1306 (I2C) TTP223 Touch buttons (2 шт.) Passive Buzzer pins на node A отвечающие за управление транзисторами : pin7 : N-MOSFET (q1) для управления питанием Node A при запуске геркон активирует транзистор и плата Node A запускается и уже сама управляет транзистором для своего питания и соответственно сама открывает и закрывает транзистор pin10 : N-MOSFET (q2) для управления питанием Node B ( Node A в своём меню запускает Node B через транзистор и при выключении посылает по UART команду на Node B что нужно выключить все модули , завершить все процессы и отослать подтверждение что плата готова к отключению (после отсылки подтверждения Node B сама переходит в спящий режим и отключается ) и через 2 секунды Node A отключает питание Node B через транзистор ) pin5 : N-MOSFET (q3) для управления питанием rf модулей
Node B: ESP32-C3 CC1101 + антенна NRF24L01+ Native Wi-Fi + BLE Опционально: IR TX/RX

