# Parts, group AUDIO (stage 3)

## FINAL TABLE (iteration 3, 2026-10-22; supersedes the iteration-2 table below for the bridge, DAC, ADC rows, the 1.8 V rail, flash, currents and noise). Details in "Iteration 3" at the end.
| role | mpn | sourcing | price $ | stock | lib_id | fp_id | mA at 5 V typ/worst | SPICE |
|---|---|---|---|---|---|---|---|---|
| USB audio bridge (USB 2.0 HS device, UAC2 firmware, 24-bit play + record to 192 kHz; internal 512 KB flash, no external flash) | **STM32F723VET6** (LQFP-100, on-chip HS PHY, 2 x SAI, I2C) | ST eStore, TME [S]; not found in the JLC index | ~9.2 @10k (ST eStore [S]); budget 10 @100 [E] | in stock at ST eStore/TME [S, not re-read]; Mouser JP lists the sister IET6 13,309 [S] | MCU_ST_STM32F7:STM32F723VETx (KiCad, 100 pins checked by hwflow check) | Package_QFP:LQFP-100_14x14mm_P0.5mm | 41/52 (3.3 V buck, ~55/70 mA at 3.3 V [M]) | none (digital) |
| clocks 22.5792 / 24.576 MHz to DAC + ADC (one enabled at a time via OE) | SiT1602 3.2x2.5 3.3 V (SIT1602BC-23-33E-24.576000E / 22.579200) or SiT8008BI-31-33E-22.579200 (C817260) | global sourcing / LCSC | ~1.2 each [E] | unverified | Oscillator:ASE-xxxMHz | Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm | 3/4 (one running) | none |
| HSE crystal for the STM32 (25 MHz [M], feeds the HS PHY) | any 25 MHz 3.2x2.5 crystal + 2 caps | LCSC basic class | 0.2 [E] | high | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | in bridge | none |
| DAC | **ES9039Q2M** QFN-32 5x5 | global sourcing (not LCSC) | ~9 [E] | unverified | **kb_audio:ES9039Q2M** (new, 33 pins, from datasheet v0.2.3 p.10-12) | Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm (datasheet EP 3.2) | 24/30 (23.5 mA at 3.3 V, datasheet p.52) | behavioural: 0.60 uV A-wtd at 2 Vrms diff, Rdac 390 ohm, 7.5 mApp per pin |
| ADC | **ES9822PRO** QFN-40 5x5 | global sourcing | ~7 [E] | unverified | kb_audio:ES9822PRO (pins 1-23 re-confirmed on a third-party list; **datasheet not fetchable, rest unverified**) | Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm | 47/57 (only with mic jack occupied) [S] | behavioural: 125 dB [S] |
| other rows (OPA1612 x3, OPA1622, LT3045 x2, LT3094, ADP7118, jacks, ESD, ferrite) | unchanged from iteration 2 table below | | | | | | | |
| **removed** | W25Q64 bridge flash (-$1.4), 1.8 V rail (-$0.6, -11/14 mA), 0V9 bridge buck load (-150/200 mA at 0.9 V on the power group) | | | | | | | |
Headphone only: **158 typ / 223 worst** mA at 5 V (iteration 2: 209/291); with mic recording **205 / 280** (254/346), budget ~307. Noise (datasheet-derived, DAC 130 dB is re 10 Vrms, so I/V noise is now a separate line): headphone **0.93 intrinsic / 0.97 total** uV only with the 100 ohm diff-to-SE network (0.969 / 1.009 with 200 ohm: fails), limits 0.944 / 1.0; mic 0.65 / 0.73 unchanged ([S]). Audio BOM at 100: about **$59** (+1.4 over iteration 2; the power group also drops the 0.9 V buck). Alternates: XU316-1024-QF60B (12-week lead, symbol kept in kb_audio, footprint deleted); RP2040 as a full-speed UAC1 bridge (JLC stock, -$9, 24/96 only, ADC/DAC clocks by DAC master); STM32H723 + USB3300 ULPI PHY.

## Iteration-2 FINAL TABLE (superseded in part). Details in "Iteration 2" below; the iteration-1 text underneath is superseded where they differ.
| role | mpn | lcsc / sourcing | $ @100 | stock | datasheet | lib_id | fp_id | mA at 5 V typ/worst | SPICE |
|---|---|---|---|---|---|---|---|---|---|
| USB audio bridge | XU316-1024-QF60B-C24 (3.3 V IO; not QF60A) | global sourcing; DigiKey EU 0 stock, 416 due 2027-01-07 | ~6.8 [E from EUR 7.09@10, 5.31@1248] | **0** | xmos.com XM-014600-PC (pinout page) | kb_audio:XU316-1024-QF60B | kb_audio:QFN-60-5EP_7x7mm_P0.4mm (paddles estimated) | 68+11 / 90+14 (incl. new 1.8 V rail) | none (digital) |
| bridge flash | W25Q64JVSSIQ | C179171 | 1.40 | 200 | winbond | Memory_Flash:W25Q32JVSS | SOIC-8 5.3x5.3 | in bridge | none |
| clocks 22.5792 / 24.576 MHz | SiT1602 3.2x2.5 3.3 V (e.g. SIT1602BC-23-33E-24.576000E), SiT8008BI-31-33E-22.579200 (C817260) | global sourcing / LCSC C817260 | ~1.2 each [E] | unverified | sitime.com | Oscillator:ASE-xxxMHz | Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm | in bridge | none |
| DAC | ES9039Q2M (130 dB DNR [S]) | global sourcing | ~9 [E] | unverified | esstech.com ES9039Q2M_Datasheet_v0.2.3.pdf | **missing** | **missing** | 40 / 50 [E] | behavioural |
| ADC | ES9822PRO (125 dB 2-ch [S]) | global sourcing | ~7 [E] | unverified | ESS | kb_audio:ES9822PRO (pins unverified [F]) | Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm | 45 / 55 (only with mic jack occupied) | behavioural |
| I/V, diff-SE, mic preamp (3 duals) | OPA1612AIDR | C94590 | 0.93 | 26127 | ti.com/product/OPA1612 | Amplifier_Operational:OPA1612AxD | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | 69 total with OPA1622 | fit audio_fit.lib OPA1612_FIT (vendor SBOM396 not unpacked) |
| headphone amp | OPA1622IDRCR | not at LCSC; TI store out of stock | ~2.5 [E] | **0 at TI store**, distributors unverified | ti.com/product/OPA1622 | Amplifier_Audio:OPA1622 | Package_SON:Texas_S-PVSON-N10 | in the 69 | fit OPA1622_FIT (vendor SBOM958 not unpacked) |
| LDO 3V3_A, ADC AVCC 4.5 V | LT3045EMSE#TRPBF (x2) | C666577 | 5.77 | 1639 | analog.com LT3045 | Regulator_Linear:LT3045xMSE | Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm | 8 total LDO Iq | behavioural |
| LDO -4.5 V | LT3094EMSE#TRPBF | C670494 | 5.48 | 1188 | analog.com LT3094 | Regulator_Linear:LT3094xMSE | same MSOP-12 | in the 8 | behavioural |
| LDO +4.5 V (op-amps), 2V5_BIAS | ADP7118ACPZN-R7 | C579266 | ~1.9 | 4947 | analog.com ADP7118 | Regulator_Linear:ADP7118ACPZN | Package_CSP:LFCSP-6-1EP_2x2mm_P0.65mm_EP1x1.6mm | in the 8 | behavioural |
| 1.8 V rail for XU316 (new) | small 1.8 V buck/LDO (not chosen; circuit stage) | | ~0.6 [E] | | | | | 11 / 14 | |
| jacks HP + mic | SJ-3524-SMT-TR (Same Sky), 5.37 mm high, plug-detect switch | global sourcing (DigiKey 32,661 [S]) | ~1.0 each [E] | 32661 | same-sky.com | Connector_Audio:AudioJack3_SwitchT | Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal | - | none |
| audio ESD (8 contacts) | TPD1E10B06DPYT | C1847652 | 0.24 | 275 | ti.com | Power_Protection:TPD1E05U06DPY | Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm | - | none |
| 3V3_A ferrite | BLM18PG121SN1D | C14709 | 0.015 | 3.0 M | murata | generic | 0603 | - | none |
Headphone-only audio current: **209 typ / 291 worst** mA at 5 V; with mic recording **254 / 346** (budget ~307). Noise: headphone 0.84 intrinsic / 0.885 total uV (limits 0.944 / 1.0); mic 0.65 intrinsic min gain (0.73 with coupled), 0.21 at max gain. Audio BOM at 100: about **$58** (XU316 6.8, flash 1.4, XO 2.4, DAC 9, ADC 7, OPA1612 x3 2.8, OPA1622 2.5, LT3045 x2 11.5, LT3094 5.5, ADP7118 1.9, 1.8 V 0.6, jacks 2.0, ESD 1.9, passives ~3): $8-18 over the $40-50 allotment. Cost-downs: ADP7182 for the -4.5 V rail (-1.8), ADP7118 for the 3V3_A (-3.9, noise risk for DAC AVCC), ES9038Q2M instead of ES9039Q2M (-1 to -3 if stocked, loses 1 dB).

