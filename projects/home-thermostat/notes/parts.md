# Parts: home-thermostat (stage 3)

Prices are USD at quantity 10 from the LCSC product page unless marked. Checked 2026-10-19. The hwflow parts index (jlcsearch) returned only the 100 highest-stock rows per category and ignored filters, so parts were checked on LCSC pages, DigiKey and Mouser. Facts from datasheets are in `notes/datasheets/`.

## BOM (per unit, qty 10)

| Ref | Block | Part (MPN) | LCSC | $ each | Qty | $ | Stock | KiCad symbol | KiCad footprint |
|---|---|---|---|---|---|---|---|---|---|
| U1 | Radio + MCU | ESP32-C3-MINI-1-N4 | C2838502 | 2.70 | 1 | 2.70 | 2,637 | thermostat:ESP32-C3-MINI-1 (project lib) | thermostat:ESP32-C3-MINI-1 (project lib) |
| U2 | Boost 3.3 V | TPS610994YFFR | none (DigiKey, Mouser) | 1.21 (DigiKey qty 10) | 1 | 1.21 | in stock at DigiKey, Mouser | thermostat:TPS610994YFF (project lib) | Package_BGA:Texas_DSBGA-6_0.855x1.255mm_Layout2x3_P0.4mm_LevelB |
| L1 | Boost inductor 2.2 uH | Chilisin LVH252A12-2R2M-N (1.9 A sat, 80 mOhm, 2.5 x 2.0) | C285849 | 0.0575 | 1 | 0.06 | 1,350 | Device:L | Inductor_SMD:L_1008_2520Metric |
| C1 | Buffer 4.7 mF 10 V | Panasonic EEU-FS1A472 (D12.5 x L25, 5 mm pitch, lying flat) | C407972 (LCSC stock 0); DigiKey EEU-FS1A472B | 1.06 (DigiKey qty 10) | 1 | 1.06 | DigiKey 1,500; LCSC 0 | Device:C_Polarized | Capacitor_THT:CP_Radial_D12.5mm_P5.00mm |
| M3 | Reverse-polarity NMOS | AOS AO3416 (SOT-23, 20 V, 21/34 mOhm at VGS 1.8 V) | C43307 | 0.1094 | 1 | 0.11 | 57,450 | Device:Q_NMOS_GSD | Package_TO_SOT_SMD:SOT-23 |
| U6 | EN supervisor | TI TPS3839K33DBZR (2.93 V, 150 nA, 200 ms) | C96333 | 0.6457 | 1 | 0.65 | LCSC 120; DigiKey 3,227 | Power_Supervisor:TPS3839DBZ | Package_TO_SOT_SMD:SOT-23 |
| Y1 | 32.768 kHz | Seiko SC-32S 12.5 pF, +/-20 ppm | C97606 | 0.2043 (qty 5) | 1 | 0.20 | 57,605 | Device:Crystal | Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm |
| C2, C3 | Crystal load caps | Samsung CL10C180JB8NNNC 18 pF C0G | C1647 | 0.012 | 2 | 0.02 | 8,500 | Device:C | Capacitor_SMD:C_0603_1608Metric |
| U3 | 2-ch 24-bit ADC | Chipsea CS1238 (SOP-14) | C81829 | 0.91 | 1 | 0.91 | 9,779 | thermostat:CS1238 (project lib) | Package_SO:SOIC-14_3.9x8.7mm_P1.27mm |
| R1, R2 | NTC reference 47 k 0.1 % 25 ppm | RESI PTFR0603B47K0P9 | C328427 | 0.1171 | 2 | 0.23 | 2,880 | Device:R | Resistor_SMD:R_0603_1608Metric |
| R3 | VBAT divider top 200 k 0.1 % 25 ppm | Yageo RT0603BRD07200KL | C728585 | 0.048 | 1 | 0.05 | 229,740 | Device:R | Resistor_SMD:R_0603_1608Metric |
| R4 | VBAT divider bottom 100 k 0.1 % 25 ppm | Yageo RT0603BRD07100KL | C122538 | 0.04 (estimate; 0.0326 from qty 20) | 1 | 0.04 | 1,103,720 | Device:R | Resistor_SMD:R_0603_1608Metric |
| K1, K2 | Relays | TE Schrack PE014F03 (1415390-1) | none | 9.00 (DigiKey qty 1) | 2 | 18.00 | DigiKey stock not read | thermostat:Relay_TE_PE014F03 (project lib) | thermostat:Relay_TE_PE014_PROVISIONAL (project lib) |
| U4, U5 | Relay H-bridges | JSMSEMI DRV8837DSGT-JSM (WSON-8 2x2) | C22447133 | 0.1401 (qty 5) | 2 | 0.28 | 2,615 | Driver_Motor:DRV8837 | Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm |
| J1 | 230 V terminals | Cixi Kefa KF301-5.0-2P (250 V, 17 A, 10 mm tall, 1.5 mm2) x2 | C474881 | 0.1023 | 2 | 0.20 | 39,400 | Connector:Screw_Terminal_01x02 | TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2_1x02_P5.00mm_Horizontal |
| J2 | Probe terminals | Cixi Kefa KF250-3.5-2P-1 (spring, 18-22 AWG) x2 | C475154 | 0.1189 | 2 | 0.24 | 3,620 | Connector:Screw_Terminal_01x02 | TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal (check pad drill) |
| BT1 | 2 x AAA holder, wire leads | Keystone 2469 (review-2 M2 decision, replaces the 2468: same 53.0 x 24.6 x 13.0 mm body, red = +, black = -, M65 catalog p.28; adhesive on the front face; footprint thermostat:BatteryHolder_Keystone_2469_Wire: pad 1 (+, square) and pad 2 (-), 1.0 mm drill, 2.0 mm pad, 5 mm apart, F.CrtYd/F.Fab 53 x 24.6 mm) | none (DigiKey, Mouser; price kept at the 2468 value, confirm) | 1.18 (DigiKey qty 10) | 1 | 1.18 | DigiKey 3,176 | Device:Battery_Cell | thermostat:BatteryHolder_Keystone_2469_Wire (pad 1 = + red lead) |
| SW1 | Button | XKB TS-1187A-B-A-B | C318884 | 0.0207 (qty 20) | 1 | 0.02 | 240 shown | Switch:SW_Push | Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A |
| D1 | LED | Hubei KENTO KT-0603R red 0603 (basic part; stage 4, R19 lever 3) | C2286 | 0.0073 | 1 | 0.01 | 7,629,322 | Device:LED | LED_SMD:LED_0603_1608Metric |
| M4, R17 | VBAT divider high-side switch (stage 4) | AO3401A + 1 M 0603WAF1004T5E | C15127 + C22935 | 0.0716 + 0.002 | 1 + 1 | 0.07 | 700,438 / basic | Transistor_FET:AO3401A, Device:R | SOT-23, R_0603 |
| R16, C21 | M3 gate hold (stage 4) | 100 k 0603WAF1003T5E + 1 uF CL10A105KB8NNNC | C25803 + C15849 | 0.002 + 0.004 | 1 + 1 | 0.01 | basic | Device:R, Device:C | R_0603, C_0603 |
| D2-D4 | ESD, 2 probes + button | Leiditech ESD5Z5CL (SOD-523, 5 V, +/-8 kV contact, +/-15 kV air) | C384854 | 0.0628 | 3 | 0.19 | 17,040 | Device:D_TVS | Diode_SMD:D_SOD-523 |
| J4 | Board-to-board | female 2x5 8.5 mm CJT A2541HWV-2x5P (rear) + male 2x5 CJT A2541WV-2x5P (front) | C239345 + C225520 | 0.2401 + 0.0879 | 1 + 1 | 0.33 | 2,350 / 11,395 | Connector_Generic:Conn_02x05_Odd_Even | Connector_PinSocket_2.54mm:PinSocket_2x05_P2.54mm_Vertical / Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical |
| - | Passives | 100 nF C14663 x12, 10 uF C19702 x4, 1 uF C15849 x4, 10 k C25804 x10, 1 k etc. | see left | 0.005-0.027 | about 30 | 0.36 | millions | Device:C, Device:R | 0603 |
| J5 | Programming pads | test pads, no part | - | 0 | 6 | 0 | - | Connector:TestPoint | TestPoint:TestPoint_Pad_D1.0mm |
| **Total** | | | | | | **27.93** (`hwflow build`, design.yaml prices, stage 4; includes M1, M2, D1 etc. not shown in the table) | | | |

