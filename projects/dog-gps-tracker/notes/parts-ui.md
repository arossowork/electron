# Parts, group C: UI, sensors, debug, battery contacts (stage 3)
Prices: LCSC/JLC at ~1000 pcs (parts index or web), stock checked 2026-10-31. "unv" = not verified live.

| ref | block | MPN | LCSC | USD@1k | stock | symbol | footprint (pads) | side/method |
|---|---|---|---|---|---|---|---|---|
| BZ1 | buzzer | Murata PKLCS1212E4001-R1 | C113159 | ~0.6 (unv) | unv | Device:Buzzer (1 +, 2 -) | Buzzer_Beeper:Buzzer_Murata_PKLCS1212E (2 pads 1.2x4.0 at x=+-5.6, bbox 12.9x12.5, body 12x12x3.0) | front, SMT |
| C_BZ1/2 | coupling caps | 220 nF X7R 0402 50 V (one per GPIO) | basic | 0.005 | high | Device:C | C_0402 | front, SMT |
| D_LED | status LED | Wurth 150060RS75000 red 0603 (Vf 2.0 V, 250 mcd @20 mA) | C3030991 | 0.27 | 5537 | Device:LED | LED_SMD:LED_0603_1608Metric | front, SMT |
| R_LED | LED resistor | 330 ohm 0402 | basic | 0.002 | high | Device:R | R_0402 | front |
| SW1 | button | XKB TS-1187A-B-A-B (5.1x5.1x1.5, sealed-by-membrane use) | C318884 (unv) | ~0.05 | unv | Switch:SW_Push (1,2) | Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A (4 pads 1.0x0.75, x=+-3.0, y=+-1.88; pads 1 tied, 2 tied; bbox 7.5x5.6) | front, SMT |
| D_BTN | button ESD | onsemi ESD9B5.0ST5G (SOD-923, 5 V bidirectional, IEC 61000-4-2 contact +-30 kV class, unv page) | unv | ~0.02 | unv | Diode:ESD9B5.0ST5G | Diode_SMD:D_SOD-923 | front, SMT |
| U_ACC | accelerometer | ST LIS2DW12TR | C189624 | 0.74 | ~14-16k | NONE in lib (draw hw:LIS2DW12 from Table 1) | Package_LGA:LGA-12_2x2mm_P0.5mm, 2x2x0.7 mm (pinout unverified) | front, SMT |
| TP1-7 | SWD+UART pads | 7 bare pads in a row, 2.54 mm pitch: VDD_3V6, SWDIO, SWCLK, RESET_N, GND, UART_TX, UART_RX (+100 ohm series on UART) | none | 0 | - | Connector:Conn_01x07 (or net-only) | TestPoint:TestPoint_Pad_1.5x1.5mm x7, span 15.24+1.5 = 16.7 mm (fits the 19x3 mm item) | back, no parts, pogo jig |
| BT+/BT- | battery wire pads | 2 SMD wire pads | none | 0 | - | Connector:Conn_01x02 | Connector_Wire:SolderWirePad_1x01_SMD_1x2mm (use 5x10 if the wire is AWG24; or THT pad per arch) | back, hand solder |
| D_BAT | battery ESD | in the power group (TVS1); if mine: Littelfuse/Jiangsu SMF5.0A-AE | C4944924 | 0.034 | 8982 | Diode:D (A/K) | Diode_SMD:D_SOD-123 | front |

Alternatives (max 2):
- Buzzer: none in stock-checked index; fallback CUI CPT-9019S-SMT-TR (magnetic, 9 mm, larger current, rejected: 30 mA) or TDK PS1240 (12 mm THT-free, 4 kHz, similar SPL). Pick when the 80 dBA margin below is lost.
- Accelerometer: LIS2DH12TR C110926 (0.58 USD, 18k, lib symbol Sensor_Motion:LIS2DH exists, Iq ~2 uA at 1 Hz low-power, 6 uA wake: misses IF-9 2 uA at 10 Hz); ADXL362BCCZ-R2 C579463 (270 nA wake-up mode, symbol ADXL363 differs, 1.7-2.7 USD, stock 0 or 11 USD: rejected). Clones LIS2DW12TR-MS C50385411 / -HXY C46550686 at ~0.33: do not buy (model-mpn, unknown Iq).
- LED: green LG L29K-F2J1-24-0-2-R18-Z C2899882 (stock 2123, 3.5 mcd at 2 mA); too dim, red kept.
- Button ESD: SMF5.0A-AE C4944924 (in stock, SOD-123 larger, unidirectional, up to 200 uA leakage at 5 V max: check at 3.6 V).
- Test pads: Tag-Connect TC2030-IDC-NL (6 pins, 1.27 pitch, no legs) carries VDD/SWDIO/SWCLK/RESET/GND/(SWO) only, UART would need 2 more pads; chosen row of 7 for one pogo jig.

