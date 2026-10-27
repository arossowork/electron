# ESP32-C3-MINI-1 (module) and ESP32-C3 (SoC): facts for the design

Access note (stage 3): the shell policy blocked downloading and `pdftotext` of the PDFs, so the facts below were read from Espressif's HTML datasheet pages through WebFetch (page/section numbers are those the HTML gives for the PDF) and from the Espressif hardware design guideline page. The pad coordinates come from Espressif's own KiCad library (github.com/espressif/kicad-libraries, footprint ESP32-C3-MINI-1) and were cross-checked against the 53-pin table. A human must compare the footprint with the PDF land-pattern drawing (MINI-1 datasheet section 10, p.35-36) before fabrication. Items marked (verify) were not seen in the primary text.

Sources
- Module datasheet: https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf (HTML: .../esp32-c3-mini-1_datasheet_en.html)
- SoC datasheet: https://documentation.espressif.com/esp32-c3_datasheet_en.pdf (HTML: .../esp32-c3_datasheet_en.html)
- Schematic checklist: https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/schematic-checklist.html
- Deep-sleep wake GPIOs: https://docs.espressif.com/projects/esp-idf/en/v5.2/esp32c3/api-reference/peripherals/gpio.html

## Module (MINI-1 datasheet)
| Fact | Value | Where |
|---|---|---|
| Variants | MINI-1 / MINI-1U; flash 4 MB or 8 MB; -40..85 C or -40..105 C ("H" = 105 C) | sec 1.2 Tables 1-1, 1-2, p.3-4 |
| Size MINI-1 | 13.2 x 16.6 x 2.4 mm (+-0.15) | sec 10.1, p.35 |
| Pin count | 53 pads: 1,2,11,14,36-53 GND; 3 = 3V3; 8 = EN; 15 GPIO; 14 NC | sec 3.2 Table 3-1, p.11 |
| Strapping | GPIO2, GPIO8 floating; GPIO9 weak pull-up (1). SPI boot: GPIO8=1, GPIO9 any. Joint download boot: GPIO8=1, GPIO9=0 | sec 4.1 Table 4-3, p.13 |
| Deep sleep | 5 uA (RTC timer + RTC memory) | sec 6.4 Table 6-7, p.22-23 |
| Wi-Fi TX peak | 350 mA (802.11b 1 Mbps, 20.5 dBm) | sec 6.4, p.22-23 |
| Wi-Fi RX | 82 mA (11b/g/n HT20) | sec 6.4 |
| Max TX power | 20.5 dBm (802.11b 1 Mbps) | sec 7.1.1 Table 7-2, p.25 |

## SoC (ESP32-C3 datasheet)
| Fact | Value | Where |
|---|---|---|
| Active TX 11b 1 Mbps at 21 dBm | 335 mA | sec 5.6 Tables 5-7 to 5-9, p.56-57 |
| Active RX 11b/g/n HT20 | 84 mA | same |
| Modem-sleep, CPU running | 23 mA | same |
| Light-sleep | 130 uA | same |
| Deep-sleep | 5 uA | same |
| Supply | 3.0 to 3.6 V; external supply must deliver 0.5 A | sec 5.2 Table 5-2, p.54 |
| Absolute max | input voltage -0.3 to 3.6 V; cumulative IO output current 1000 mA; storage -40..150 C | sec 5.1 Table 5-1, p.54 |
| ADC | DNL -7..+7 LSB, INL -12..+12 LSB; calibrated total error +-10 mV (ATTEN0-2), see esp32-c3.md | sec 5.5, p.56 |
| Pin reset states | GPIO2, GPIO8 IE (input, no pull); GPIO9 IE + WPU; GPIO18, GPIO19 USB_PU after reset (D+ pull-up, no input); GPIO20 IE + WPU; GPIO21 WPU with output enabled (ROM UART TX, idles high) | sec 2.2 Table 2-1, p.16-18 |
| ROM log | GPIO8 level selects UART0 ROM boot print when eFuse UART_PRINT_CONTROL = 0 | sec 3.1, p.30-32 |
| Deep-sleep wake GPIOs | GPIO0-5 only (RTC domain); others wake light sleep only | ESP-IDF GPIO docs (the SoC HTML excerpt did not list them) |
| ADC1 pins | GPIO0-4; GPIO5 is ADC2 (unusable) | MINI-1 pin table |

## Pin table (module pad: name: function), Table 3-1 p.11
3 3V3; 8 EN; 12 IO0 (ADC1_CH0, XTAL_32K_P); 13 IO1 (ADC1_CH1, XTAL_32K_N); 5 IO2 (ADC1_CH2, FSPIQ, strap); 6 IO3 (ADC1_CH3); 18 IO4 (ADC1_CH4, MTMS); 19 IO5 (ADC2_CH0, MTDI); 20 IO6 (MTCK); 21 IO7 (MTDO); 22 IO8 (strap); 23 IO9 (strap, BOOT); 16 IO10; 26 IO18 (USB_D-); 27 IO19 (USB_D+); 30 RXD0 (GPIO20, U0RXD); 31 TXD0 (GPIO21, U0TXD). NC: 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32-35. GND: 1, 2, 11, 14, 36-53 (49 = 3 x 3 centre array, 50-53 corner pads).

## Hardware guideline values (schematic checklist page)
- EN: R = 10 kOhm, C = 1 uF; tSTBL >= 50 us, tRST >= 50 us.
- GPIO9: place an external pull-up; no large capacitor on GPIO9 (the chip may enter download mode).
- 32.768 kHz crystal: ESR <= 70 kOhm; load capacitance per crystal data sheet; parallel bias resistor 5 to 10 MOhm normally not populated.
- U0TXD: 499 Ohm series resistor suppresses harmonics. USB lines: reserve series resistors (22/33 Ohm start) and capacitors to ground.
- Power entry: ESD diode and at least 10 uF at the supply entrance. Chip-level decoupling 0.1 uF per supply pin and 10 uF on the analog supply (the module has these inside; module-level values 10 uF + 0.1 uF at pin 3 are the reference-design convention, verify against the module reference schematic).
