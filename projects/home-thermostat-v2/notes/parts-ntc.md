# Parts: NTC front end (R13-R16, R25, R26, R28, R29)

Date 2026-10-20. Index caveat: the hwflow index (jlcsearch) returns stock = null for resistors and "Out" for capacitors, and has no SGM58031, ADS1115 or ESD-diode entries. Stock below comes from LCSC pages where stated; otherwise "unverified". Re-run `hwflow parts check` at order time (R29). The SGM58031 PDF could not be converted to text in this session; its numbers are from search extracts (see `datasheets/sgm58031.md`).

## 1. Parts

| Ref | Role | MPN | LCSC | Stock | Price qty 10 | Symbol | Footprint |
|---|---|---|---|---|---|---|---|
| U3 | 16-bit I2C ADC, 4 ch | SGM58031XMS10G/TR | C700177 | 3764 (page is "Pre-ordered-Chips": unverified) | USD 5.39 | Analog_ADC:ADS1115IDGS | Package_SO:MSOP-10_3x3mm_P0.5mm |
| R_ref1, R_ref2 | divider reference 10 k 0.1 % 25 ppm | Yageo RT0603BRD0710KL | C95204 | unverified (index null) | USD 0.024 | Device:R_US | Resistor_SMD:R_0603_1608Metric |
| R_cal1, R_cal2 | calibration divider 10 k / 10 k | same part | C95204 | as above | 0.024 | Device:R_US | R_0603 |
| R_s1, R_s2 | series 100 ohm 1 % | UniRoyal 0603WAF1000T5E | C22775 (basic) | unverified | 0.002 | Device:R_US | R_0603 |
| C_f1, C_f2 | filter 4.7 uF X5R 25 V | Samsung CL21A475KAQNNNE | C1779 (basic) | index "Out": unverified | 0.032 | Device:C | Capacitor_SMD:C_0805_2012Metric |
| D_esd1, D_esd2 | probe ESD | TI TPD1E05U06DPYT | C1847651 | 1900 | 0.0806 | Power_Protection:TPD1E05U06DPY | Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm |
| R_sda, R_scl | I2C pull-ups 4.7 k 1 % to VSW | UniRoyal 0603WAF4701T5E | C23162 (number from memory, check) | unverified | 0.002 | Device:R_US | R_0603 |
| C_d1 | ADC decoupling 100 nF X7R | Yageo CC0603KRX7R9BB104 | C14663 (from memory, check) | high | 0.003 | Device:C | Capacitor_SMD:C_0603_1608Metric |
| C_d2 | ADC bulk 1 uF X5R 25 V | Samsung CL10A105KA8NNNC | C15849 (from memory, check) | high | 0.01 | Device:C | C_0603 |
| NTC1, NTC2 | off-board probe, not on BOM | 10 k B3950 1 %, 5 m cable (Semitec 103AT-2 class, R25 10 k +/-1 %, B +/-1 %) | - | - | 0.5-1 USD (R14) | Thermistor_US | J2 terminal |

Pads: MSOP-10 pads 1-5 left, 6-10 right, 1:1 with symbol pins 1-10. 0603/0805 passives pads 1, 2 (Device:R_US, Device:C pins 1, 2). TPD1E05U06 pad 1 = I/O (terminal side), pad 2 = GND.

## 2. U3 SGM58031 versus the ADS1115IDGS symbol (pin by pin)

| Pin | SGM58031 MSOP-10 (extract) | Symbol pin | Match |
|---|---|---|---|
| 1 | ADDR | ADDR | yes (tie to GND: address 0x48) |
| 2 | ALERT/RDY | ALERT/RDY | yes (open drain; leave open or pull up to VSW if used as wake) |
| 3 | GND | GND | yes |
| 4-6 | AIN0, AIN1, AIN2 | AIN0, AIN1, AIN2 | yes |
| 7 | AIN3/VREFIN | AIN3 | yes (also external-reference input; use as AIN3) |
| 8 | VDD | VDD | yes |
| 9 | SDA | SDA | yes |
| 10 | SCL | SCL | yes |