Evidence key. [V] = read in this session from the TI product page (ti.com/product/OPA1612, /OPA1622) or the LCSC product page. [A] = ESS marketing figure (Astute/Mouser product text), datasheet PDF itself not readable here (PDF fetch timed out; no pdftotext access). [M] = recalled from the datasheet, NOT re-verified: confirm before it goes in a `design.yaml` number. [E] = estimate.
The hwflow JLC index (`hwflow parts`) has none of the audio ICs (its text search ignores names), so stock and price come from LCSC product pages. hwflow `block list` has no audio block (esp32c3, rev_guard, tps61299 only).

## Chosen parts

| role | mpn | lcsc | $ @100 | stock | lib_id / fp_id | key specs | alternatives |
|---|---|---|---|---|---|---|---|
| USB audio bridge | XU316-1024-QF60A-C24 | not found at LCSC | ~8 [E] (Mouser $9.75 @1, due 2026-06) | Mouser | none (project symbol + QFN-60 fp needed; `lib fp QFN-60` empty) | xcore.ai, UAC2 HS, I2S master/slave, 2 external MCLK | CM6631A (~$3 [E], UAC2/UAC1, I2S master, lower current, no LCSC code found); SA9227; RP-class MCU (FS only, Windows UAC2 class driver needs HS: reject) |
| bridge flash | W25Q64JVSSIQ | C179171 | 1.40 [V] | 200 (low) | Memory_Flash:W25Q32JVSS (same SOIC-8 pinout) / Package_SO:SOIC-8_5.3x5.3mm (check `lib fp`) | 3.3 V SPI | W25Q32JVSSIQ |
| clocks 22.5792 / 24.576 MHz | SIT1602BC-33-XXN-24.576000T | C1164059 | 0.26 [V] | **0 [V]** | needs oscillator symbol; fp `Oscillator_SMD_*` not matched by `lib fp` | 3.3 V, 4-pad | SIT1602BI-13-25E-24.576000D (C1172080, 2.5 V); SIT1408BC-22-33N-22.579200E (Avnet only) |
| DAC | ES9039Q2M | not found | ~8 [E] | Mouser | none (project symbol + fp) | 130 dB DNR, -120 dB THD+N [A]; current-output | ES9038Q2M C2682306 $4.62 **stock 0** [V], 129 dB [A]; PCM1794A (lib symbol exists, SSOP-28; LCSC PCM1792ADB $14.92 @100 [V]) |
| ADC | ES9822PRO (124 dB DNR [M]) | not found | ~7 [E] | ? | none | 2 ch, I2S, on-chip PGA [M] | AK5552VN (123 dB [M]); PCM1808 (C-code unverified, 99 dB: fails R11); CS5381 (120 dB, ~$20) |
| I/V + diff-to-SE + 80 kHz filter, mic preamp (3 duals) | OPA1612AIDR | C94590 | 0.93 [V] | 26127 [V] | Amplifier_Operational:OPA1612AxD / Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | en 1.1 nV/rtHz @1 kHz, THD+N 0.000015 %, GBW 40 MHz, SR 27 V/us, Vos 0.5 mV max, Iq 3.6 mA/ch, Iout +-30 mA, +-2.25..18 V [V] | OPA1611 (C86723, single); LME4562/LM4562 |
| headphone amp (gain 1) | OPA1622IDRCR | not found | ~2.2 [E] | ? | Amplifier_Audio:OPA1622 / Package_SON:Texas_S-PVSON-N10 | en 2.8 nV/rtHz, THD+N -119.2 dB (32 ohm, 100 mW), Iout +145/-130 mA, Iq 2.6 mA/ch, GBW 32 MHz, SR 10 V/us, +-2..18 V, EN shutdown ~5 uA [V] | OPA1612 + LME49600 buffer in loop (lower noise, more Iq); TPA6120A2 (noise higher, Iq ~14 mA) |
| LDO 3V3_A (and optionally +4.5 V) | LT3045EMSE#TRPBF | C666577 | 5.77 [V] | 1639 [V] | Regulator_Linear:LT3045xMSE / Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm | 0.8 uVrms 10 Hz-100 kHz, ~2 nV/rtHz, PSRR >75 dB to 1 MHz [M] | ADP7118ACPZN-R7 |
| LDO -4.5 V | LT3094EMSE#TRPBF | C670494 | 5.48 [V] | 1188 [V] | Regulator_Linear:LT3094xMSE / same MSOP-12 | 0.8 uVrms, PSRR ~ LT3045 class [M] | ADP7182ACPZ-R7 C659406 $3.64 @1 [V], LFCSP-8, no KiCad symbol (only the TSOT-5 AUJZ one) |
| LDO +4.5 V, 2V5_BIAS | ADP7118ACPZN-R7 | C579266 | ~1.9 [V: 2.33 @1, 1.50 @1000] | 4947 [V] | Regulator_Linear:ADP7118ACPZN / Package_CSP:LFCSP-6-1EP_2x2mm_P0.65mm_EP1x1.6mm | 200 mA, adjustable, noise ~11 uVrms 10 Hz-100 kHz [M]; fine behind op-amp PSRR, not for DAC AVCC | LT3045 |
| jacks (HP, mic) | PJ-320D (SHOU HAN) | C431535 | 0.05 [V] | 74960 [V] | Connector_Audio:AudioJack4 / Connector_Audio:Jack_3.5mm_PJ320D_Horizontal | SMD TRRS, pads T, R1, R2, S (match symbol). **No detect switch.** Tie R2 to S for a TRS plug | PJ-327 family with switch (C319103, check) |
| audio ESD per contact | TPD1E10B06DPYT | C1847652 | 0.24 [V] | 275 [V] | `lib sym TPD1E10B06` empty; use Power_Protection:TPD1E05U06DPY / Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm | 12 pF, 5.5 V bidirectional, +-30 kV [M]; leakage <=100 nA [M] | TPD1E05U06DPYR: ~0.5 pF, +-12 kV [M] (preferred for distortion; LCSC code not found) |
| 3V3_A input ferrite | BLM18PG121SN1D | C14709 | 0.015 [V] | 3.0 M [V] | generic ferrite | 120 ohm @100 MHz, 2 A | GZ2012D101TF C1015 |