**Stage 4 update (circuit, 2026-10-19): BOM 27.93 USD per board, under the 28 USD limit by 0.07 USD.** Changes: D1 Wurth 0.2725 -> KT-0603R C2286 0.0073 (lever 3 applied, -0.265; LCSC page price 1-999 pcs, basic part, same 0603 footprint and Device:LED pin 1 = K); M2 AO3401A price corrected 0.02 -> 0.072 (LCSC C15127 1-99 pcs 0.0716, +0.052); new M4 AO3401A 0.072 + R17 1 M 0.002 (VBAT divider high-side switch, R17 tap <= VDD); new R16 100 k 0.002 + C21 1 uF 0.004 (M3 gate hold, R3). Net -0.14. R17 1 M (C22935) and R16 (C25803) prices are the usual basic-part price, not re-read on the LCSC page (the jlcsearch index did not list them). Levers 1, 2 and 4 below stay open.
**Iteration 3 (2 x AAA) BOM: 28.07 USD per board; limit 28 USD (conflict C14, answer q3): over by 0.07 USD.** The table rows above are the 2026-10-19 review-1 state with the iteration-3 rows replaced; the total is from `hwflow check`. Without the relays the BOM is 10.07 USD. Levers (PM to choose, none applied): (1) C1 from LCSC C407972 at 0.8492 when restocked (-0.21) or Rubycon 10YXJ4700M12.5X25 at 0.87 (DigiKey, out of stock, lead time 24 weeks) (-0.19); (2) U6 at DigiKey about 0.5 instead of 0.646 (-0.15, second supplier); (3) D1 Wurth 0.27 to a basic 0603 LED about 0.02 (-0.25, no function change); (4) a qty-10 relay quote below 9.00 (not obtainable, DigiKey 9.00 at qty 1; TE lists the part as "not currently available").
Changes this iteration (price, USD each): BT1 0.93 -> 1.18 (2468, +0.25); C1 0.52 -> 1.06 (+0.54, higher voltage and longer can); M3 0.10 -> 0.109 (AO3416); U6 0.45 -> 0.646 (LCSC price, not the DigiKey estimate); R3 0.09 -> 0.048, R4 0.09 -> 0.04 (Yageo thin film 0.1 %, 25 ppm, same family for ratio tracking). Net +0.90.