Pinout source: LCSC/flux.ai pin table seen through a search extract, not the PDF figure. State this in review; confirm on the PDF figure before the board is released. Do not use the symbol's TSSOP footprint (pad 1.1 x 0.25); use the MSOP-10 footprint above (the symbol field footprint=TSSOP-10 must be overridden in design.yaml).

Wiring: AIN0 = N1, AIN1 = N2, AIN2 = calibration divider mid, AIN3 = P (VSW, direct, no filter). ADDR = GND. VDD = VSW with 100 nF + 1 uF at pins 8/3. Pull-ups 4.7 k to VSW (not to +3V3): pull-ups are unpowered when the ADC is off, so zero sleep current and no back-power through the I2C pins. 4.7 k gives 0.85 x 4.7 k x 50 pF = 0.2 us rise (400 kHz limit 0.3 us). Firmware rule: GPIO6/7 set to input (not driven high) whenever VSW is low. Current while SDA is low: 0.7 mA for a few ms per wake: negligible. Disable the ESP32 internal pull-ups.

Alternatives for U3:
- TI ADS1115IDGSR (LCSC C37593, VSSOP-10): same symbol and pinout, exact library match, datasheet fully published (INL 1 LSB typ, offset 3 LSB, gain 0.01 %). LCSC page shows out of stock; Mouser 5.12 USD. Choose it if SG Micro stock fails or the PDF check finds worse offset.
- Microchip MCP3428 (SOIC-14, 16-bit 4 ch, +/-2.048 V FS): rejected, P = 3.3 V exceeds the input range, a divider would break the ratio scheme.
- ADS1015 (12-bit): rejected, 2 mV LSB breaks R14.

## 3. Rejected assumption in the architecture: ratiometric reference

The ADC (SGM58031 and ADS1115 alike) uses an internal reference, so it is not ratiometric with VSW. The architecture does not rely on that: it reads P (VSW) and N with the same ADC and takes the ratio, so gain, reference drift (30-70 ppm/C) and gain error (0.03-0.3 %) cancel. This is sound. It does depend on four things that the architecture did not write down:

1. Input loading is not equal on P and N. P comes from a low-impedance source (GPIO about 25 ohm). N comes from 4.5-6.8 kohm. A switched-capacitor input of about 6 Mohm (ADS1115 class at +/-4.096 V; SGM value to verify) loads N by 0.08-0.11 % and P by 0.0004 %. Effect on R_NTC: factor P/(P - N) = 3.0 at 10 C, so +0.33 % in R, which is +0.069 C at 10 C, +0.045 C at 20 C, +0.033 C at 30 C. Not in the architecture budget. Fix: firmware constant for Rin (R_ntc correction), residual +/-30 % of the error (+/-0.021 C at 10 C). The calibration divider cannot measure it: its 0.1 % resistor ratio is as large as the effect (0.08 %).
2. Rail drift between the P and N readings. The boost refills C_bulk in PFM bursts; with 0.55 mA load the rail slews about 67 mV/s (0.55 mA / 8.2 mF) and N lags P by the filter time constant (21-32 ms), i.e. 1.4-2.1 mV. Fix: read order P, N, P (average the two P readings) with 860 SPS, which leaves about 20 % (0.01 C). Without it: +0.04 C. Firmware requirement for stage 4.
3. Single-ended offset of the SGM58031 is 2-8.5 LSB (not 3 LSB as budgeted). Option B (below) cuts it.
4. Part price: 5.39 USD, not 0.6 USD (R28): see section 7.

Option B (recommended if the margin is thin): put P on AIN3 and read N single-ended (AIN0 or AIN1) plus D = AINx - AIN3 differential (N - P, negative). Then r = N / (P - N) uses one single-ended and one differential reading: differential offset is 1-5 LSB, and error sensitivities fall from (kN + kP) to (1/N + 1/D) = 1.37 / 1.23 / 1.23 per V. ADC error drops from 0.080 / 0.072 / 0.072 to 0.042 / 0.041 / 0.046 C. No hardware change: the ADS1115 mux has AIN0-AIN3 and AIN1-AIN3 differential pairs, and AIN3 = P in the wiring above. (SGM58031 mux assumed identical to ADS1115: verify in the PDF.)

