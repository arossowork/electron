# ESP32-C3 (Espressif), used through ESP32-C3-MINI-1 module

Source: documentation.espressif.com/esp32-c3_datasheet_en.html (fetched 2026-10-20). Module datasheet (ESP32-C3-MINI-1) to confirm in stage 3: pinout, 13.2 x 16.6 x 2.4 mm, antenna keep-out, CE RED certificate.

- VDD recommended 3.0-3.6 V; pin absolute maximum -0.3 to 3.6 V
- Deep sleep (RTC timer + RTC memory): 5 uA typ (Table 5-9). Datasheet does not say if the external 32 kHz crystal changes it (budget +1 uA, bench-check)
- Light sleep: 130 uA
- Wi-Fi TX 802.11b 1 Mbps @ 21 dBm: 335 mA; 802.11n HT20 MCS7 @ 18.5 dBm: 276 mA; RX: 84 mA
- External 32.768 kHz crystal: XTAL_32K_P = GPIO0, XTAL_32K_N = GPIO1. Internal RC ~136 kHz (adjustable, no accuracy figure)
- ADC: 12-bit SAR. ADC1: 5 channels GPIO0-GPIO4, factory calibrated. ADC2: 1 channel (GPIO5), not factory-calibrated, "not operable on some chip revisions" -> do not use
- ADC ranges and error after calibration: ATTEN0 0-750 mV, ATTEN1 0-1050 mV, ATTEN2 0-1300 mV: +-10 mV; ATTEN3 0-2500 mV: +-35 mV
- VIH 0.75 x VDD; VIL 0.25 x VDD; cumulative IO output current 1000 mA max
- Strapping (Table 3-3): GPIO9 selects download (0) / SPI boot; GPIO8 = 1 needed for download boot; "GPIO2 actually does not determine SPI Boot and Joint Download Boot mode, but it is recommended to pull this pin up due to glitches". Strapping latched at chip reset.
