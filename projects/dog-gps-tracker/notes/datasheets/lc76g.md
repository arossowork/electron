# Quectel LC76G facts (LC76G HW design V1.3 2024-11, GNSS spec V1.1, via search summary)
- GPS, GLONASS, Galileo E1, BDS, QZSS (5 constellations on by default).
- 10.1 x 9.7 x 2.4 mm LCC, 0.5 g, footprint-compatible with L76 (KiCad RF_GPS:Quectel_L76).
- Supply AB/PA 2.55-3.6 V; PB 1.75-1.98 V. Tracking: AB 28.3 mA, PA 8.2 mA (spec 10 mA), PB 13 mA. PB backup 13 uA.
- Sensitivity acq -147, track -166 dBm. LCSC stock not confirmed.

## Stage 3 additions (LC76G HW design V1.3, 2024-11-25; quectel.com/content/uploads/2023/05/Quectel_LC76G_Series_Hardware_Design_V1.3.pdf; pdftotext, page = footer "n / 58")
- Package: 10.1 x 9.7 x 2.4 mm, **28 pads = 18 LCC (castellated, pins 1-9 left, 10-18 right) + 10 LGA (19-28, centre)** (p.9, 40). KiCad RF_GPS:Quectel_L76 has only the 18 LCC pads: NOT a full footprint (the centre GND pad 28 and D_SEL 24 etc. are missing). Fix: project footprint, or L76 footprint + "LGA pads unused" (D_SEL default pulled down = UART, ok; GND pins 1,10,12 on LCC are enough electrically, 28 is GND too). Quectel: keep 3 mm free around the module (p.40).
- Pins (Table 6, p.20-23): 1 GND, 2 TXD (DO), 3 RXD (DI), 4 1PPS, 6 V_BCKP, 8 VCC, 9 RESET_N, 10 GND, 11 RF_IN (50 ohm), 12 GND, 13 ANT_ON, 14 VDD_RF, 16 I2C_SDA, 17 I2C_SCL, 20 SPI_CS, 21 GEOFENCE, 22 JAM_IND, 23 3D_FIX, 24 D_SEL, 25-27 SPI, 28 GND. RESERVED 5,7,15,18,19: leave floating, not to GND/power.
- Supply (PA): VCC 2.55/3.3/3.6 V; V_BCKP 1.65/3.3/3.6 V (must be powered at start-up, simultaneously with or before VCC); ripple < 50 mV (p.28-29). Abs max (Table 10, p.41): VCC -0.3..3.63 V, V_BCKP 0..3.63, I/O -0.3..3.63, RF_IN 0 dBm max. IO levels: VOH min 2.4 V, VIH min 2.0 V (so UART to nRF9151 at VDD_GPIO 3.0-3.3 V; 1.8 V GPIO is NOT enough for VIH 2.0 V).
- Current (PA; p.44-45): tracking 9.7 mA, acquisition 9.7 mA, ALP1 3.8 mA, ALP2 7.5 mA, IPEAK 24 mA, backup mode 13 uA, I(V_BCKP) 127 uA while active. Powering VCC+V_BCKP off = 0 uA but cold start (TTFF 28 s w/o AGNSS, 15 s with EASY, 5 s with EPO cold; hot 1 s needs V_BCKP). Always-on V_BCKP backup 13 uA > PWR-5 10 uA total.
- Decoupling at VCC: TVS + 10 uF + 100 nF + 33 pF, smallest closest (p.25). RESET_N: internal 10k pull-up to 1.8 V, NO external pull-up; VIL max 0.45, VIH min 1.72 V, low >= 100 ms resets; leave open-drain driven or N/C.
- RF: SAW required ("must", p.38): Murata SAFFB1G56AC0F7F or RF360 B39162B2618P810 close to RF_IN. Passive antenna: 1559-1606 MHz, RHCP, VSWR < 2, gain > 0 dBi (Table 9 p.37). VDD_RF = VCC, ANT_ON high in active modes (only for an active antenna/LNA).
- Op temp -40..85 C. Reflow peak 235-246 C.

## Project library `dog_misc:LC76G` (stage 3 lib, read from the PDF figures)
- Footprint: HW design V1.3 Fig 18 "Recommended Footprint" p.40 (pdf p.41) + Fig 19 p.46: LCC pads 1.8 x 0.8, pitch 1.1, centres x = +-4.85, y = -4.4..+4.4 (pad 1 top left, 1-9 down the left, 10-18 up the right; same as RF_GPS:Quectel_L76); LGA pads 0.8 x 0.8, pitch 1.1, centres y = -4.4 (pads 23..19, x = -2.2..+2.2) and y = +4.4 (pads 24..28, x = -2.2..+2.2); body 9.7 x 10.1 +-0.15 x 2.4 +-0.2; coplanarity <= 0.13; keep 3 mm free. Pad sizes of the LGA row 0.8 x 0.8 read from the figure ("0.8"), pads 19-23 vs 24-28 y-position from the figure scale (equal to the pad 1/9 rows).
- Symbol pins: Fig 2 p.19 + Table 6 p.20-23. Reserved 5,7,15,18,19 = no_connect pins (leave unconnected). 4 GND pads (1,10,12,28). D_SEL internal pull-down: UART.
- Full MPN suffix NOT in the HW design doc (labelling fig only). Quectel's LC76G-PA order code is believed LC76GPANR12A01S: confirm on the distributor page before BOM (value in the symbol is "LC76G-PA").