## 4. Excitation switching

Chosen: ESP32-C3 GPIO10 as VSW (as in architecture), 0.55 mA (0.29 mA dividers + 0.26 mA ADC) is far below the GPIO limit (20 mA drive; abs max 40 mA). Zero leakage while off, no extra part. A PFET would add Idss (AO3401 class: up to 1 uA max) against a 1.1 uA sleep margin: rejected. Add a 1 Mohm pull-down on VSW so the net does not float in deep sleep (cost 3.3 uA only while on: 3.3 V / 1 M = 3.3 uA, negligible against 550 uA). Open item: the digital GPIO hold in deep sleep on ESP32-C3 (non-RTC pin GPIO10) must be checked in the stage 4 GPIO plan; if not held, use RTC pin GPIO2 (the LED pin in the plan) instead. Rail: VSW = 3+ V (rail 3.234 V min outside relay pulses minus 20 mV) is above the ADC VDD minimum 3.0 V; never sample during a relay pulse (rail 3.00 V).

## 5. Key parameters for the error budget

| Item | Value | Source |
|---|---|---|
| ADC gain error / gain drift | 0.03-0.3 % / 30-70 ppm/C | cancel in ratio |
| ADC INL | 1 typ, 4 LSB max (125 uV/LSB at +/-4.096 V; used 500 uV) | extract |
| ADC offset | single-ended 2-8.5 LSB, differential 1-5 LSB, drift 0.06 LSB/C max | extract |
| ADC noise | 1 LSB (125 uV) per reading, no averaging credit | assumption |
| ADC input impedance | about 6 Mohm common mode (assumed, ADS1115) | verify |
| Reference drift | 30-70 ppm/C, cancels | extract |
| R_ref | 0.1 %, 25 ppm/C, 0603 100 mW (0.27 mW used) | RT0603BRD0710KL |
| R_s | 1 % 100 ohm, +/-1 ohm | UniRoyal |
| TVS leakage | 1 nA typ, 10 nA max at 2.5 V; IEC +/-12 kV contact, +/-15 kV air | TI TPD1E05U06 |
| C_f leakage | IR floor assumed 100 Mohm at 2 V = 20 nA (X5R 25 V, typically >1 Gohm) | assumption, verify in Samsung data |
| C_f effective | 4.7 uF -10 % tol -15 % bias at 2 V = 3.6 uF worst | assumption |
| Filter corner at 30 C | 1/(2 pi x 4.49 k x 4.7 uF) = 7.5 Hz; worst 9.9 Hz at 3.6 uF (R15 limit 10 Hz: marginal, pass) | calc |

## 6. R14 error budget recomputed (degrees C at node; sensitivities and NTC rows as in architecture 5.1)

| Block | 10 C | 20 C | 30 C |
|---|---|---|---|
| NTC R25 1 % | 0.203 | 0.218 | 0.233 |
| NTC B 1 % | 0.142 | 0.049 | 0.051 |
| R_ref 0.1 % + 25 ppm x 15 C | 0.028 | 0.030 | 0.032 |
| ADC INL 4 LSB (500 uV x (kN + kP)) | 0.023 | 0.021 | 0.021 |
| ADC noise 1 LSB | 0.006 | 0.005 | 0.005 |
| ADC offset 8.5 LSB at 125 uV, uncorrelated, plus drift | 0.051 | 0.046 | 0.046 |
| ADC total (option A, single-ended) | 0.080 | 0.072 | 0.072 |
| Input loading residual (Rin corrected, +/-30 %) | 0.021 | 0.014 | 0.010 |
| Cable + 100 ohm | 0.002 | 0.004 | 0.006 |
| Leakage: TVS 10 nA + C_f 20 nA = 30 nA at N | 0.006 | 0.005 | 0.004 |
| Self-heating | 0.005 | 0.005 | 0.005 |
| Rail drift (P-N-P bracket) | 0.010 | 0.008 | 0.008 |
| **Worst-case sum, option A** | **0.497** | **0.405** | **0.421** |
| **RSS, option A** | 0.263 | 0.237 | 0.252 |
| Worst-case sum, option B (ADC 0.042 / 0.041 / 0.046) | 0.459 | 0.374 | 0.395 |
| RSS, option B | 0.254 | 0.234 | 0.249 |