## Pin/pad check
- OPA1612AxD: pins 1/7 outputs, 2/6 inverting, 3/5 non-inverting, 4 V-, 8 V+ (SOIC-8, same numbers).
- OPA1622: 1/5 in+, 10/6 in-, 9/7 out, 8 EN, 2 V+, 3 GND, 4 V-, 11 EP (V-); pads 1-10 + 11 match.
- LT3045: pins 1-3 IN, 4 EN/UV, 8 SET, 10 OUTS, 11/12 OUT, 9/13 GND. LT3094: pin 7 VIOC, 3 EN/UV (different map from LT3045: do not reuse a net list). Pads 1-13 match.
- ADP7118ACPZN: 1 VOUT, 2 SENSE/ADJ, 3 GND, 4 EN, 5 SS, 6 VIN, 7 EP.
- PJ320D pads: T, R1, R2, S + 2 NPTH; AudioJack4 pins R1, R2, S, T.

## Noise budgets recomputed (A-weighted 20 Hz-20 kHz, uVrms)

Headphone (arch 5.1 line items, DAC and amp replaced by datasheet figures):

| item | arch | this | basis |
|---|---|---|---|
| DAC | 0.71 | 0.63 | ES9039Q2M 130 dB [A] x 2 Vrms: 2 x 10^(-130/20). ES9038Q2M 129 dB gives 0.71 |
| I/V + diff-to-SE + filter | 0.45 | 0.45 | OPA1612 1.1 nV/rtHz [V]; arch network kept. A 100 ohm network gives ~0.36 [E] |
| HP amp OPA1622 | 0.31 | 0.31 | 2.8 nV/rtHz [V] x 141 x 0.79 |
| DAC ref/AVCC at silence | 0.10 | 0.10 | allocation (LT3045 0.8 uV behind DAC AVCC PSRR is <0.02) |
| LDO via op-amp PSRR + 1 ohm | 0.01 + 0.01 | same | |
| **intrinsic RSS** | 0.90 | **0.84** | limit 0.944, margin 0.10 (ES9038Q2M: 0.90, margin 0.04) |

Widening further: in-loop buffer (OPA1612 driving LME49600 / BUF634A, OPA1612 en 1.1 nV) gives HP ~0.16 and RSS 0.80 at +8 mA/ch (against C12). Coupled 0.28 unchanged (layout). Total 0.89 against 1.0.

Mic (arch 5.2, ADC replaced): preamp OPA1612 1.1 nV/rtHz as arch. ADC ES9822PRO 124 dB [M] re 2 Vrms = 1.26 uV, / gain 2 = 0.63. Min gain RSS = sqrt(0.17^2 + 0.28^2 + 0.63^2 + 0.01^2) = **0.71** (arch 0.86 with 122 dB); max gain 0.21 (ADC 0.006 at x200). Both under 0.944 / 1.0. With AK5552VN 123 dB: 0.79 / RSS 0.80.

THD+N (R30): DAC -120 dB [A] RSS OPA1622 -119.2 dB at 32 ohm 100 mW [V] = -116.6 dB, meets -106. The 2 Vrms / 300 ohm case is not on the product page: read the OPA1622 datasheet curve at the circuit stage. OPA1612 Iout is only +-30 mA [V], so it cannot drive the 32 ohm load (44 mA peak at 1 Vrms): the OPA1622 (145 mA) is required.

## Audio current at 5 V, bus mode (typ / peak)
Chain: pre-reg 0.85, 3.3 V buck 0.88, 0.9 V buck 0.85.
| load | typ mA | peak mA | basis |
|---|---|---|---|
| bridge XU316 + flash + 2 XO | 68 | 90 | arch estimate kept [E]; XU316 power not read |
| DAC | 18 | 25 | [E] |
| ADC | 20 | 25 | [E] |
| op-amp Iq, 3 OPA1612 duals 7.2 mA + OPA1622 5.2 mA = 26.8 mA per rail [V] (peak 3x9+5.2 if max) | 70 | 78 | 2 x 5.5 V x 26.8 mA / 0.85 / 5 V |
| LDO Iq (LT3045, LT3094, 2 x ADP7118 ~ 3 mA) | in rails | | |
| HP signal 1 Vrms / 32 ohm both ch | 73 | 73 | arch (2 Vrms: 146) |
| **audio total** | **249** | **291** (364 at 2 Vrms) | the arch group row: 68+18+20+67+73 = 246 / 288 |
Result: the datasheet op-amp Iq equals the arch estimate (26.8 vs 26 mA per rail); no saving there. Real savings against C12: CM6631A bridge instead of XU316 (about -20 to -25 mA [E]); OPA1622 EN low when the jack is empty (-13 mA, needs a detect switch, which PJ-320D lacks); ADC mic channel off when unused (-10 [E]). The DAC/ADC currents need the datasheets.

## Group cost (100 units)
Bridge 8 + flash 1.4 + 2 XO 1.0 + DAC 8 + ADC 7 + OPA1612 x3 2.8 + OPA1622 2.2 + LT3045 5.8 + LT3094 5.5 + ADP7118 x2 3.8 + jacks 0.1 + ESD 8 x 0.24 = 1.9 + passives (C0G, film, 0.1 % R) ~3 = **about $51**, against the $20-30 allotment. Cost-down: CM6631A bridge (-5), ADP7118/ADP7182 rails (-4), ES9038Q2M (-3 if stocked): ~ $39. Neither reaches $30 without a spec change. Decision for the product person (ties to C9, R17).

