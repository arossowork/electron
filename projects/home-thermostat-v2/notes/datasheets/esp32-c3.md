# ESP32-C3 (SoC) / ESP32-C3-MINI-1 (module)

The Espressif PDFs (documentation.espressif.com/esp32-c3_datasheet_en.pdf, esp32-c3-mini-1_datasheet_en.pdf) were downloaded but could not be converted to text in stage 2. Facts below come from the cited secondary sources; stage 3 confirms them from the PDF.

| Fact | Value | Source |
|---|---|---|
| ADC calibrated total error, ATTEN0 (0-750 mV) | +/-10 mV | ESP-IDF ADC calibration docs / datasheet table (web search 2026-10-20) |
| ATTEN1 (0-1050 mV) | +/-10 mV | same |
| ATTEN2 (0-1300 mV) | +/-10 mV | same |
| ATTEN3 (0-2500 mV) | +/-35 mV | same |
| ADC reference | 1100 mV nominal, 1000-1200 mV chip to chip | same |
| ADC2 | oneshot not supported on C3 (unstable results, errata) | ESP-IDF adc_oneshot docs |
| ADC1 channels | GPIO0-4 | ESP-IDF (verify pin table) |
| Deep-sleep wake GPIOs | GPIO0-5 | verify |
| 32 kHz crystal pins | XTAL_32K_P/N = GPIO0/GPIO1 | verify |
| Deep sleep current | about 5 uA | conflicts.md reference table (esp32.co.uk); verify |
| RX current / TX peak | about 84 mA / 200-400 mA | conflicts.md reference table; verify |
| Strapping pins | GPIO2, GPIO8, GPIO9 | verify |
| Module size | 13.2 x 16.6 mm | conflicts.md C3; verify height |