## Per-block choices, alternatives and reasons

**U1 ESP32-C3-MINI-1-N4.** Only certified Wi-Fi module with a PCB antenna in this size class on LCSC (R10), 4 MB flash covers OTA (R21), deep sleep 5 uA. KiCad has no symbol (only `RF_Module:ESP32-C3-WROOM-02`, different pinout and footprint), so the project library has one (pin numbers from datasheet table 3-1). Alternatives: ESP32-C3-MINI-1-N4X (rev v1.1, same footprint, no LCSC stock found), ESP32-C3-WROOM-02 (KiCad symbol exists, 18 x 20 mm, too large for the front board). Datasheet: https://documentation.espressif.com/esp32-c3-mini-1_datasheet_en.pdf. Risk: the -N4 uses chip rev v0.4, marked NRND.

**U2 TPS610994YFFR.** Fixed 3.3 V, 0.7 V start, Iq 0.4 + 0.6 uA, 0.8 A min switch limit (R3). The DRV package of the fixed version does not exist in stock anywhere checked, so the part is the 0.88 x 1.23 mm 6-ball DSBGA, 0.4 mm pitch. Alternatives: TPS61099YFFR adjustable (LCSC C91091, $1.70, 1,920 in stock, needs a 3.3 V feedback divider that leaks at least 0.3 uA at 10 Mohm total; rejected), TPS610997YFFR 5 V (C2072359, wrong voltage). Datasheet: https://www.ti.com/lit/gpn/tps61099. Mounting: JLCPCB can place 0.4 mm DSBGA only with fine-pitch options; plan hand assembly for 10 prototypes.

**L1 2.2 uH.** Datasheet range 0.7-2.86 uH. Chilisin 1.9 A saturation against 1.25 A max peak limit (R3 margin 52 %), 80 mOhm. Alternatives: Sunlord SWPA252010S2R2MT (C36399, page not readable), Wuerth WE-LQS 252012 (1.8 A, 80 mOhm).

