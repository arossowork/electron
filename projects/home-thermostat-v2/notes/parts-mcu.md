# Parts: MCU / radio / UI group (stage 3)

Facts and sources: `notes/datasheets/esp32-c3-mini-1.md`. Prices are the index unit price (qty 1 tier or qty 10 where noted), taken 2026-10-20. The index (jlcsearch) returns no stock for filtered passive queries; those are JLCPCB basic parts, so stock is high, but R29 (stock >= 15) must be re-checked at order time.

## U2 WiFi module: ESP32-C3-MINI-1-H4 (LCSC C2934569)
- Role: MCU + WiFi 4 + BLE 5 (R20, R24), PCB antenna, pre-certified module, 4 MB flash, schedule storage (R17).
- Price/stock: $3.353, stock 6370 (index). Datasheet: https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf
- Temperature: -40..105 C (R25 pass). Size 13.2 x 16.6 x 2.4 mm. TX power 20.5 dBm (R20 needs >= 15 dBm: pass).
- Symbol: project library `thermostat:ESP32-C3-MINI-1` (file `lib/symbols/thermostat.kicad_sym`), written in this stage: KiCad has no C3-MINI-1 symbol. Footprint: `thermostat:ESP32-C3-MINI-1` (`lib/footprints/thermostat.pretty/ESP32-C3-MINI-1.kicad_mod`), 53 pad numbers, 61 pads (pad 49 = 9 pads, the 3 x 3 GND array), antenna keep-out rule area on the antenna end, F.CrtYd 13.6 x 17.0 mm. Check: symbol pins == footprint pad numbers (53 = 53) with `Libs(extra_dirs=...)`.
- Pins used in design.yaml (symbol pin number = pad number): 3 = 3V3, 8 = EN, 12 = IO0, 13 = IO1, 5 = IO2, 6 = IO3, 18 = IO4, 19 = IO5, 20 = IO6, 21 = IO7, 22 = IO8, 23 = IO9, 16 = IO10, 26 = IO18, 27 = IO19, 30 = RXD0 (GPIO20), 31 = TXD0 (GPIO21). GND: 1, 2, 11, 14, 36-53 (all must be listed on net GND; 22 pins, 18 of them hidden and stacked in the symbol). NC (type no_connect, no mapping needed): 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32, 33, 34, 35.
- Key parameters: deep sleep 5 uA (RTC timer + RTC memory; add about 1 uA for the 32 kHz crystal, assumption); light sleep 130 uA; modem-sleep CPU 23 mA; RX 82 mA; TX peak 350 mA (11b, 20.5 dBm); supply 3.0-3.6 V; abs max input -0.3 to 3.6 V.
- WiFi connect energy: no datasheet figure. Reference used by architecture (conflicts.md C1): 0.5 J per check-in, i.e. about 100 mA average for 1.5 s. Datasheet bounds: 84 mA RX + 350 mA short TX bursts.
- Choice vs ESP32-S3-MINI-1 (the only MINI symbol in KiCad): the S3 deep-sleep figure is about 7 uA (my recollection of its datasheet, not re-read) against 5 uA; R3 budget has 1.1 uA margin (sum 8.90 of 10 uA), so the S3 would need about 11 uA and fails R3; it also costs more and has more GPIO than needed. The C3 is clearly better, so the project library was created.
- Alternatives: (1) ESP32-C3-MINI-1-H4X, C41349510, $3.079, stock 1541, same footprint, chip ESP32-C3FH4X variant (check chip revision/limits before use); (2) ESP32-C3-MINI-1U-H4 C3013922, $3.304, stock 639, u.FL antenna connector instead of PCB antenna, not wanted (R22 antenna on board). The 85 C "-N4" MINI-1 is not in the index. Price is $1.05 above the architecture estimate (2.3); the cost total in architecture.md section 9 moves from 16.7 to about 17.8 USD, still <= 25 (R28).

