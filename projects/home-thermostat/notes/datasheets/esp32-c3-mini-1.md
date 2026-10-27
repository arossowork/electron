# ESP32-C3-MINI-1-N4 (Espressif), datasheet v2.2, https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf

Read pages 1-12, 21-23, 35, 38-39 of the PDF (doc page = pdf page).

| Item | Value | Page |
|---|---|---|
| Module | 13.2 x 16.6 x 2.4 mm, 53 pads, PCB antenna, 4 MB flash (-N4: chip rev v0.4, NRND; -N4X: rev v1.1 recommended). LCSC stocks the -N4 (C2838502) | 3 |
| Supply | 3.0-3.6 V; abs max VDD33 -0.3 to 3.6 V; external supply must deliver >= 0.5 A (table 6-2) | 21 |
| Pins (number: name) | 3: 3V3; 8: EN (do not float); 5: IO2; 6: IO3; 12: IO0 (XTAL_32K_P); 13: IO1 (XTAL_32K_N); 16: IO10; 18: IO4; 19: IO5; 20: IO6; 21: IO7; 22: IO8; 23: IO9; 26: IO18 (USB D-); 27: IO19 (USB D+); 30: RXD0 (GPIO20); 31: TXD0 (GPIO21); GND: 1, 2, 11, 14, 36-53; NC: 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32-35 | 10-11 |
| Strapping pins | GPIO2 (floating default), GPIO8 (floating), GPIO9 (weak pull-up, 45 k) | 12 |
| GPIO | VIH 0.75 x VDD, VIL 0.25 x VDD, IIH/IIL 50 nA max, source 40 mA, sink 28 mA, internal pull 45 k | 21 |
| Active current | TX 802.11b 20.5 dBm: 350 mA; 11g 18 dBm: 295 mA; 11n 290 mA; RX 82-84 mA | 22 |
| Modem sleep, 80 MHz | CPU running 17 mA (22 with peripherals); CPU idle 13 mA (18) | 23 |
| Light sleep | 130 uA; deep sleep 5 uA (RTC timer + RTC memory); power off 1 uA | 23 |
| Flash VCC | 2.7-3.6 V (3.3 V flash) | 23 |

Land pattern (fig 11-1 p.38, fig 10-1 p.35): 0.8 mm pitch, edge pads 0.8 x 0.4, 4 corner pads 0.7 x 0.7, 3 x 3 ground pads of 1.45 mm (all pin 49), pad zone 11.2 mm tall at the bottom of the module, antenna area 5.4 mm at the top. The project footprint `thermostat:ESP32-C3-MINI-1` is derived from these figures. The datasheet gives the pad dimensions in figure form only, so check the footprint against the Espressif DXF before fabrication (risk R-ESP-1).

Design consequences:
- IO2 is a strapping pin. The 100 k / 100 k VBAT divider on IO2 (architecture section 3.1) puts 0.45-0.83 V on the pin while IO21 pulls the low side down, and VBAT / 1 through the top resistor when IO21 is high-Z. That is neither a valid logic 0 nor logic 1 at reset (VIH 2.5 V). Circuit stage: put the divider top on a switched net, or add a pull-up, so IO2 reads 1 at reset.
- IO20 and IO21 are UART0 pins; the ROM boot message prints on TXD0 (IO21). IO21 (divider enable) will toggle at boot. Harmless if the divider only loads the cell for a few ms.
- IO8 (LED) is a strapping pin, IO9 (button) has the weak pull-up: both as in the architecture.
- The module has no free GPIO for the DRV8837 nSLEEP lines (see parts.md, risk R-DRV-1).