**C1 4.7 mF buffer (iteration 3).** Panasonic EEU-FS1A472 (FS series, 4700 uF 10 V, 12.5 x 25 mm, 5 mm pitch, 15 mOhm at 100 kHz, tan delta 0.19 -> 0.054 ohm at 120 Hz, 10,000 h at 105 C). Chosen because it is the only 10 V, D12.5, 4700 uF part found with stock (DigiKey EEU-FS1A472B 1,500, $1.06 at qty 10); LCSC C407972 has 0 stock ($0.85). Length 25 mm instead of 20 mm: layout must give 25 x 12.5 mm lying flat. Leakage: no series meets "<= 1 uA at 3.3 V, 24 h" by datasheet. The aluminium electrolytic rule is 0.01 CV or 3 uA (greater), 2 min at rated voltage: 0.01 x 4700 x 10 = 470 uA max (155 uA if scaled to 3.3 V, which the datasheet does not claim); after 24 h at 33 % of rated voltage the leakage is typically well under 1 uA but not specified, so the 1 uA budget is a bench item (3 samples, state.md). The FS datasheet PDF was not readable (specs from distributor pages, `notes/datasheets/eeu-fs1a472.md`). Alternatives: Rubycon 10YXJ4700M12.5X25 (YXJ, 0.01 CV or 3 uA, 32 mOhm, DigiKey $0.87 at qty 10 but out of stock, 24-week lead time), Nichicon UVK1A472MHD (12.5 x 25, leakage 3 uA or 0.01 CV). Supercaps rejected (ESR 10-50 ohm against the 0.1 ohm limit).