## Y1 crystal: ABRACON ABS07-32.768KHZ-7-T (LCSC C276424)
- Role: RTC 32.768 kHz on XTAL_32K_P/N (GPIO0/1), R18. +-20 ppm at 25 C, CL 7 pF, ESR 70 kOhm max, -40..85 C, 3.2 x 1.5 mm.
- Price $0.5949 (qty 10), stock 6339. Datasheet: https://www.lcsc.com/datasheet/C276424.pdf
- Symbol `Device:Crystal_Small` (pins 1, 2); footprint `Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm` (pads 1, 2).
- ESR 70 kOhm equals the ESP32-C3 limit (<= 70 kOhm, guideline page): zero margin on the stated max; typical ESR is lower. Risk: oscillator start-up in cold corner; check on the prototype.
- Load capacitors C_L1, C_L2: 8.2 pF C0G 0402, C1579 (0402CG8R2C500NT, $0.0022) or Murata C76984 (GRM1555C1H8R2CA01D, $0.0075). CL = C/2 + Cstray = 4.1 + about 3 pF = 7.1 pF (Cstray 3 pF assumed: pads, trace, pin). Tune on the prototype with a frequency counter; 1 pF is about 3 ppm.
- Alternatives: ABS07-32.768KHZ-6-T C1985518 (6 pF, $0.827, stock 1352, lower drive, load caps 6.2 pF); Micro Crystal CM7V-T1A-32.768KHZ-7PF-20PPM C5366537 (7 pF, 20 ppm). 12.5 pF parts (C620155 YXC, C182057 Citizen) need 22 pF caps and more oscillator current: rejected for sleep current.
- Drift check R18 (architecture 2.3): 20 ppm + 21 ppm temperature at 0 C + 5 ppm load-capacitor mismatch = 46 ppm = 28 s/week <= 60 s.

## Module support passives (values from the Espressif guideline page; LCSC are JLCPCB basic unless noted)
| Ref | Value | Part | Role |
|---|---|---|---|
| C_U2a | 10 uF X5R 10 V 0603 | C19702 CL10A106KP8NNNC $0.0824 | module 3V3 bulk next to pad 3 (guideline: 10 uF + 0.1 uF) |
| C_U2b | 100 nF X7R 16 V 0402 | C1525 CL05B104KO5NNNC $0.006 | 3V3 HF decoupling at pad 3 |
| R_EN | 100 kOhm 1 % 0402 | C25741 $0.004 | EN pull-up to 3V3 |
| C_EN | 4.7 uF X5R 10 V 0402 | C23733 CL05A475MP5NRNC $0.0192 | EN delay, tau about 0.47 s nominal (about 0.25 s at 3.3 V DC bias) |
| R_B9 | 10 kOhm 1 % 0402 | C25744 $0.0029 | GPIO9 pull-up (guideline: external pull-up, no capacitor on GPIO9) |
| C_L1, C_L2 | 8.2 pF C0G 0402 | C1579 | crystal load |

EN RC: the guideline value is 10 kOhm / 1 uF (tau 10 ms), and the task asked for it, but the architecture (section 7) needs EN to lag the 3V3 ramp: the boost charges 8200 uF in about 0.15 s, so a 10 ms tau lets EN go high at the start of the ramp and the module sees a slow VDD rise with EN already high. I propose 100 kOhm / 4.7 uF (architecture value). Static cost zero (a capacitor leaks about 0.03 uA at most: add to the R3 sum, margin 1.1 uA). Product decision not needed; circuit stage may fall back to 10 k/1 uF if the SPICE rail ramp is shorter than 10 ms.

## Programming / debug: Tag-Connect TC2030-IDC-NL (no-legs) footprint, no connector part
- Footprint `Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical` (6 pads, numbers 1-6, plus 3 non-plated holes), symbol `Connector:Conn_01x06_Pin`; BOM cost 0 (copper only), no JLCPCB part.
- Pad assignment (6 signals): 1 = 3V3, 2 = GND, 3 = EN, 4 = GPIO9 (BOOT), 5 = GPIO18 (USB D-), 6 = GPIO19 (USB D+). USB-Serial-JTAG on GPIO18/19 programs the module without a UART bridge, so GPIO20/21 are free for I2C. Needs a custom adapter TC2030-NL to USB-A (hand built, 22 ohm series not fitted); fallback if no Tag-Connect cable is wanted: six 1.5 mm round pads at 2.54 mm (`TestPoint:TestPoint_Pad_D1.5mm`) in a row.
- No D+/D- series resistors (guideline suggests 22/33 Ohm): lines are only 2 cm and only connected during programming. Risk is low; add 2 x 22 Ohm 0402 (C25092) if USB enumeration is unstable.
- First programming: a blank module enumerates on USB by itself. After a firmware that uses GPIO18/19 or sleeps, hold GPIO9 low through pad 4 during reset.

