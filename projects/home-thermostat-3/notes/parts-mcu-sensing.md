# Parts: MCU + SENSING + UI (stage 3)

Prices are USD at qty 10 (LCSC page, fetched 2026-10-20) unless marked "idx" (jlcsearch index unit price). Stock from the same pages. PDF text extraction (pdftotext) was blocked in this session, so datasheet numbers come from the Espressif/TI HTML datasheet pages and LCSC pages. Items marked (recall) are from memory of the TI datasheet and must be checked at the page named in the "page" column.

## Table

| Ref | Role | Part (mpn) | LCSC | Price | Stock | KiCad symbol | KiCad footprint | H mm | Key numbers (datasheet section/page) |
|---|---|---|---|---|---|---|---|---|---|
| U1 | Wi-Fi SoC module | ESP32-C3-MINI-1-H4X (alt -H4 C2934569, -N4X) | C41349510 | 3.08 idx (H4: 2.76) | 1541 (H4: 3919) | **none in KiCad** (no ESP32-C3-MINI-1; MCU_Espressif:ESP32-S3-MINI-1 is a different pinout) | **none** (RF_Module:ESP32-S2-MINI-1 is 15.4 x 20.5, wrong) | 2.4 +-0.15 | 16.6 x 13.2 x 2.4 (s10.1 p35); 3.0-3.6 V (s6.2 p21); deep sleep RTC timer + RTC mem 5 uA typ (Table 6-7); 40 MHz crystal +-10 ppm (s8 p32); HBM 2 kV (s12.2); 53 pins, 15 GPIO |
| Y1 | 32.768 kHz crystal | Micro Crystal CM7V-T1A-32.768KHZ-6PF-20PPM-TA-QC | C5366534 | 0.330 | 6031 | Device:Crystal | Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm (pads 1,2) | 0.9 (3.2 x 1.5) | CL 6 pF, +-20 ppm at 25 C, ESR 70 k max, -40..85 C, AEC-Q200 |
| C_Y1a/b | crystal load caps | 5.6 pF C0G 0402, 2 pcs | choose in stage 4 (not looked up) | 0.01 | - | Device:C | Capacitor_SMD:C_0402_1005Metric | 0.5 | CL 6 pF = (C1 x C2)/(C1 + C2) + Cstray: C = 2 (6 - 3) = 6 pF for Cstray = 3 pF; 5.6 pF used, trim on the bench |
| U3 | 16-bit ADC | TI ADS1115IDGSR | C37593 | 1.07 (1+: 1.23) | 4227 | Analog_ADC:ADS1115IDGS (pins 1 ADDR, 2 ALERT, 3 GND, 4 AIN0, 5 AIN1, 6 AIN2, 7 AIN3, 8 VDD, 9 SDA, 10 SCL) | Package_SO:VSSOP-10_3x3mm_P0.5mm (pads 1-10; the symbol default is TSSOP-10_3x3mm, same land) | 1.1 max (VSSOP) | VDD 2.0-5.5 V; 150 uA run; offset +-3 LSB single-ended (+-1 LSB differential) at FSR +-2.048 V, gain error 0.01 % typ / 0.15 % max at 25 C, INL 1 LSB at 8 SPS (s5.5, search snippet); power-down 0.5 uA typ / 2 uA max (recall, s5.5); abs max analog input GND-0.3 to VDD+0.3 V, 10 mA continuous (recall, s5.1) |
| R_F1/2 | NTC reference | Yageo RT0603BRD0710KL, 10 k 0.1 % 25 ppm/K | C95204 | 0.033 (20+) | 328660 | Device:R | Resistor_SMD:R_0603_1608Metric | 0.5 | 0.1 %, +-25 ppm/K, 75 V, -55..155 C |
| R_S1/2 | probe series R | 1 k 1 % 0603 | stage 4 | 0.01 | - | Device:R | R_0603 | 0.5 | limits ESD current into the ADC to <= 10 mA |
| C_F1/2 | probe filter cap | 100 nF X7R 0402, 2 pcs | stage 4 | 0.01 | - | Device:C | C_0402 | 0.5 | tau = (Rf // Rntc + 1 k) x 100 nF |
| D_T1/2 | probe ESD TVS | JSCJ CESD3V3D5, SOD-523, unidirectional | C90954 | 0.026 (20+) | not read | Device:D_Zener (unidirectional) | Diode_SMD:D_SOD-523 | 0.6 | Vrwm 3.3 V, Vclamp 13 V at 16 A (8/20 us), IR 80 nA, 210 W, IEC 61000-4-2 level 4; capacitance not listed on the page |
| J2 | probe connector, 4 pin | JST SM04B-SRSS-TB(LF)(SN), SH 1.0 mm right-angle SMD | C160404 | 0.216 (5+) | 41130 | Connector_Generic:Conn_01x04 | Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal (pads 1-4, MP x2) | 3.8 (recall) | 1 A, 50 V, -25..85 C; mating SHR-04V-S housing + crimps (about 0.2 USD more) |
| PR1/2 | NTC probe 2 m | 10 k B3950 +-1 %, epoxy bead, PVC wire 2 m, XH2.54 or bare | AliExpress / eBay | about 1.2 each (estimate, not a quoted price) | - | - | - | - | See risk 3: the common 5 x 25 mm stainless tube probe has an exposed metal tip and does not meet R33 literally |
| SW1 | setup button | TS-1088-AR02016, 4 x 3 mm tact | C720477 | 0.056 (basic) | 605776 | Switch:SW_Push | closest KiCad: Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2; check pad spacing against the TS-1088 drawing in stage 4 | 2.0 (recall) | 1.6 N, 100k cycles, -30..80 C |
| D1/R_D1 | status LED | Red 0603 150060RS75000 + 1 k | C3030991 | 0.27 | 7960 | Device:LED | LED_SMD:LED_0603_1608Metric | 0.5 | Vf 2.0 V at 20 mA, 250 mcd; (3.3 - 2.0) / 1 k = 1.3 mA, enough for 5 ms pulses |
| EN, boot | EN RC + pulls | R 10 k, C 1 uF (EN); R 10 k pull-up on IO9; R 10 k pull-up on IO8; 10 uF + 100 nF on 3V3 | stage 4 | about 0.06 total | - | Device:R, Device:C | 0402/0603 | - | Espressif guidelines: EN 10 k + 1 uF, IO9 pull-up, 10 uF at the supply entry |
| J3 | program/debug | 6 test pads, 1.5 mm | - | 0 | - | Connector_Generic:Conn_01x06 | TestPoint:TestPoint_Pad_D1.5mm (exists) x6 | 0 | USB D- (IO18), D+ (IO19), 3V3, GND, EN, IO9; 22 ohm series on D+/D- optional |

### Decision: no USB connector
Pads for USB (IO18/IO19 are the on-chip USB-Serial-JTAG) plus EN and IO9 allow flashing with a pogo fixture. A USB-C connector would add 0.15 to 0.5 USD and 8 mm of board edge on a 46 mm wide board.

## Subtotal (qty 10, parts only)
| Group | USD |
|---|---|
| U1 | 3.08 |
| Y1 + 2 caps | 0.35 |
| U3 | 1.07 |
| 2 R_F, 2 R_S, 2 C_F, 2 TVS | 0.19 |
| J2 + mating housings/crimps | 0.42 |
| SW1, D1, R_D1 | 0.34 |
| EN, boot pulls, decoupling | 0.06 |
| **Electronics subtotal** | **5.51** |
| 2 NTC probes (estimate) | 2.4 |
| **With probes** | **7.9** |

Architecture C8 assumed the ADC at about 1.5 USD: actual 1.07, so the C8 cost estimate falls by 0.4 USD.

## Alternatives and reasons

| Block | Choice | Alternative 1 | Alternative 2 | Why |
|---|---|---|---|---|
| MCU | ESP32-C3-MINI-1-H4X | -H4 C2934569 (more stock, 2.76 USD, older chip revision; Espressif lists N4X/H4X as the recommended ones) | ESP32-C3-MINI-1U (not wanted: no PCB antenna) | Espressif recommends the X versions for new designs; 105 C rating gives margin over R29 |
| ADC | ADS1115 | ADS1113/ADS1114 (one differential channel only; needs an analog mux, so no gain; ADS1113IRUGT lists at 7.8 USD idx, 157 in stock) | MCU internal ADC (+-10 mV, 0.95 C: fails R17, see architecture 2.4) | Only the ADS1115 has the 3 single-ended inputs needed (Ve, N1, N2), is 1.07 USD and is in stock. No cheaper pin-compatible 16-bit part exists in the JLC index |
| Crystal | CM7V 6 pF | Epson X1A0001410006 C94673, 6 pF, +-20 ppm, 5975 in stock, 0.235 (5+) | other 3215 6 pF types | Datasheet ESR 70 k stated (equals the Espressif limit); same CL, either works |
| TVS | CESD3V3D5 | ESD5Z3V3 (JSMSEMI C917015, bidirectional types exist) | DESD5Z3V3 (CBI, C21714095, 15 pF max) | 3.3 V standoff, 80 nA leakage, 0.026 USD |
| Rf | RT0603BRD0710KL | ARG03BTC1002 C309083 (0.1 %, 25 ppm, 0.021) | TR0603B10K0P0525Z C188363 | Yageo, 328k in stock |
| Connector | JST SH 4-pin | JST XH SMD 2.5 mm (matches the XH2.54 on common probes; LCSC number not looked up) | Screw terminal on face B | SH is verified, in stock and has a KiCad footprint |

## GPIO map (module pin numbers from datasheet Table 3-1, p11)

| GPIO | Module pin | Function | Notes |
|---|---|---|---|
| IO0 | 12 | XTAL_32K_P | RTC pad |
| IO1 | 13 | XTAL_32K_N | RTC pad |
| IO2 | 5 | VBAT_EN (active low) | strapping (pull-up recommended): the 1 M pull-up to VBAT_P keeps it high at reset. VBAT_P up to 3.4 V stays below the 3.6 V pin limit |
| IO3 | 6 | VBAT_ADC | ADC1 |
| IO4 | 18 | SENS_EN | 100 k pull-down; hold the level in deep sleep |
| IO5 | 19 | SDA | I2C pull-up to SENS_EN |
| IO6 | 20 | SCL | I2C pull-up to SENS_EN |
| IO7 | 21 | K1 IN1 | |
| IO10 | 16 | K1 IN2 | |
| IO20 | 30 (RXD0) | K2 IN1 | |
| IO21 | 31 (TXD0) | K2 IN2 | |
| IO8 | 22 | LED (active low) | strapping: must be 1 for download boot; 10 k pull-up |
| IO9 | 23 | SW1 to GND | strapping: low at reset = download mode; 10 k pull-up |
| IO18 | 26 | USB D- | J3 pad |
| IO19 | 27 | USB D+ | J3 pad |
| EN | 8 | reset | 10 k to 3V3, 1 uF to GND, J3 pad |
| 3V3 | 3 | supply | |
| GND | 1, 2, 11, 14, 36-53 | | |
| NC | 4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32-35 | | |

## Architecture numbers that changed or need a change

1. 40 MHz reference: the schematic in the module datasheet specifies +-10 ppm, not +-20 ppm. R20 budget: 10 + 0.8 + 0.2 = **+-11 ppm** (was +-21).
2. ADC cost 1.07 USD (was about 1.5). Offset +-3 LSB single-ended and INL 1 LSB confirm the (assumed) values in budget lines 4 and 5.
3. **New error term, not in the budget (line 4a): ADS1115 input impedance.** The input resistance at FSR +-4.096 V is several MOhm (recall: about 6 MOhm common mode, 15 MOhm differential; to be checked at s5.5). Source resistance on the NTC node is Rf // Rntc + 1 k, about 5 k: relative error 5 k / 6 M = 0.08 %, which gives ratio error 0.07 K at 0 C and 0.03 K at 40 C. Fixed by firmware (known divider loading) or reduce Rf to 4.7 k. The sum stays below 0.3 K even if uncorrected, but add the line.
4. TVS leakage (budget line 8): 80 nA max at 3.3 V against the 216 uA divider: 0.037 %, 0.009 K at 40 C. Confirmed within the 0.011 K of the budget. The TVS is on the J2 side of the 1 k, so its leakage flows through the 1 k and gives <= 0.1 mV.
5. ADS1115 shutdown (0.5 uA typ) is irrelevant for the sleep budget: U3 is powered from SENS_EN, which is 0 V in sleep.
6. ESP32-C3-MINI-1 height 2.4 mm confirmed (architecture 6.2).
7. Deep sleep 5 uA typ is for the module with RTC timer + RTC memory (Table 6-7); no figure for the 32 kHz crystal (budget +1 uA stays an assumption).

## Risks and requirement conflicts

1. **R18 wording cannot be met literally.** A TVS that survives +-8 kV has Vclamp 13 V at 16 A (CESD3V3D5), above ADC VDD + 0.3 V = 3.6 V. The ADS1115 pin survives through its internal diodes at <= 10 mA continuous (recall) and the 1 k series resistor limits the current to (13 - 3.9) / 1 k = 9 mA at the clamp. Proposal for the product person: reword R18 as "series R limits the pin current below the ADC input-current absolute maximum".
2. **R32 button cannot wake the SoC.** Only GPIO0-5 wake the ESP32-C3 from deep sleep; the button is on IO9 and all RTC pins are used. The button is read only during the 60 s control wakes (a press must last up to 60 s) or after a reset. Fix needs a GPIO swap (for example free IO5 or IO2) or an RC kick on EN; R32 is "could".
3. **R33: NTC probes.** Probes on the market (5 x 25 mm stainless tube with XH2.54) have a bare metal tip. Order the epoxy-bead MF52 type with a PVC/heat-shrink sleeve, or accept a stainless tube whose metal is sleeved. No part number or quote found: price is an estimate, and the probes are the largest uncertain cost line (about 2.4 of 7.9 USD). The probe 1 % tolerance and B tolerance are outside the electronics error of R17.
4. **No KiCad symbol or footprint for ESP32-C3-MINI-1.** Stage 4 must create a custom symbol (pins from the table above) and a footprint with 53 pads (datasheet s11.1: 48 x 0.4 / 0.8 mm pads plus 4 thermal vias per pad). The module footprint is 16.6 x 13.2 mm.
5. **Antenna keep-out dimensions are not in the datasheet text** (Figure 3-1 only; Espressif guideline text says 15 mm clearance in all directions in the product and antenna at the board edge). The 13 x 6 mm keep-out in the floorplan is an estimate from the module outline and must be checked on Figure 3-1 and the layout guideline.
6. **CE RED:** the Espressif certificate page fetch did not list the C3-MINI-1; the module datasheet says "RF certification: see certificates". R16 stays unconfirmed until the certificate PDF is seen.
7. **Crystal ESR 70 k equals the Espressif limit (<= 70 k).** Typical ESR is lower, but there is no margin on the maximum. Alternative: a crystal with ESR max 50 k.
8. Single source: the ADS1115 (TI) and the module (Espressif). ADS1115 was shown out of stock by one search snippet, in stock (4227) on the LCSC page; recheck at order time.
9. No vendor SPICE model: TI publishes no SPICE for ADS1115 (IBIS only is likely; not confirmed). Use `spice.source: fit` with a behavioural model: ideal 16-bit quantiser, offset +-3 LSB, input resistance 6 M to GND. Espressif has no SPICE model. Crystal and TVS: fit (TVS as a zener, Vz 3.9 V knee at 80 nA, clamp 13 V at 16 A).
10. Stock for SW1, D1 and Rf is high; for the TVS the stock was not read.
