# ESP32-C3-MINI-1 module (Espressif)

Source: documentation.espressif.com/esp32-c3-mini-1_datasheet_en.html (fetched 2026-10-20); PDF at documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf. HW guidelines: docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32c3/.

- Size 16.6 +-0.15 x 13.2 +-0.15 x 2.4 +-0.15 mm (s10.1 p35). Land pattern s11.1: 48 x 0.4/0.8 pads plus 4 thermal pads/vias.
- Pins (Table 3-1 p11): GND 1,2,11,14,36-53; 3V3 3; EN 8; IO0 12; IO1 13; IO2 5; IO3 6; IO4 18; IO5 19; IO6 20; IO7 21; IO8 22; IO9 23; IO10 16; IO18 26; IO19 27; IO20/RXD0 30; IO21/TXD0 31; NC 4,7,9,10,15,17,24,25,28,29,32-35.
- Supply 3.0-3.6 V, 0.5 A min external (Table 6-2 p21). 85 C (N4X) or 105 C (H4X) variants. Deep sleep RTC timer + RTC memory 5 uA typ (Table 6-7). ESD HBM 2 kV, CDM 500 V (s12.2).
- Strapping (Table 4-3 p13): GPIO2, GPIO8, GPIO9; GPIO9 default weak pull-up = SPI boot, low = download.
- Reference schematic (s9 p34): EN 10 k + 1 uF; X1 32.768 kHz is "NC" by default, C1/C2 "TBD, depend on the crystal". s8 p32: 40 MHz crystal +-10 ppm.
- Keep-out: Figure 3-1 only (dotted zone, no mm in the text); HW guideline: antenna at the board edge, 15 mm clearance in all directions in the final product, ground vias near the antenna.
- Guidelines: 32 kHz crystal ESR <= 70 k, optional parallel 5-10 M; USB D+/D- 22/33 ohm series; 10 uF + 0.1 uF decoupling; ESD diode and >= 10 uF at the supply entry.
- CE RED: not confirmed from the page ("RF certification: see certificates").
- LCSC: ESP32-C3-MINI-1-H4X C41349510; -H4 C2934569 ($2.76 at 10, 3919 in stock).
- KiCad: no symbol or footprint for the C3-MINI-1. Project library now has both (lib/symbols/thermo.kicad_sym, lib/footprints/thermo.pretty).

## Stage 3 close-out (2026-10-20)
- Pad pattern in thermo:ESP32-C3-MINI-1: from the Espressif KiCad library file footprints/Espressif.pretty/ESP32-C3-MINI-1.kicad_mod as summarised by a web fetch (source: search summary, unverified; the datasheet land-pattern drawing is an image). Cross-check with the HTML datasheet: pad rows 11.8 mm across and 9.8 mm along, pads 0.4 x 0.8, thermal vias 0.7 mm. Origin at module centre: pads 1-11 left (x -5.9, y -1.3..6.7, pitch 0.8), 12-24 bottom (y 7.6, x -4.8..4.8), 25-35 right, 36-48 row at y -2.2, pad 49 = 3 x 3 pads of 1.45 mm (pitch 1.975), 50-53 corner pads 0.7 mm. GND = 1, 2, 11, 14, 36-53. Antenna zone y -8.3..-2.9 (5.4 mm, datasheet HTML "5.4 mm margin").
- Antenna: Espressif hardware design guidelines: antenna at the board edge, 15 mm clear in all directions in the final product (search summary).
- CE RED: Espressif "ESP32-C3-MINI-1 CE Certification.pdf" (https://www.espressif.com/sites/default/files/ESP32-C3-MINI-1 CE Certification.pdf): EU-Type Examination Certificate E1177-210536, Notified Body 1177 (TIMCO Engineering), dated 2 June 2021, RED 2014/53/EU Articles 3.1(a), 3.1(b), 3.2 (search summary, PDF not opened, unverified). The certificates page, when fetched, did not list C3-MINI-1 rows (filter showed S3-WROOM-1). Open point: whether the certificate covers the H4X/N4X variants.