## SW1 button: XKB TS-1187A-B-A-B (LCSC C318884)
- Role: WiFi setup, forced check-in, long-press reset; deep-sleep wake on GPIO2.
- 5.1 x 5.1 mm, 1.5 mm high, SMD 4-pad, 1.6 N, 100 000 cycles, -30..85 C, basic, $0.0197, stock 280245. Datasheet via LCSC C318884.
- Symbol `Switch:SW_Push` (pins 1, 2); footprint `Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A` (pad 1 twice, pad 2 twice: the two pads of the same number are shorted inside). Top actuated, 1.5 mm body: sits behind the plate and a plate pusher reaches the 1.5 mm dome. Plate geometry is not in the inputs: the mechanical stage must confirm the plunger length. Side-actuated alternative: TS24CA (C393942, 4.6 x 1.8 mm, right angle, $0.0253, stock 298287, -20..70 C: R25 OK), footprint not in KiCad: needs a custom footprint.
- Alternatives: TS-1088-AR02016 (C720477, 4 x 3 mm, basic, $0.0557, stock 605776, no KiCad footprint); TS5220A (C412370).
- ESD (R26): TPD1E10B06DPYR, LCSC C48260, $0.0188, stock 182680, bidirectional, +-30 kV contact and air (IEC 61000-4-2), Vrwm 5.5 V, leakage 100 nA max, 12 pF, 10 V clamp at 1 A, -40..125 C. Datasheet https://www.ti.com/product/TPD1E10B06. Symbol `Device:D_TVS` (pins A1, A2); footprint `Diode_SMD:D_SOD-882` (pads 1, 2; the TI X1SON-2 / DFN1006-2 land pattern must be compared with this generic one). Leakage 100 nA max vs the 0.05 uA in architecture 4.1: typical is about 10 nA; if the worst case counts, the R3 margin falls from 1.1 to 1.05 uA, still passes. Alternative: Nexperia PESD5V0S1ULD C552570 (stock 0 in the page, leakage 1 uA max at 5.5 V from the datasheet: do not use).
- Circuit: SW1 pin 1 -> node A (TPD1E10B06 to GND) -> 1 kOhm (C11702, 0402) -> GPIO2 with 100 kOhm pull-up to 3V3 (C25741); SW1 pin 2 -> GND. GPIO2 must be high at reset (download boot needs GPIO8 = 1 and the pull-up keeps GPIO2 high); an unpressed button draws 0, pressed 33 uA.

## D1 status LED: LG L29K-F2J1-24-0-2-R18-Z (LCSC C2899882; ams-OSRAM family, manufacturer not shown by the index)
- 0603, green 570 nm, tested at 2 mA (3.5 mcd), Vf about 1.7 V (index), -40..100 C, $0.2675, stock 2384. Symbol `Device:LED_Small` (K = 1, A = 2); footprint `LED_SMD:LED_0603_1608Metric` (pads 1, 2).
- Resistor 910 Ohm 1 % 0402 (C44933, $0.0009, not basic) to 3V3, LED cathode to GPIO8 (active low): I = (3.3 - 1.7)/910 = 1.76 mA, max (3.37 - 1.6)/910 = 1.95 mA (<= 2 mA, R23). At 3.0 V rail and Vf 2.0 the current is 1.1 mA: still visible.
- LED on 3V3 side keeps GPIO8 high at reset (download boot condition and ROM log pin). Vf at 2 mA must be confirmed in the Osram datasheet (index shows 1.7 V without test current).
- Alternative: NCD0603C5-F (C41378548, $0.0293, stock 1342, 565-578 nm, 80 mcd at 20 mA, Vf 2 V at 20 mA, so about 1.8 V at 2 mA: use 750 Ohm). Cheaper by $0.24, brightness at 2 mA to be checked.