## SPICE
- OPA1612: vendor PSpice SBOM396 (https://www.ti.com/lit/zip/SBOM396), TINA SBOM397 [V]. OPA1622: PSpice SBOM958 (https://www.ti.com/lit/zip/SBOM958), TINA SBOM957 [V]. Not downloaded into `models/` (downloads need approval here): fetch before the circuit stage.
- LT3045/LT3094: ADI publishes LTspice models (not downloaded). ADP7118: ADI model [M].
- Fallback for op-amps: en 1.1 nV/rtHz (OPA1612), 2.8 nV/rtHz (OPA1622) at 1 kHz [V]; 1/f corner ~ 100 Hz class, in ~ 1.7 pA/rtHz [M]; GBW 40 / 32 MHz, SR 27 / 10 V/us, Iq 3.6 / 2.6 mA/ch [V].
- DAC behavioral source: output noise 0.63 uV A-wtd, full scale 2 Vrms differential via I/V network, source impedance set by the I/V stage. ADC: 1.26 uV re 2 Vrms FS input-referred, Zin to read from the datasheet.

## Risks
1. DAC stock: no LCSC stock for ES9038Q2M (C2682306, 0); ES9039Q2M, ES9822PRO, XU316 not found at LCSC (Mouser/Digikey only, so hand-assembly or a consigned part). Supply chain risk is the main one.
2. 24.576 MHz oscillator C1164059 out of stock; 22.5792 MHz not found at LCSC.
3. Missing KiCad symbols/footprints: XU316 (QFN-60), ES9039Q2M, ES9822PRO, oscillator, TPD1E10B06, ADP7182 LFCSP-8. A project symbol or generic one with a pin check against the datasheet is needed.
4. Margin 0.10 uV depends on the ESS 130 dB figure [A], a marketing number; DAC datasheet not read (supply currents, FS output, I/V resistor unknown).
5. Audio total (249/291 mA) leaves C12 open; every audio saving needs the bridge swap.
6. No detect switch on PJ-320D; the HP shutdown idea needs a different jack or a GPIO.
7. Cost $51 against $20-30 for the group.
8. Many [M] numbers (LT3045, ADP7118 noise/PSRR, ES9822 124 dB) must be confirmed against the datasheets before they enter `design.yaml`.

---

# Iteration 2 (stage 3, PM round 4: STUDIO 120 dB kept, audio ~$40-50, bus audio <= ~300 mA at 5 V)

Environment note: pdftotext/curl refused; PDF datasheets through WebFetch return only binary or the front page (ES9039Q2M v0.2.2 and v0.2.3, OPA1612, OPA1622, LT3045, LT3094, ADP7118 PDFs: all failed or timed out, 2 attempts each). Mouser and analog.com pages time out. Tags: [V] read on a vendor/distributor page, [S] search-result snippet of a datasheet (not the page itself), [F] third-party symbol on flux.ai (datasheet-derived, unverified), [M] recalled, [E] estimate.

## Closed items (append order)

### Item 4: jacks (closed)
- Chosen: Same Sky (ex CUI) **SJ-3524-SMT-TR**: 3.5 mm stereo TRS, right angle, SMT, 1 internal switch (tip-normalled: T connects to TN until a plug enters), height **5.37 mm** [S: search/Digikey listing], 32,661 in stock at DigiKey [S]. Stack: 5.37 + 1.6 board = 6.97 mm, limit 11.1 mm (margin 4.1 mm). Price ~ $1.0 @100 [E] (Digikey page not readable).
- KiCad: symbol `Connector_Audio:AudioJack3_SwitchT` (pins R, S, T, TN) with footprint `Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal` (pads R, S, T, TN + 2 NPTH, bbox x +-5.6 y -9.0..+11.6 mm). `hwflow lib pads` shows exactly R, S, T, TN: pins match pads. No library work needed.
- Use: T and TN are shorted with no plug and open when a plug enters. Headphone jack: T is the low-impedance amp output net; pull TN up through 1 M to 3V3: TN reads low = empty (shorted to T), high = plug present; use it for the OPA1622 EN pin (high = on). Do not route audio through TN. Mic jack: T is the mic signal with the 2.2 k bias; pull TN down through 1 M: reads high (bias via T) = empty, low = plug present; it switches the ADC (CHIP_EN) and mic rail share. R tied to S for a TRS plug. Detect logic and its 1 M pull are circuit-stage items.
- Rejected: PJ-320D (C431535) has no switch; PJ-342C (C2837091) is 4.1 mm high TRRS without a known switch pin set.

### Item 3: clocks (closed, with a caveat)
- 22.5792 MHz: SiTime SiT8008BI-31-33E-22.579200 (LCSC C817260, SMD5032-4P, 3.3 V, +-20 ppm) [V: LCSC page; stock not shown ("2 alternative parts" suggests low stock)]. 24.576 MHz: SiT1602 family (3.2x2.5, 1.8-3.3 V, phase jitter 1.3 ps rms 12 kHz-20 MHz [S]), many 24.576 MHz variants at Avnet [S] (e.g. SIT1602BC-23-33E-24.576000E / -33S-); LCSC C1164059 is 0 stock [V, previous pass].
- 1.3 ps rms is the SiT1602 general-purpose figure, not a studio-grade figure. The DAC/ADC reference in this design is an I2S slave of the bridge (ASRC/DPLL inside the ESS parts reject MCLK jitter); the audio-band noise budget does not carry a jitter line. If the PM wants ultra-low jitter, KiCad has `Oscillator:ABLNO` (Abracon ultra-low phase noise XO), Mouser/Digikey only, ~$5-8 each [E].
- KiCad: `Oscillator:ASE-xxxMHz` (pins 1 EN, 2 GND, 3 OUT, 4 Vdd) with fp `Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm` (pads 1-4 at 1.05/0.82 mm) fits the SiT1602 3.2x2.5 pinout (1 OE, 2 GND, 3 CLK, 4 VDD). The SiT8008 in 5032 needs a different footprint (not made). Prefer 3.2x2.5 SiT1602 for both frequencies: one footprint.
- The XMOS reference voice board uses a 24 MHz crystal (Murata XRCGB24M000F1H02R0) on XIN/XOUT [V: xmos.com XM-014904-PC BOM]. The XU316 therefore runs from a crystal/24 MHz input; the 22.5792/24.576 MHz oscillators are audio master clocks on a GPIO/MCLK pin (X0D35 on QF60 pin 41 is "MCLK"): bridge reference-design scheme (crystal 24 MHz + two MCLK oscillators with a select) is kept.

### Item 2: obtainability (closed; mostly bad news)
| part | where | status (2026-10-22) |
|---|---|---|
| XU316-1024-QF60B-C24 (3.3 V IO) | DigiKey EU [V] | **0 stock**, 416 due 2027-01-07, lead 12 weeks; EUR 8.56 @1, 7.09 @10, 5.31 @1248 |
| XU316-1024-QF60A-C24 (1.8 V IO) | DigiKey CA [V] | **0 stock**, lead 12 weeks, CAD 15.60 @1, 9.68 @1248 |
| XU316 at Mouser | previous pass: $9.75 @1 due 2026-06 [stale]; Mouser pages time out here | unverified |
| ES9039Q2M | Mouser product text exists [S]; DigiKey search: no result [V]; LCSC: no | stock unverified; global sourcing candidate |
| ES9822PRO | not found at LCSC/DigiKey in the pages readable; alldatasheet shows datasheet [S] | stock unverified |
| OPA1622IDRCR | TI store [V]: out of stock for both reel options, purchase limit on samples | distributor stock unverified |
| OPA1612AIDR | LCSC C94590 26,127 [V previous pass] | good |
- **Variant correction**: the previous pass chose QF60A. QF60A has 1.8 V I/O on VDDIOL/R/T; QF60B has 3.3 V I/O [V: xmos.com XVF3610 pinout page, same package]. The design has 3V3_D, W25Q64JV (3.3 V) and 3.3 V ESS I/O, so **QF60B** is right. Both variants need a 1.8 V supply on VDDIOB18 (pins 17, 26) and USB_VDD18A (pin 31) [V]: a 1.8 V rail (~25 mA [E]) is missing in the architecture power tree. XMOS's reference voice board makes it with a 300 mA fixed 1.8 V LDO (NCP114A-1.8) plus a 3.3 V LDO and an AP61102 buck for the core [V: XM-014904-PC BOM].
- Bridge risk: no stocked XU316 anywhere I could read. Fallback bridge (UAC2 high-speed, I2S master 32-bit/384k): none verified; CM6631A is UAC2/1 but LCSC code unknown. Decision for the PM: wait ~12 weeks for XU316 (needed anyway for JLC global sourcing), or accept UAC1/UAC2 limits of a CM6631A-class bridge.

### Item 1 (partial): datasheet figures obtained so far
| figure | value | tag / source |
|---|---|---|
| ES9039Q2M DNR | +130 dB | [S] Astute Group product text |
| ES9039Q2M THD+N | -120 dB per channel (THD -126 dB) | [S] Mouser/Astute search text |
| ES9039Q2M interface | TDM, I2S, LJ, RJ, DSD, DoP, S/PDIF; programmable FIR filters | [S] |
| ES9039Q2M supply currents, FS output current, Zout, I/V circuit, pinout | **not readable** (PDF 107 pages, 3.2 MB; saved by the tool at /home/arosso/.claude/projects/-home-arosso-Personal-electron-design2/612987c6-97ee-457e-aa8f-2e9da15964d8/tool-results/webfetch-1792691903520-44eool.pdf) | open: the v0.2.3 PDF is on disk; pinout needs a person (or a PDF-capable tool) to read the pin table |
| ES9822PRO DNR | 125 dB (2-ch mode), 128 dB (mono); THD+N -117 dB | [S] alldatasheet snippet of datasheet |
| ES9822PRO supply current (48 kHz, 2 ch) | AVCC 26.5 mA + AVDD 15.2 mA at MCLK 49.152 MHz; AVCC 26.4 mA + AVDD 12.9 mA at 24.576 MHz | [S] |
| ES9822PRO package/pins | QFN-40 5x5, pitch 0.4, EP 3.84x3.84; AVCC is a **4.5 V** supply, AVDD 3.3 V, DVDD core, inputs IN_P1/IN_M1/IN_P2/IN_M2 (differential) | [F] flux.ai/silicon-yard/es9822qpro (user symbol; warns pin 40 = RT1/DGND, pin 41 = EP) |
| ES9038Q2M pins (fallback DAC, 129 dB [A]) | 32-QFN: 1 AVCC_R, 2 DACRB, 3 DACR, 4 BIAS, 5 SW, 6 FSYNC, 7 HPSDB, 8 DACL, 9 DACLB, 10 AVCC_L, 11 AGND_L, 12 VCCA_1, 13 DVCC, 14 DGND, 15 DVDD, 16 GPIO2, 17 GPIO1, 18 DATA2, 19 DATA1, 20 DATA_CLK, 21 RESETB, 22 RT1, 23 SCL, 24 SDA, 25 ADDR, 26 NC, 27 VCCA_2, 28 XOUT, 29 XI, 30 AGND_R, EP | [F] flux.ai/jecstronic/es9038q2m |
| TAD5242 (TI) | 120 dB DNR differential line out, THD+N -100 dB, 2 Vrms FS | [V] ti.com product page. Rejected: 120 dB is 10 dB short of the 130 dB class and THD+N -100 dB misses R30. |
| PCM1794A (TI) | stereo DNR ~127 dB (132 dB mono, 9 Vrms FS), THD+N -108 dB, current output 7.8 mA p-p, 28-SSOP | [V] ti.com product page (stereo 127 dB from memory [M]). Rejected: 127 dB re 2 Vrms = 0.89 uV alone, intrinsic RSS 1.07 uV; mono pair is $30. |
| OPA1612 | en 1.1 nV/rtHz @1 kHz, 3.6 mA/ch, +-30 mA, +-2.25..18 V, THD+N 0.000015 % | [V] ti.com document-viewer front page |
| OPA1622 | en 2.8 nV/rtHz @1 kHz, THD+N -119 dB at 142 mW/ch into 32 ohm, +145/-130 mA, 2.6 mA/ch, shutdown < 5 uA/ch, GBW 32 MHz, SR 10 V/us, +-2..18 V, VSON-10 3x3 | [V] ti.com |
| XU316 supplies | VDD 0.9 V (0.855-0.945) on pins 4,12,19,27,34,42,49,57 and 4 EP paddles 61-64; VDDIOL pin 8, VDDIOR 38, VDDIOT 52 (3.3 V on QF60B); VDDIOB18 pins 17, 26 (1.8 V); PLL_AVDD pin 22 (0.9 V); USB_VDD33A pin 30; USB_VDD18A pin 31; GND = EP paddle 65 | [V] xmos.com XM-014600-PC (XVF3610 on the same package) |
| XU316 currents | not published on any readable page | [E] keep arch 0.30 W (68/90 mA at 5 V) with a +-40 % spread |
| LT3045 / LT3094 / ADP7118 noise, PSRR | pages timed out | stay [M]: LT3045 0.8 uVrms 10 Hz-100 kHz, PSRR > 70 dB at 100 kHz, ~2 nV/rtHz; LT3094 same class (0.8 uVrms); ADP7118 ~11 uVrms (fixed) |

### Item 5: output stage for R29 (closed)
Stage kept: DAC current outputs -> OPA1612 I/V (4 amps) -> OPA1612 diff-to-SE with 2nd-order 80 kHz filter -> **OPA1622 gain-1 buffer** -> 0.47 ohm series -> jack T/R; OPA1622 EN driven by the jack detect (TN), so an empty jack costs 0. Rails +-4.5 V from the +-5.5 V pre-regulator through LT3045 (+) / LT3094 (-). Only the OPA1622 can drive 32 ohm (OPA1612: +-30 mA [V]).
| case (R29, both channels) | Vpk | Ipk/ch | amp headroom on +-4.5 V | verdict |
|---|---|---|---|---|
| 1.0 Vrms / 32 ohm (31 mW) | 1.41 V | 44 mA | 3.1 V (R29 asks 0.5 V) | passes; Ipk 30 % of the 145 mA limit [V] |
| 2.0 Vrms / 300 ohm | 2.83 V | 9.4 mA | ~1.3 V if the swing is within ~0.4 V of the rail at light load [M] | passes |
| 2.0 Vrms / 32 ohm (0 dBFS everywhere, the arch single-gain case) | 2.83 V | 88 mA | ~0.8 V if the drop is ~0.9 V at 100 mA [M] | passes electrically; 61 % of the current limit; read the OPA1622 output-swing curve at 100 mA at the circuit stage |
Supply current at 5 V (rails 0.85 efficient). Sine: mean |i| = 0.9 x Vrms / R per channel, both channels from the rail. 2 Vrms/32 ohm = 112 mA x 5.5 V / 0.85 / 5 V = **145 mA** (arch 146); 1 Vrms/32 ohm = 73 mA. Music (mean |i| = 0.8 x Vrms / R): -10 dBFS rms (0.63 Vrms) = 41 mA; -20 dBFS = 13 mA. A sustained 0 dBFS sine into 32 ohm is a test case, not a use case: the bus-mode budget uses music at -10 dBFS as "worst" and lists the sine as an overload column. Zout = 0.47 + OPA1622 closed loop (~0.05 ohm [M]) <= 0.6 ohm (R32).
### Item 6: noise budgets and bus current (closed; sources tagged)
Headphone (A-wtd 20 Hz-20 kHz, uVrms): DAC 0.63 (ES9039Q2M 130 dB [S] x 2 Vrms) | I/V + diff-SE + 80 kHz filter 0.45 (OPA1612 1.1 nV/rtHz [V], 200 ohm network, arch [E]; a 100 ohm network gives 0.36 [E]) | OPA1622 0.31 (2.8 nV/rtHz [V] x sqrt(20 kHz) x 0.79) | DAC ref/AVCC at silence 0.10 (allocation) | LDO through PSRR 0.01 + series 1 ohm 0.01 -> **intrinsic RSS 0.84** (limit 0.944, margin 0.10). Coupled 0.28 (arch, layout-dependent, limit 0.33). **Total 0.885 uV** against 1.0 (margin 0.115). Sensitivity: DAC DNR 129 dB gives 0.90 / 0.94; 128 dB gives 0.93 / 0.97; a 127 dB DAC (PCM1794A stereo) gives 1.07 / 1.10 and fails. The margin rests on the 130 dB [S] figure (ESS defines DNR re full scale at a given I/V gain: confirm which full-scale current and I/V resistor the 130 dB assumes).
Mic: source 0.17 (150 ohm // 2.2 k) | preamp OPA1612 + network 0.28 (min gain) / 0.12 (max) | ADC ES9822PRO 125 dB (2-ch) [S] re an assumed 2 Vrms differential full scale [E] = 1.12 uV, / gain 2 = 0.56 (at gain 200: 0.006) | bias supply 0.01 -> **min gain 0.65 uV intrinsic** (DR 123.7 dB re 1 Vrms), **max gain 0.21 uV** (EIN -133.6 dBV). With the 0.33 coupled limit at min gain: 0.73 total. Both within R11. If the ADC were 123 dB: 0.70 / 0.77. The full-scale voltage is not confirmed (datasheet unreadable). AK5552VN fallback 123 dB [M].
THD+N (R30): DAC -120 dB [S] RSS OPA1622 -119 dB at 142 mW/ch into 32 ohm [V] = -116.5 dB (meets -106); the 2 Vrms/300 ohm case needs the datasheet curve (not on the product page).

Bus-mode audio current at 5 V (mA; rails: pre-reg 0.85, 3.3 V buck 0.88, 0.9 V buck 0.85)
| load | typ | worst | basis |
|---|---|---|---|
| XU316 + W25Q64 + 2 XO (0.9 V core, 3.3 V IO/USB) | 68 | 90 | arch estimate 0.30 W [E]; XU316 datasheet currents unreadable here, spread +-40 % |
| 1.8 V rail (VDDIOB18 + USB_VDD18A), small 1.8 V buck | 11 | 14 | 25 mA at 1.8 V [E] (new, missing in the arch power tree) |
| DAC ES9039Q2M (3V3_A LDO + digital) | 40 | 50 | [E] (previous 18/25 was optimistic for an ESS part; datasheet unreadable) |
| ADC ES9822PRO, only while the mic jack is occupied: AVCC 26.5 mA at 4.5 V via pre-reg = 34, AVDD 15.2 mA at 3.3 V = 11 | 45 | 55 | [S] currents at 49.152 MHz MCLK; 0 with CHIP_EN low |
| op-amps: 3 OPA1612 duals 21.6 mA + OPA1622 5.2 mA = 26.8 mA per rail: 2 x 5.5 V x 26.8 / 0.85 / 5 | 69 | 87 | [V] Iq per channel; worst = +25 % |
| LDO Iq (LT3045 x2, LT3094, ADP7118) | 8 | 9 | [M] |
| headphone signal into 32 ohm, both channels | 13 | 41 | typ = music -20 dBFS, worst = music -10 dBFS |
| **audio total, mic jack empty** | **209** | **291** | |
| **audio total, mic recording** | **254** | **346** | |
| overload: sustained 0 dBFS sine, 2 Vrms/32 ohm (145 mA instead of 41) | | 395 / 450 | test case only |
Against the bus budget (PM round 4: internal <= 600 mA; hubs ~280, MCU ~12, power ~1.4 leave audio <= ~307 mA): headphones only passes at worst 291 (margin 16 mA); headphones plus an active mic at loud music is **346, 39 mA over**. Idle (both jacks empty, OPA1622 off by detect, ADC off) is ~180 mA typ. Ways to close the 39 mA: (a) +-5.0 V pre-reg (-14 mA, crest-peak dropout risk), (b) headphone -6 dB while recording in bus mode (-20 mA), (c) a lower-power bridge than XU316 (CM6631A class, -20 to -25 mA [E]). PM decision (ties to R29 "subject to round 3 q1"). Savings kept per PM: jack-detect shutdown and ADC off.

Item 5 addendum, rejected on paper: (1) +-5.0 V pre-reg instead of +-5.5 V saves ~14 mA but leaves 0.5 V over the LT3094 dropout at the 176 mA crest peak (both channels, 2 Vrms/32 ohm): only if the LT3094 dropout curve at 0.2 A allows (datasheet unreadable here); (2) OPA1622 as the diff-to-SE subtractor (removes one OPA1612 dual, -19 mA) raises the stage noise to ~0.8 uV, which breaks R10.

### Item 7: project library (closed with caveats)
- `/home/arosso/Personal/electron-design2/projects/mechanical-keyboard-usb-audio-hub/lib/symbols/kb_audio.kicad_sym`: `XU316-1024-QF60B` (65 pins: 60 + paddles 61-64 VDD, 65 GND; names carry the port, e.g. X0D35/MCLK; pinout from xmos.com XM-014600-PC `device_pinout.html`, the XVF3610-QF60 table on the same package; QF60A has the same pinout) and `ES9822PRO` (41 pins; pinout from the flux.ai ES9822QPRO symbol [F], NOT checked against the ESS datasheet; Footprint property = KiCad `Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm`, pad 41 = EP).
- `/home/arosso/Personal/electron-design2/projects/mechanical-keyboard-usb-audio-hub/lib/footprints/kb_audio.pretty/QFN-60-5EP_7x7mm_P0.4mm.kicad_mod`: 60 perimeter pads 0.8 x 0.2 at pitch 0.4 on a 6.8 mm centre square (15 per side, CCW from top-left; pin 1 = left side top end), paddles 61-64 (1.0 x 1.0 at +-2.0, +-2.0) and 65 (2.6 x 2.6 centre). The perimeter is the generic 7x7 0.4 mm QFN pattern; **the five paddle shapes are estimates** (no readable XMOS page has the package drawing): replace with the XU316 QF60 drawing before release. Pin-to-pad order (CCW from pin 1 at the left) is assumed from the XVF3610 table (VDD pins fall two per side, VDDIOL/R/T at the side middles), consistent but not drawing-verified.
- Checks run: bracket balance equal in both files (1262/1262, 431/431), 106 `(pin` entries = 65 + 41, 65 `(pad` entries, ES9822 pin numbers 1-41 each used once. **`hwflow lib show kb_audio:...` / `lib pads` could not be run**: the `lib` CLI builds `Libs()` without the project (it searches only the KiCad share: "symbol library 'kb_audio' not found"), and the python/env wrappers that load the project libs were refused in this session. Re-run once a part uses them (`uv run hwflow check <proj>`).
- Not made: **ES9039Q2M symbol and footprint** (pin table unreadable; PDF v0.2.3 is saved at the tool-results path in Item 1 and needs a reader). Library reuse for the other chosen parts (no new files): OPA1612 `Amplifier_Operational:OPA1612AxD`; OPA1622 `Amplifier_Audio:OPA1622` + `Package_SON:Texas_S-PVSON-N10`; LT3045/LT3094 `Regulator_Linear:LT3045xMSE`/`LT3094xMSE` + `Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm`; ADP7118 `Regulator_Linear:ADP7118ACPZN` + `Package_CSP:LFCSP-6-1EP_2x2mm_P0.65mm_EP1x1.6mm`; W25Q64 `Memory_Flash:W25Q32JVSS`; jacks `Connector_Audio:AudioJack3_SwitchT` + `Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal` (pads checked this pass); XO `Oscillator:ASE-xxxMHz` + `Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm` (pins/pads checked this pass); ESD `Power_Protection:TPD1E05U06DPY` + `Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm`.
- Datasheet pages per item: none (documents unreadable); pin sources are the URLs above.

### Item 8: SPICE (closed with fallbacks)
- TI vendor models downloaded by the fetch tool but **not unpacked** (unzip refused here): OPA1612 PSpice SBOM396 (https://www.ti.com/lit/zip/SBOM396, 27.7 KB, files `OPA161x.LIBSD`, `OPA161x.LIBsigSD`; copy at /home/arosso/.claude/projects/-home-arosso-Personal-electron-design2/612987c6-97ee-457e-aa8f-2e9da15964d8/tool-results/webfetch-1792692601453-gha65p.zip); OPA1622 PSpice SBOM958 (https://www.ti.com/lit/zip/SBOM958, 30.5 KB, `OPA1622.LIBSD`; copy at .../webfetch-1792692601581-ijgr8e.zip in the same folder). Subckt names and pin order are unknown until unpacked (`unzip -p <zip> OPA161x.LIBSD | head`). The `.LIBSD`/`.LIBsigSD` pair looks like a PSpice-signed library that ngspice may not read; use the fit for noise.
- LT3045/LT3094/ADP7118: ADI publishes LTspice models (analog.com timed out; not downloaded; LT parts' LTspice models are encrypted macros). For hwflow use a behavioural regulator: Vout, noise 0.8 uVrms 10 Hz-100 kHz (about 2 nV/rtHz) [M], PSRR 75 dB at 10 kHz, 60 dB at 1 MHz [M], Zout 5 mohm.
- Fit written: `/home/arosso/Personal/electron-design2/projects/mechanical-keyboard-usb-audio-hub/lib/spice/audio_fit.lib` with `.subckt OPA1612_FIT inp inn vpos vneg out` and `.subckt OPA1622_FIT inp inn vpos vneg out` (series resistor on the non-inverting input gives the noise: 73.7 ohm = 1.1 nV/rtHz, 478 ohm = 2.8 nV/rtHz; GBW 40 / 32 MHz single pole; swing limited to 1.0 V inside the rails; Iq by resistor; no current or slew limit). **Not simulated** (ngspice run refused here): deck `scratch/audio_fit_test.cir` checks follower noise, expected 1.1 / 2.8 nV/rtHz at 1 kHz. Use `spice.source: fit`.
- Behavioural DAC: output noise 0.63 uV A-wtd (5.7 nV/rtHz flat equivalent), full scale 2 Vrms differential, source impedance set by the I/V stage. Behavioural ADC: input-referred 1.12 uV A-wtd re 2 Vrms differential full scale [E] (about 7.9 nV/rtHz); Zin unknown (datasheet).

---

# Iteration 3 (stage 3, PM round 5 q2: replace the XU316, 12-week lead; keep ES9039Q2M and ES9822PRO)

## Item 2 done: ES9039Q2M read from the saved datasheet (facts in notes/datasheets/es9039q2m.md)
- QFN-32 5x5 P0.5, EP 3.2 (pin 33). Symbol `kb_audio:ES9039Q2M` (33 pins) written; footprint `Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm` (KiCad, pad 33 = EP; pads 1-32 CCW from the left top, same as the datasheet figure p.10).
- Supplies 3.3 V only (AVCC_DAC1/2 +-15 %, AVDD/VCCA +-5 %), DVDD 1.2 V internal (1 uF tantalum). Current at 48 kHz full scale, master mode MCLK 24.576 MHz: 6.8 + 6.8 + 0.5 + 9.4 = **23.5 mA** (78 mW); silence 10.3 mA (p.52-53). Slave mode 49.152 MHz: 16.2 mA.
- Output: current-mode, per pin 0.889 x AVCC Vpp (2.93 Vpp), 7.5 mApp, Rdac 390 ohm +-15 %, bias AVCC/2 (offset current 4.2 mA at Vg = 0). For 2 Vrms differential: Rf = 374 ohm (gain 0.96). ESS EVB uses Rf 1.8k (10 Vrms).
- DNR 130 dB A-wtd (-60 dBFS) and THD+N -120 dB (48 kHz), -117 dB (96 kHz), -114 dB (192 kHz): **both measured at 10 Vrms = 0 dBFS** on the EVB, not at 2 Vrms. Derived DAC part at our gain: 0.60 uV (see budgets below).
- DAC can be I2S master (BCLK/WS out on DATA_CLK/DATA1) from MCLK alone; I2C 0x90-0x96 (ADDR0/1).

## Item 1 (iteration 3): bridge choice
Requirement set: UAC2 (or UAC1 24/96, R12), play + record 24-bit, driverless, to the DAC/ADC over I2S, low current, available now.
| candidate | verdict | why |
|---|---|---|
| **STM32F723VET6 + firmware (TinyUSB UAC2 HS, async feedback, SAI slave)** | **chosen** | HS device with on-chip PHY (true UAC2, 192 kHz/24-bit play and record fit high speed), 3.3 V only (no 0.9 V, no 1.8 V rail, no external flash), ES9039Q2M master mode keeps the converter clock on the oscillator. In stock at ST eStore ($9.18 @10k) and TME [S]; KiCad symbol + LQFP-100 footprint exist (no library work). Cost: firmware is a project (no vendor image), LQFP-100 is 16 x 16 mm with leads (keep it outside the audio region: R35 forbids USB traces in it). |
| XMOS XU316 | alternate | 12-week lead time, 0 stock (iteration 2). Symbol kept in kb_audio (marked alternate), footprint deleted. |
| XMOS XU208-128-QF48 | rejected | DigiKey lead 12 weeks, Mouser non-stocked (MOQ 490); only Dasenic shows 10,116 [S]. Same firmware-licence flow as XU316. |
| Savitech SA9227/SA9312, C-Media CM6631A/CM6642, CT7601/CT7302 | not available | no stock, price or datasheet found at LCSC/Mouser/DigiKey in 5 searches (LCSC and alldatasheet block the fetch tool); only module sellers (Audiophonics) list SA9227 boards; record-path (I2S in) not confirmed. Single-source Asian parts: treat as not available. |
| RP2040 (JLC, C2040) + TinyUSB, full speed | fallback | in stock, ~$0.8; UAC1 24/96 stereo play + record fits the 12 Mbit/s bus (about 1.15 MB/s of 1.2 usable), 192 kHz does not; Windows UAC2 class driver and FS: not verified. Saves ~$9. PM call. |
| STM32H723 + USB3300 (ULPI PHY) | alternate | same firmware class, +$1.5 PHY, more area. |
| STM32F4 (e.g. F405/F446) FS | rejected | full speed only, no UAC2 HS |
Does the 1.8 V rail go away: yes. Does the 0.9 V bridge rail go away: yes (power group: remove 0V9_CORE buck, 150/200 mA at 0.9 V). Config bus: I2C1 (STM32) to DAC (0x90-0x96) and ADC. Clocks: two oscillators (22.5792, 24.576 MHz) -> MCLK of DAC and ADC; DAC is the I2S master; STM32 HSE crystal 25 MHz [M] for its USB PHY.
STM32 current: ~55/70 mA at 3.3 V [M] -> 41/52 mA at 5 V (3.3 V buck 0.88); oscillator 3/4. Bridge group 44/56 against 79/104 for XU316 + flash + 1.8 V (-35/-48 mA).

## Item 3 (iteration 3): ES9822PRO
- The ESS datasheet could not be obtained (esstech product URLs 404, alldatasheet 403, 5 attempts). Existing symbol kb_audio:ES9822PRO compared with the flux.ai ES9822QPRO list: pins 1-23 identical (1 GPIO10, 2 GPIO11, 3 MODE, 4 ACLK, 5 AVCC, 6 AGND, 7 XOUT, 8 XIN, 9 VREF_L, 10 AVCC_L, 11 AGND_L, 12 IN_P1, 13 IN_M1, 14-17 NC, 18 IN_M2, 19 IN_P2, 20 AGND_R, 21 AVCC_R, 22 VREF_R, 23 CHIP_EN). Pins 24-41 (GPIO9, SDA/SCLK/SS/MISO, DVDD, AVDD, DGND, GPIO8..1, RT1, EP) are from the same third-party symbol: **unverified**. Symbol description updated with this status.
- Footprint KiCad QFN-40-1EP 5x5 P0.4 EP3.8x3.8 (flux note: EP 3.84): pads 1-41 match symbol pins (hwflow check). Land pattern against the ESS package drawing: unverified.
- Full-scale input, input impedance and currents stay [S]: DNR 125 dB (2 ch), THD+N -117 dB, AVCC 26.4 mA at 4.5 V + AVDD 12.9 mA at 3.3 V (24.576 MHz MCLK, 48 kHz) from the alldatasheet snippet.
- **A person must read the ESS ES9822PRO datasheet (pin table, full-scale input voltage, input impedance, power-up order) before release.** Mic noise figures depend on the full-scale input voltage.

## Item 4: XU316 footprint
Deleted `lib/footprints/kb_audio.pretty/QFN-60-5EP_7x7mm_P0.4mm.kicad_mod` (unused; paddle shapes were estimates). The XU316 symbol stays as an alternate with an empty Footprint property.

## Item 5: bus-mode audio current at 5 V and noise budgets (iteration 3)
Chain: pre-reg 0.85 (op-amp rails, ADC AVCC), 3.3 V buck 0.88 (MCU), LDO from 5 V for 3V3_A (DAC, ADC AVDD: input current = load current).
| load | typ | worst | basis |
|---|---|---|---|
| bridge STM32F723 + 1 XO | 44 | 56 | [M] 55/70 mA at 3.3 V, buck 0.88; XO 3.5/5 mA |
| DAC ES9039Q2M (all 3.3 V from 3V3_A) | 24 | 30 | datasheet p.52: 23.5 mA full scale (master, 24.576 MHz); worst +25 % for 192 kHz |
| ADC ES9822PRO, only with mic jack occupied | 47 | 57 | [S] AVCC 26.4 mA x 5.5/0.85/5 = 34, AVDD 12.9 mA from the LDO = 13 |
| op-amps (3 OPA1612 duals + OPA1622) | 69 | 87 | unchanged |
| LDO Iq (LT3045 x2, LT3094, ADP7118) | 8 | 9 | [M] |
| headphone signal into 32 ohm | 13 | 41 | music -20 / -10 dBFS |
| **total, mic empty** | **158** | **223** | iteration 2: 209 / 291 |
| **total, mic recording** | **205** | **280** | iteration 2: 254 / 346; budget ~307: passes, margin 27 mA at worst |
| idle (OPA1622 off by detect, ADC off) | ~135 | | |
| overload sine 2 Vrms/32 ohm (145 mA) | | 327 / 384 | test case only |
Headphone noise (A-wtd 20 Hz-20 kHz, uVrms). The ESS 130 dB DNR is measured at 10 Vrms full scale with Rf = 1.8 k (gain 4.6), op-amp noise included: eval floor 3.16 uV, op-amp share ~1.3 uV (1.1 nV/rtHz x 5.6 plus Rf, two amps), DAC part 2.88 uV. At Rf = 374 ohm (gain 0.96, 2 Vrms) the DAC part is **0.60**. The I/V op-amp pair is a separate line:
| item | uV | basis |
|---|---|---|
| DAC | 0.60 | derived from datasheet p.54 |
| I/V OPA1612 x2, Rf 374 ohm | 0.52 | per pin sqrt((1.1 x 1.96)^2 + 2.49^2) = 3.3 nV/rtHz, x sqrt2, x 111.7 |
| diff-to-SE + 80 kHz filter, 100 ohm network | 0.36 [E] (200 ohm: 0.45) | arch, scaled |
| OPA1622 | 0.31 | 2.8 nV/rtHz |
| AVCC_DAC / ref at silence | 0.10 | allocation (ESS asks an ultra-low-noise regulator on AVCC_DAC; LT3045 0.8 uV [M]) |
| LDO via PSRR + 1 ohm | 0.01 + 0.01 | |
| **intrinsic RSS** | **0.93** (limit 0.944; 0.97 with the 200 ohm network) | |
| total with coupled 0.28 | **0.97** (limit 1.0; 1.01 with 200 ohm: fails) | margin only 0.03 uV: the circuit stage must use the 100 ohm network, 0.1 % thin-film R, and the OPA1612 current limit +-30 mA holds (14 mA at 1.41 V into 100 ohm) |
Levers if the margin disappears: two OPA1612 sections in parallel per I/V amp (-0.06 uV), mono mode of the DAC is not applicable, ES9039Q2M calibration/ES9312Q is not available. THD+N at 2 Vrms: DAC -120 dB (48 kHz), -117 dB (96 kHz) [datasheet p.54] RSS OPA1622 -119 dB = -115.5 dB at 48 kHz, -114.9 dB at 96 kHz: meets -106.
Output offset: DAC pin DC = AVCC/2 = 1.65 V; if the I/V virtual ground is 0 V, 4.2 mA flows in Rf (1.6 V DC at each op-amp output, same on both pins, removed by the diff stage; swing 0.2..3.0 V inside +-4.5 V). Rdac +-15 % moves the level +-15 % (use the digital volume).
Mic: unchanged, 0.65 intrinsic min gain (0.73 with coupled), 0.21 at max gain; depends on the ADC full-scale voltage, which is not read.

## Item 6: libraries (iteration 3)
`uv run hwflow check` on a scratch design (copied into scratch/kbcheck_audio inside the project, because writes to /tmp were not permitted; deleted afterwards) with U1 = kb_audio:ES9039Q2M on QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm (33 pins/33 pads), U2 = kb_audio:ES9822PRO on QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm (41/41), U3 = STM32F723VETx on LQFP-100_14x14mm_P0.5mm (100/100), every pin on its own net: the only errors were 174 "single-pin net" (33 + 41 + 100); no pin/pad mismatch, no package_pads/pitch error. The project library loads when `libraries:` lists its absolute path (`lib` is also auto-loaded).

## Risks (iteration 3)
1. Firmware: no ready UAC2 + async-feedback + record firmware; TinyUSB has the pieces (F7 port, UAC2); expect weeks, and the bridge becomes a software product (the XU316 had the same issue with xcore code).
2. STM32F723VET6 stock confirmed only on ST eStore/TME search snippets, not on LCSC/JLC (no JLC assembly: hand or consigned); datasheet not read: currents, REXTPHYHS value and HSE frequency for the HS PHY are [M].
3. ES9822PRO datasheet not readable: pins 24-41, full-scale input and package land pattern unverified; ES9039Q2M and ES9822PRO are global-sourcing parts with unverified stock.
4. Headphone noise margin 0.03 uV (total 0.97 against 1.0) after the datasheet showed the 130 dB is re 10 Vrms; needs the 100 ohm network; fails with 200 ohm.
5. LQFP-100 footprint 16 x 16 mm: audio region is 73 x 16.5 mm; place the bridge outside it (R35: no USB trace inside the region).
6. Power group: remove the 0V9_CORE (bridge) buck and the 1.8 V rail; the 3V3_D buck load rises by ~55 mA.