**Y1 + C2/C3.** CL = 12.5 pF: with 3 pF stray, each load cap = 2 x (12.5 - 3) = 19 pF, so 18 pF C0G. Seiko SC-32S: +/-20 ppm, 3.2 x 1.5 mm. The parabolic coefficient (-0.034 ppm/K^2) is the typical tuning-fork value and is not read from the SC-32S datasheet (https://www.lcsc.com/datasheet/C97606.pdf). Alternatives: KYX KFC3276812520T (C18209174, 20 ppm 12.5 pF), Micro Crystal CC7V-T1A (C7462183, out of stock).

**U3 CS1238.** Only 24-bit ratiometric 2-channel ADC on LCSC at under 1 USD. The index has no KiCad symbol (only HX711, different pinout), so there is a project symbol. Alternatives: two CS1237 (C77801, SOP-8, 1 channel each, about 2 x 0.6 USD), the internal ESP32-C3 ADC (rejected in architecture 5.1: 0.68 degC). Datasheet still not readable as text: see `notes/datasheets/cs1238.md`. Iteration 3: the pin map 1-14 agrees with the project symbol (second-hand source, no pin change); REFIN 1.5 V to VDD + 0.1 V; operating current and the common-mode range by PGA remain unverified.

**R1-R4 resistors.** 0.1 % thin film 0603: RESI PTFR0603B47K0P9 is 25 ppm/K, which matches the error budget (0.015 degC). Alternatives: Yageo RT0603BRD0747KL, Vishay MCT0603 (RS 1701895). 100 k: Uniroyal TC0325B1003T5E (C54345), Susumu RG1608P-104-B-T5 (C328672), Yageo RT0603BRD07100KL (C122538).
**R3/R4 VBAT divider (iteration 3): 200 k / 100 k, 0.1 %, 25 ppm.** Yageo RT0603BRD07200KL (C728585, $0.048 at qty 10, 229,740 in stock) over RT0603BRD07100KL (C122538, 1,103,720 in stock, 0.0326 from qty 20): same family, same 25 ppm, so the ratio drift is under 25 ppm/K difference. Divide by 3: tap 0.73-1.2 V for the 2.2-3.6 V pack. The previous RESI PTFR0603B100KN9 (C394537) costs $0.1381 at qty 10 (not 0.09). Alternatives for 200 k: Yageo RT0603BRE13200KL (C6207722, 50 ppm); 100 k: RESI PTFR0603B100KN9 (10 ppm). Datasheets not read (LCSC pages only). R13 680 ohm: Uniroyal 0603WAF6800T5E, C23228, 1 %, $0.0021, 1.4 M in stock.

**K1, K2 relays.** PE014F03 is the architecture's choice (10 mm tall, 3 V coil, 41 ohm, 10.6 mJ per pulse), but it is not on LCSC and DigiKey lists it at 9.00 USD at quantity 1. Alternative found: Panasonic DE1A-L-3V (LCSC does not stock the 3 V version; the 5 V DE1A-L is C1565546), single coil latching, 1 form A, 10 A 250 VAC, 90 ohm, 100 mW, creepage and clearance >= 8 mm coil to contact (reinforced, would resolve C12 without the screw cover), 25 x 12.5 x 12.5 mm. Set voltage max 2.1 V; the pulse energy is about 4.6 mJ (3.22 V^2 / 90 ohm x 40 ms), less than half of the PE. Price: IBS Electronics $11.23 (qty 1-39), not better. The datasheet for the DE series could not be read (PDF blocked), so its pin layout and exact contact ratings are not confirmed; the area (25 x 12.5 x 2) is 56 % larger than the PE. Third option: Hongfa HF3F-L, 15.5 mm tall (rejected, depth). Decision for the PM in conflicts C14.

**U4, U5 H-bridges.** One DRV8837 per relay (1.8-7 V VM, nSLEEP, sleep current 45 nA VM + 17 nA VCC at the MSKSEMI page; the TI datasheet was not read). Two single bridges at 0.28 USD replace the dual-bridge idea: DRV8833 has VM >= 2.7 V and 1.6 uA sleep, which would add 1.5 uA at the rail (+2 uA of average current, R2). Alternatives: MSKSEMI DRV8837DSGR-MS (C50385422, 2,590 in stock), TI DRV8837DSGR (original), 4 x AOS AO6604 complementary pairs (C47744, out of stock). Symbol `Driver_Motor:DRV8837` pins: 1 VM, 2 OUT1, 3 OUT2, 4 GND, 5 IN2, 6 IN1, 7 ~SLEEP, 8 VCC, 9 EP. Risk R-DRV-1: nSLEEP needs a GPIO (see risks).

**J1, J2, BT1, SW1, D1, TVS, J4.** Terminals: KF301 (10 mm, 250 V, 17 A) meets R15 for 230 V; the 5.0 mm pole pitch has 5.0 mm creepage between adjacent poles at 230 V (functional spacing). Spring terminals 18-22 AWG (0.33-0.82 mm2) do not fully cover R15's 0.2-1 mm2. Alternatives: DG128-5.0-02P (14.1 mm tall, rejected), JILN JL212V-SMT-35002BP1 (C5118798, 0.2-1.5 mm2, SMD vertical). **BT1 (iteration 3): Keystone 2468, 2 x AAA, THT PC pins** (DigiKey 36-2468-ND $1.18 at qty 10, 3,176 in stock; no LCSC listing; Mouser 534-2468; Farnell EUR 1.88 at 10). Catalog drawing (M65p28 p.28, `notes/datasheets/keystone-2468.md`): 53.0 x 24.6 x 13.0 mm high, PC pins on the short end 12.7 mm apart, 3 mounting posts of 2.9 mm. KiCad footprint `Battery:BatteryHolder_Keystone_2468_2xAAA` exists (pads 1 and 2 at 12.7 mm, 2 NPTH 3.45 mm, bbox 54.0 x 25.6 mm). Not keyed. Fallback per architecture: AAA clips, block <= 23.4 mm wide. Alternatives: Keystone 2468RB (with ribbon), Keystone 2469 (wire leads, 6 in), MYOUNG BH-AAA-style wire-lead holders. ESD: ESD5Z5CL lists 1 uA leakage at 5 V; at 1.3 V it is far lower but not specified, so measure against the 20 nA limit; alternatives: Littelfuse SP0402B-ULC-01ETG (C468904, 25 nA), AZ5725-01F (C385877). Battery reverse protection: M3 low-side NMOS AO3416 (below).

## SPICE model parameters (for the circuit stage)

| Ref | spice.source | Model | Parameters |
|---|---|---|---|
| U2 | behavioral | `models/tps610994_avg.lib` subckt `TPS610994_AVG` ports `vin out gnd` (the vendor TPS61099 PSpice model is encrypted and not usable in ngspice) | VOUT 3.3, EFF 0.85 (wake) or 0.65 (10 uA sleep), ILIMIN 0.65 (0.8 A min peak - ILH/2), 0.85 typ, IQVIN 0.4u, IQVOUT 0.6u, VUVLO 0.65. Not yet run in ngspice (command needs approval), run it in `hwflow build --until sim` |
| C1 | none (capacitor with `esr`, `esl`) | - | C 4.7m, esr 0.054 at 20 C from tan delta 0.19 (120 Hz) / 15 mOhm at 100 kHz, design.yaml uses 0.10 for 0 C (2x, estimate), leakage shunt 1.1 Mohm (3 uA at 3.3 V, pessimistic estimate; spec only gives 470 uA at 10 V after 2 min) |
| M3 | fit | `models/nmos_lowvgs.lib` NMOS_LOWVGS (typ), NMOS_LOWVGS_MAX (max); ports D G S | AO3416 Rev 5.1 p.2: RDS(on) 16/18/21 mOhm typ and 22/26/34 mOhm max at VGS 4.5/2.5/1.8 V; Vth 0.4/0.7/1.1 V; Ciss 1295, Coss 160, Crss 87 pF; VSD 0.62 V at 1 A. Level-1 fit reproduces the three RDS points within 0.1 mOhm. Not run in ngspice (needs approval) |
| U6 | behavioral | `models/tps3839_beh.lib` TPS3839_BEH ports `gnd rst vdd`, params VIT 2.93 HYS 0.029 TD 200m IQ 150n ROUT 200 | SBVS193D p.7-8: VIT- 2.857/2.93/2.974 V, hys 29 mV, IDD 150 typ / 500 nA max, td 120/200/350 ms, push-pull. Corners: VIT 2.857 / 2.974, TD 120m / 350m, IQ 500n. TI PSpice SBVM235 exists (zip, not unpacked). Model rewritten (delay was 150 us, wrong), not run in ngspice (needs approval) |
| K1, K2 | spice: false, coil as R + L | - | R 41 ohm +/-10 %, L about 0.1 H (estimate), set/reset 2.25 V min |
| U4, U5 | behavioral (R switches) | - | Ron 0.65 ohm (HS + LS), VM 3.3 V |
| Y1 | spice: false | - | f 32.768 kHz, CL 12.5 pF, tolerance 20 ppm, temp coefficient -0.034 ppm/K^2 (typical) |
| U1 | spice: false | current sources in the testbench | sleep 5 uA, light sleep 130 uA, modem-sleep 13-22 mA, TX 350 mA (module datasheet table 6-4, 6-6, 6-7) |
| U3 | spice: false | - | supply current: confirm, architecture uses 1.5 mA |

## Power budget re-check with real part data

| Item | Architecture | Parts stage | Delta on the 54.99 uA average |
|---|---|---|---|
| Module deep sleep | 5.0 uA | 5 uA typ (table 6-7) | 0 |
| Boost IQ | included in the 65 % at 6.6 uA | 1.0 uA typ, 2.6 uA max (0.4 + 0.6 typ, 1.1 + 1.5 max) | 0 typ; +1.6 uA at the cell side at max |
| C_buf leakage | 1.0 uA | 1-5 uA typ (not specified) | +0 to +5.2 uA at 3 uA: +2.6 uA (57.6 uA) |
| H-bridges standby | 0.1 uA | 0.12 uA (2 x 62 nA, MSKSEMI page) | +0.03 uA |
| TX peak | 335 mA | 350 mA (module) | R3 already uses 350 mA |
| CS1238 supply | 1.5 mA assumed | unconfirmed | 0 |

At 3 uA of buffer leakage the average becomes 57.6 uA: alkaline 13.4 months, lithium 2.18 years, both above the R2 life targets, but above the 55 uA acceptance limit (margin used up). At 5 uA the average is 60.2 uA and the alkaline life 12.9 months, below 13 (R2 fails). The sleep floor and the leakage must be measured at bring-up.

## Risks

0. Iteration 3 risks: (a) BOM 28.07 USD vs 28 USD limit (levers in the BOM section); (b) TE lists 1415390-1 as "not currently available"; (c) C1 leakage has no datasheet bound at 3.3 V (470 uA at 10 V, 2 min); LCSC C1 stock is 0, DigiKey only; (d) U6 stock 120 at LCSC (DigiKey 3,227); (e) TPS3839 IDD max is 500 nA (not 150), and td is 120-350 ms; (f) AO3416 VGS(th) max 1.1 V (spec was 1.0), RDS(on) at 1.8 V is guaranteed 34 mOhm max; (g) Keystone 2468 has no LCSC listing (DigiKey, Mouser) and its footprint has 2 mounting holes where the catalog shows 3.
1. R-REL-1 (high): relay footprint pad coordinates are provisional (consistent with the TE figure to one 2.5 mm grid step); TE DXF needed before layout. DE1A-L-3V pinout unknown.
2. C14 (cost): relays cost 18 USD of 28.07 USD; no LCSC stock; single source (TE at DigiKey/Mouser).
3. C15 (battery life): C_buf leakage unspecified at 3.3 V; one measurement decides R2.
4. R-TPS-1: 4.7 mF is 47 times the datasheet maximum COUT (100 uF); only 0.4 mm DSBGA package exists (hand assembly); single source (TI).
5. R-DRV-1: DRV8837 needs nSLEEP (45 nA vs several uA awake). All 15 GPIOs are used. Option: share one nSLEEP line on IO18 or on IO21 (move the VBAT divider enable). Without it the bridges stay awake and the 3V3 rail gains their quiescent current (datasheet not read: unknown).
6. CS1238: Chipsea PDF still not readable; pin map agrees with the symbol (second-hand), operating current and common-mode range unconfirmed.
7. Stock: C1 is out of stock at LCSC (DigiKey 1,500) and the Keystone 2468 is not at LCSC (DigiKey 3,176). ESP32 module 2,637, CS1238 9,779 are fine.
8. The ESP32 module needs a supply that can deliver 0.5 A (table 6-2). The end-of-life cell (Voc 1.15 V, 0.3 ohm) at the 0.65 A average input limit gives (1.15 - 0.195) x 0.65 x 0.85 = 0.53 W, which is 0.16 A at 3.3 V; the 150 mA wake load fits, and the 4.7 mF buffer covers the 350 mA TX peak for 5 ms. A continuous 0.5 A supply is not available.
9. IO2 strapping against the VBAT divider (see esp32 notes).
10. SC-32S temperature coefficient not read from its datasheet.
11. Project library: `lib/symbols/thermostat.kicad_sym` (ESP32-C3-MINI-1, TPS610994YFF, CS1238, Relay_TE_PE014F03) and `lib/footprints/thermostat.pretty` (ESP32-C3-MINI-1, Relay_TE_PE014_PROVISIONAL). The module footprint is derived from datasheet figures, check it against the Espressif DXF.

## Review-1 changes (stage 5)

**Reverse-polarity protection (M2).**
- Keyed-holder option rejected: KiCad has only one 1xAA holder footprint (`Battery:BatteryHolder_Keystone_2460_1xAA`). The 2460 has no documented reverse-insertion rib, and LCSC/DigiKey pages could not be read this session.
- Chosen: M3, a low-side NMOS ideal diode (`Device:Q_NMOS_GSD`, SOT-23).
  - Drain to the cell negative (BAT_N), source to GND, gate to the cell + terminal (VBAT).
  - Reversed cell: VGS < 0 and the body diode is reverse biased. Simulated cell current 5 pA (reverse_dc).
- Iteration 3 purchase spec (2 x AAA, VGS = whole pack, at least 2.0 V under load): VDS >= 12 V, VGS max >= +/-8 V, RDS(on) <= 50 mOhm at VGS 1.8 V (datasheet), VGS(th) max <= 1.0 V, SOT-23 G-S-D.
- **M3 = AOS AO3416 (LCSC C43307, $0.1094, 57,450 in stock), datasheet Rev 5.1 read** (`notes/datasheets/ao3416.md`): VDS 20 V, VGS +/-8 V, RDS(on) 21 typ / 34 max mOhm at VGS 1.8 V (ID 5 A), 18 / 26 at 2.5 V, VGS(th) 0.4 / 0.7 / 1.1 V, Ciss 1295 pF, G-S-D pins 1-2-3. Meets every line of the spec except VGS(th) max 1.1 V (spec 1.0 V); the guaranteed 34 mOhm at 1.8 V is what the circuit needs. Drop at 0.65 A: 22 mV at the 34 mOhm max. Alternatives: Vishay Si2312CDS (Vth 0.4-1.0 V, not read), Diodes DMN2056U (not read), third-party AO3416 copies (BORN C5148471, PUOLOP C479060, about $0.02, unverified silicon). The old candidate DMN1019USN-7 is not used.
- Replaces the 1-cell spec (RDS(on) at VGS 1.2 V and 0.9 V, VGS(th) <= 0.6 V) that no SOT-23 part met.

**EN supervisor (M3).**
- U6 TPS3839K33DBZR, SOT-23, KiCad `Power_Supervisor:TPS3839DBZ` (pins 1 GND, 2 ~RESET, 3 VDD); datasheet SBVS193D read pp. 1-8 (`notes/datasheets/tps3839.md`): VIT- 2.857/2.93/2.974 V, hys 29 mV, IDD 150 typ / 500 nA max, td 120/200/350 ms, push-pull. LCSC C96333 ($0.6457 at qty 10, stock 120); DigiKey 3,227 in stock.
- Alternatives (not read): TPS3839G33DBZR (3.08 V threshold, higher release above 3.0 V ESP32 minimum), TPS3809K33DBVR (6-15 uA class, rejected by the sleep budget), TLV809K33.
- 150 nA Iq, push-pull active-low (the review asked for open drain; a push-pull output feeding R5 works the same and draws no pull-up current in reset).
- C20 100 nF decouples it.
- Alternatives with KiCad symbols are all uA-class (MCP100, MAX811/CAT811 about 6-15 uA), which the sleep budget rejects.

**Other new parts.**
- R15 10 k and C19 10 nF: M2 slow gate (S2).
- RV1/RV2: TDK S07K275 MOV footprints, DNP (S4).
- TP7: IO21 TXD0.
- BOM cost was 27.16 USD after review 1; 28.07 USD after iteration 3 (see the BOM section).

## Verification log, iteration 3 (2026-10-19)

| Item | Result |
|---|---|
| TPS3839K33 (SBVS193D pp.1-8) | Done. Real values replace memory values: delay 200 ms (was 150 us), IDD max 500 nA, threshold 2.857/2.93/2.974 V; push-pull confirmed. TI PSpice model SBVM235 exists but could not be unpacked. |
| AO3416 (Rev 5.1 pp.1-3) | Done. Fit in `models/nmos_lowvgs.lib` (typ and max); source: fit. |
| CS1238 | Partial. Pin map matches the symbol (no change); operating current and common mode still not found; Chipsea PDF not retrievable. |
| PE014F03 pads | Not done. TE figure gives only a 2.5-2.54 mm grid; provisional pads consistent to one step; TE page says the part is not currently available; name stays PROVISIONAL; layout blocked. |
| TPS61099 (SLVSD88M pp.12-16) | Done. Down mode from VIN > VOUT - 50 mV, pass-through above VOUT + 0.5 V (3.8 V), exit at 103 % VOUT; no min/max given; vendor PSpice model encrypted (unchanged). |
| Keystone 2468 | Done from the catalog page; mounting-post coordinates not given. |
| C1 | Part chosen; leakage at 3.3 V not specified by any datasheet; bench only. |

## S1 fix parts (review-2 S1, 2026-10-19)
- New R20 6.8 M 1 % 0603, UNI-ROYAL 0603WAF6804T5E. R18 changes 1 M -> 3.3 M 1 % 0603, UNI-ROYAL 0603WAF3304T5E. R19 changes 10 k -> 100 k (0603WAF1003T5E, C25803, the same part as R16). C23 is unchanged (10 nF CL10B103KB8NNNC, C57112); only its pad 2 net changes to GND.
- LCSC numbers for 0603WAF6804T5E and 0603WAF3304T5E are not filled in, because the parts search was offline (urlopen failed). Both are UNI-ROYAL 0603 1 % thick film (basic-part family, about 0.002 USD). Confirm the C-numbers and stock before ordering.
- Resistor data that matters: 1 % tolerance, about 100 ppm/K and 75 V working voltage (typical UNI-ROYAL WAF series figures; the datasheet was not read this session). Each part sees <= 3.6 V. The MC run uses the design tolerances: VIN min +0.254..+0.294 V over 50 runs.
- No Schottky is added. Option b was rejected on analysis (notes/circuit.md "S1 fix"), so no Schottky datasheet facts are recorded.
- BOM: +0.002 USD (R20). The other changes are value swaps at the same price. Total 27.94 USD per board.