## GPIO allocation (module pin in brackets)
| GPIO | Pad | Function | Boot / sleep note |
|---|---|---|---|
| 0 | 12 | XTAL_32K_P (Y1) | analog; also ADC1_CH0 |
| 1 | 13 | XTAL_32K_N (Y1) | |
| 2 | 5 | SW1 button, wake (low level) | strapping; 100 kOhm pull-up keeps it high; deep-sleep wake GPIO (0-5) |
| 3 | 6 | VBAT ADC (ADC1_CH3, ATTEN2) | |
| 4 | 18 | K1 SET gate | clean pin (MTMS), floating at boot, 100 kOhm gate pull-down wins |
| 5 | 19 | K1 RESET gate | clean pin (MTDI) |
| 6 | 20 | K2 SET gate | clean pin (MTCK) |
| 7 | 21 | K2 RESET gate | clean pin (MTDO) |
| 8 | 22 | LED D1 (active low to 3V3) | strapping; LED path keeps it high |
| 9 | 23 | BOOT pad (TC2030 4), 10 kOhm pull-up | strapping; nothing else on it |
| 10 | 16 | VSW (switched 3.3 V: NTC dividers, ADC supply, VBAT divider NMOS gate) | clean output |
| 18 | 26 | USB D- (TC2030 5) | not used as GPIO |
| 19 | 27 | USB D+ (TC2030 6) | not used as GPIO |
| 20 | 30 | I2C SDA to U3 | pull-up to VSW; WPU at reset, harmless (U3 unpowered) |
| 21 | 31 | I2C SCL to U3 | ROM UART TX idles high at boot into SCL: harmless while VSW is low; disable ROM log (eFuse) in production |

Differences from the tentative plan in architecture.md section 7 (which put the LED on GPIO2, I2C on GPIO6/7, button on GPIO5): the relay gates must not sit on boot-active pins. Table 2-1 shows GPIO18/19 with the USB D+ pull-up active after reset, GPIO20 with a weak pull-up, GPIO21 driving its UART idle level high: on a gate with a 100 kOhm pull-down these would set or reset a latching relay at boot. GPIO4-7 are plain inputs after reset (verify in Table 2-1: the HTML excerpt only listed the strapping, USB and UART rows), so they take the four relay gates, and the I2C bus moves to GPIO20/21 where a boot glitch has no effect. GPIO8 and GPIO2 must be high at reset: they carry the LED and the button, both pulled to 3V3. Button on a deep-sleep wake pin: GPIO2 (0-5 are the RTC wake pins; 0/1 used by Y1, 3 by the battery ADC). Low-level wake is configured in firmware. Gate pull-downs 100 kOhm as in architecture 2.4; with GPIO hold during the 30 ms pulse.

## Issues and risks
1. PDF not converted: shell policy blocked `curl` and `pdftotext`, so facts came from the HTML datasheet pages. The footprint is Espressif's own KiCad geometry, not re-derived from the PDF drawing (section 10, p.35-36). Verify before fabrication.
2. `uv run hwflow lib show/pads thermostat:...` fails: `cli.py` builds `Libs()` without the project design, so project libraries are not searched. The library loads correctly with `Libs(extra_dirs=[lib/symbols, lib/footprints])` (53 pins, 61 pads, same pad numbers), and `hwflow check` uses `project_lib_dirs(design)` so it should resolve in the build. Not confirmed with a real design.yaml (not edited here).
3. `hwflow parts` crashes (TypeError in `_fmt`) when a result has no stock value, and `crystals` returns HTTP 404 (the index has no crystals); filtered passive queries return stock None. Crystal, LED and ESD were taken from LCSC pages.
4. Module 85 C "-N4" not in the index; H4 (105 C flash) costs about 1 USD more than the architecture estimate.
5. Crystal ESR 70 kOhm at the ESP limit; tune load capacitors on the prototype.
6. TPD1E10B06 leakage is 100 nA max (0.05 uA budgeted); EN capacitor leakage 0.03 uA: R3 margin 1.1 uA absorbs both.
7. Button plunger length and plate geometry are open (mechanical).
8. Single source for all three: Espressif module, ABS07 crystal, TPD1E10B06 (the Nexperia and Leiditech clones C2972450 exist for the TVS).
9. Boot pins: GPIO4-7 reset state to confirm in SoC Table 2-1 (p.16-18); GPIO20/21 on I2C needs the ROM-log eFuse for production.