## Findings that change architecture numbers
1. IF-7: SPL is 84 dB typ / 75 dB MIN at +-1.5 V (3 Vpp) square, 10 cm, in dB (not dBA, A-weight at 4 kHz is ~ +1 dB). One GPIO through a cap swings at most 3.6 Vpp = +1.6 dB: 85.6 typ / 76.6 min < 80: min fails. Two GPIOs in antiphase (H-bridge style, two PWM channels, a cap in each leg) give 7.2 Vpp = +7.6 dB: 91.6 typ / 82.6 min. The membrane (acoustic vent, 2-5 dB loss class, not in any datasheet) takes the min to 78-80: write IF-7 as typical >= 80 dBA at the port, or the lead asks for a membrane loss number. Needs 2 GPIOs (architecture says 17 of 32: ok, BUZ_DRV becomes BUZ_P/BUZ_N; interface.yaml signal to split). Stuck pin = DC blocked by the series caps, silent; both stuck in antiphase also silent. Caps: 220 nF each leg (series 110 nF vs 19.5 nF buzzer: -1.4 dB, already in margin above? no: subtract 1.4 -> 81.2 min before membrane). Use 470 nF to lose 0.7 dB. Resonant drive current ~ 2*C*Vpp*f = 2*19.5n*7.2*4k = 1.1 mA for 10 s; average 0.03 uA at one use a day (architecture 30 mA x 10 s is conservative). Piezo ESR: add 10 kohm across the buzzer so the coupling caps do not float (leaves 0.36 mA at 3.6 V only while driven; DC 0).
2. Sleep floor (PWR-5): LIS2DW12 0.38 uA (1.6 Hz) to 1 uA (12.5 Hz) in LP1 against 1 uA allotted: no change. LED, buzzer, button: 0 uA in sleep (button pull-up must be >= 1 Mohm: 3.6 V / 1 M = 3.6 uA only while pressed). ESD9B5.0S leakage at 3.65 V < 1 uA class (unv) on BTN: which is high only when pressed. Battery TVS leakage is a power-group item (SMF5.0A-AE up to 200 uA at 5 V: pick a part with < 0.1 uA at 3.65 V).
3. Mass: buzzer 0.5 g, accel ~0, LED/button ~0.1 g: no change to 64.2 g. Spring contacts/wires are housing parts: 2.0 g per architecture (not on the board).
4. ESD (ENV-3): the board TVS only helps if the housing contact discharges to the board; ESD9B5.0S +-8 kV contact: yes (class +-30 kV, unv page). Battery pad TVS goes in the power group.

## limits (paste into design.yaml)
```
BZ1:   limits: {abs_max_v: 12.5, rec_max_v: 7.2, source: "Murata spec JGB40-1584B p1: +-12.5 Vo-p max; drive 7.2 Vpp"}
U_ACC: limits: {abs_max_v: 4.8 (unv, ST DS11811 abs max table), rec_max_v: 3.6, source: "DS11811: Vdd 1.62-3.6 V; confirm abs max page"}
SW1:   limits: {abs_max_v: 12 (unv, XKB), source: "XKB TS-1187A: 12 VDC 50 mA rating"}
D_BTN: limits: {abs_max_v: 5.0, source: "ESD9B5.0S Vrwm 5 V"}
```
spice: none needed (buzzer = 19.5 nF; mpn fit PKLCS1212E4001-R1 for the capacitor). No SPICE for LIS2DW12.

## Floorplan facts
- buzzer: footprint above, height 3.0 mm (spec p1), front SMT, sound port facing the top-edge membrane.
- button: TS-1187A, height 1.5 mm (arch; XKB drawing not fetched: placeholder, check pad span 6.0 x 3.76 against the XKB drawing).
- led: 0603, 0.8 mm; accel LGA-12 0.7 mm (unv drawing); test pads 0.1 mm back; wire pads back, hand solder.
No connector entry (no envelope_mm).

## Risks
- Mechanical drawings NOT checked: TS-1187A (XKB), LIS2DW12 (ST), Wurth LED are from KiCad libs only = placeholders for the floorplan gate; ST datasheet fetch failed (timeout).
- No LIS2DW12 symbol; pinout from memory/search only: do not bind before Table 1 is read.
- Buzzer and button LCSC numbers/stock not verified live (parts index has no buzzer or switch category). Single source for the buzzer (Murata).
- 80 dBA is not shown at the port: membrane loss unknown; the datasheet unit is dB.
- Operating temp: PKLCS -20..70 C ok with ENV-1; TS-1187A and LIS2DW12 (-40..85) ok.
## Unit cost
Group C BOM ~ 0.6 + 0.01 + 0.27 + 0.05 + 0.02 + 0.74 = 1.7 USD (architecture had 0.6 + 0.8 + 0.1 + 0.05 + part of TVS 0.3 = 1.85): no change to COST-1.

---
# FIX PASS 2026-11-01
U_ACC LIS2DW12 supply (VDD, VDD_IO) = VDD_AON 3.0 V (v_max 3.06 V <= 3.6 V rec max), not VDD_3V6 (4.12 V would exceed 3.6 V): limits unchanged. Button pull-up and TPL5010 also on VDD_AON. Q shifter BSS138LT1G: parts-power.md G4. No other change.

## Stage-5 review fixes (2026-11-01)
- Buzzer drive: VDD_GPIO is 3.0 V (VDD_AON), not 3.6 V: two antiphase legs give 6.0 Vpp (not 7.2): +6.0 dB over the 3 Vpp spec point -> 90.0 dB typ / 81.0 dB min before the 470 nF coupling (-0.7 dB) and the membrane. P0.28/P0.29 configured high drive (H0H1) so the 150 ohm + 470 nF legs reach the rails at 4 kHz.
- Test-pad ESD (ENV-3, lead decision): 2x TI TPD4E05U06DQAR (USON-10 2.5 x 1.0, 4 ch, VRWM 5.5 V, IEC 61000-4-2 +-12 kV contact, 0.5 pF class, leakage 10 nA typ per channel; max to verify on SLVSBK9), ~0.15 USD each (TI/DigiKey 1k; LCSC number not confirmed in this pass). U10: VDD_4V0, SWDIO, SWCLK, RESET_N; U11: UART_TX, UART_RX. Sleep leakage 6 x 10 nA = 0.06 uA typ at the rail (x1.465 = 0.09 uA battery side, inside PWR-5 22 uA). Back face, next to TP1-7.
