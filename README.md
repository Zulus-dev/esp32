# Colibry OS Project Structure

- main.py — основной kernel
- config.py — пин-аут и настройки
- boot.py
- radio/
  - managers/subghz.py (CC1101)
  - drivers/cc1101_driver.py
  - wifi_audit.py, nrf24.py, ble.py
- menu/manager.py + menu_config.json
- web/server.py + web/api.py
- hardware/ — OLED, buttons, buzzer, radio_uart# esp32