Result: pass against +/-0.5 C, but option A has only 0.003 C margin at 10 C worst case; the NTC is 69 % of it. Sensitivities to flag: (a) a probe with R25 +/-2 % (common for cheap probes) adds 0.2 C at 10 C and breaks the worst case in both options (0.70 / 0.66 C at 10 C); RSS (0.44 C, option A) still passes; specify 1 % in the user documentation. (b) TVS and filter-cap leakage are now 0.006 C, not 0.019. (c) Uncorrected input loading would add 0.069 C at 10 C and push option A to 0.545 C: the firmware correction is required, and so is the P-N-P read order (+0.04 C otherwise).

## 7. Cost and availability issues

- SGM58031 is 5.39 USD at qty 10 (LCSC), architecture assumed 0.6 USD. R28 estimate 16.7 -> about 21.5 USD (still under 25, margin 3.5 USD; extended-part fees come on top). ESD diodes 0.08 x 3 and the other passives match the estimate.
- LCSC URL for C700177 is in the "Pre-ordered-Chips" tree; R29 needs JLCPCB stock >= 15. Check at order time; fallback is the ADS1115IDGSR (also out of stock at LCSC, 5.12 USD at Mouser, hand-solder or consign).
- TPD1E05U06DPYT, 10 nA: stock 1900 on LCSC.

## 8. SPICE notes (stage 4)

- ADC not simulated. Model it as 6 Mohm to GND on N and on P, and a 1 pF input; the sampling kick is hidden by C_f.
- Divider: V(VSW) 3.3 V step, R_ref 10 k (0.1 %, tc1 = 25e-6 for R tolerance Monte Carlo), 100 ohm, cable 2 x 0.5 ohm, NTC with B equation (B = 3950, R25 = 10 k), C_f 3.6-5.2 uF with a 0.1 uF/1 uF decoupling at VDD.
- TVS leakage as a resistor from the terminal to GND: 10 nA at 2.5 V max = 250 Mohm (typical 1 nA = 2.5 Gohm). C_f leakage as 100 Mohm across C_f (worst). TVS capacitance 0.5 pF (ignore).
- ESD pulse: IEC 61000-4-2 8 kV contact model (150 pF, 330 ohm) onto the terminal with the TVS clamp at 10 V: check that N stays within -0.3 V ... 3.6 V; the 100 ohm + 4.7 uF protects the ADC pin. TPD1E05U06 has no SPICE model on TI's page that I used; use a 10 V clamp (Zener Vz = 8.5 V, series 0.5 ohm) as the stage 4 stand-in.
- SPICE parameters: spice.source: fit for D_esd (Zener stand-in) and for the ADC load; no vendor model published for SGM58031.

## 9. Risks

1. SGM58031 PDF not read page by page (no INL/offset/input-impedance verification, pin figure unconfirmed): all ADC numbers are search extracts.
2. Price 5.39 USD and "pre-ordered" stock for C700177; the ADS1115 fallback is also out of stock at LCSC.
3. Rin unknown for the SGM58031; the loading correction is a firmware constant.
4. Option A budget margin 0.003 C at 10 C; option B recommended.
5. C14663, C15849, C23162 LCSC numbers are from memory (not index-verified).
6. Probe tolerance (user supplies the probe): 1 % R25 and B must be documented.
7. Alternatives for each non-ADC part: R_ref Yageo RT0805BRD0710KL (C110775, 0805, 0.1 %, 25 ppm) or Panasonic ERA-3AEB103V (0.1 %, 25 ppm); C_f 0805B475K160CT (X7R 16 V, C123624); ESD TI TPD1E10B06DPYR (C48260, 100 nA max, spec at the budget limit) or onsemi ESD9B3.3ST5G (symbol exists; leakage not checked, assume 1 uA: reject).
