# Parts, group POWER / USB PD (stage 3)

## FINAL TABLE (iteration 3, 2026-10-22; LCSC price/stock read 2026-10-22; "est" = not read from a source; iteration 3 changes are in the section "Iteration 3" at the end and override the iteration 2 text below)

Iteration 3 changes in one line each: C-port controller is now IP2736U_EF (CH236D dropped, datasheet unreadable); CH224D, TPS25940A, TPS2553, LM61460 pin tables and land patterns read, project symbols and footprints created and checked with hwflow check; CH224D has no PG pin and VBUS abs max 24 V (local R + zener); TPS2553 OUT max 7 V so a series P-FET Q_bus is needed in front of VBUS_C; eFuse R_ILIM 88.7 k (0.90/0.99/1.07 A); inductors FEXL0660A-8R2M (C5378547) with FXL0630-8R2-M (C167222) as fallback.

| role | mpn | lcsc | $ at 100 | stock | datasheet | lib_id | fp_id | quiescent mA (bus mode) | SPICE source |
|---|---|---|---|---|---|---|---|---|---|
| 5V_SYS buck 20 -> 5.1 V, 4.3 A peak (FPWM, 400 kHz) | LM61460AFSQRJRRQ1 | C2876600 | 1.51 | 145 (low) | ti.com/lit/ds/symlink/lm61460-q1.pdf (read: notes/datasheets/lm61460.md) | kb_power:LM61460AFS | kb_power:VQFN-HR-14_RJR0014A_4.0x3.5mm (from the TI RJR0014A land pattern, checked) | 0.0006 (EN low, off in bus mode) | fit (behavioral: Vref 1.0 V, 400 kHz, Rds 41/21 mohm, HS limit 10.3 A); vendor PSpice on ti.com/product/LM61460-Q1, not downloaded |
| C-port buck x2 (20 -> 5/9 V) | LM61460AFSQRJRRQ1 | C2876600 | 1.51 | as above | as above | as above | as above | 0.0006 each | fit |
| C-port source controller x2 | IP2736U_EF (Injoinic) | C42437970 | 0.5634 | 3944 | datasheet.lcsc.com/datasheet/pdf/fa546fa55e17fb06f11f04b1719b76c3.pdf (read: notes/datasheets/ip2736u.md); alternatives: CH236D (unreadable, not on LCSC), TPS25750 class (flash, no FB drive) | kb_power:IP2736U | kb_power:QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_IP2736U | 0 (VIN from the buck output: unpowered in bus mode); abs max VIN/CC/FB 30 V | none (behavioral: ideal source with FB loop) |
| PD sink 20 V | CH224D | C3975094 | 0.2403 (150 qty) | 14165 | WCH CH224 manual V2.1 (read: notes/datasheets/ch224d.md); alternatives CH224A (PG pin, 32 V, ESSOP10/DFN10, stock not checked), AP33771 | kb_power:CH224D | Package_DFN_QFN:QFN-20-1EP_3x3mm_P0.4mm_EP1.65x1.65mm (datasheet QFN20 3x3, 0.4 pitch, EP 1.8) | 0 (VBUS from PD input only) | none. VBUS abs max 24 V, CC 20 V; NO PG pin |
| sink FET x2 back-to-back | AO4407A (AOS) | C16072 | 0.2433 (150 qty) | 12130 | AOS AO4407A | Device:Q_PMOS | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | 0 | fit (Rds 12 mohm class, from LCSC listing) |
| TVS VIN20 (+ CH224D pin protection: 100 ohm series + BZT52C22 22 V zener + 1 uF at its VBUS pin; CC TVS Vrwm 12-13 V) | SMBJ22A (DOWO) | C284010 | 0.0314 | 400 | LCSC listing: Vrwm 22 V, Vc 35.5 V at 16.9 A (below LM61460 42 V; above CH224D 24 V, hence the local clamp) | Device:D_TVS | Diode_SMD:D_SMB | 0 | n/a |
| TVS host VBUS | SMBJ5.0A-TR (ST) | C130397 | 0.197 (150 qty) | 10640 | LCSC listing: Vrwm 5 V | Device:D_TVS | Diode_SMD:D_SMB | 0 | n/a |
| host eFuse + reverse blocking (replaces TPS25947 and LM74700) | TPS25940ARVCR | C2653873 | 1.5094 | 763 | ti.com/lit/ds/symlink/tps25940.pdf (read: notes/datasheets/tps25940a.md); R_ILIM 88.7 k = 0.90/0.99/1.07 A; alternative TPS25942ARVCR C181295 | kb_power:TPS25940A | kb_power:WQFN-20_RVC0020A_4.0x3.0mm_P0.5mm_EP2.6x1.6mm (TI RVC0020A land pattern) | 0.21 (IQ on; 0.095 DevSleep) | fit (Ron 42 mohm, IMON 52.3 uA/A) |
| USB-A port switch + BC1.2 x2 | TPS2546RTER | C117009 | 0.5212 | 20738 | ti.com/lit/ds/symlink/tps2546.pdf | kb_power:TPS2546 | Package_DFN_QFN:Texas_RTE_WQFN-16-1EP_3x3mm_P0.5mm_EP1.2x0.8mm | 0.165-0.23 each (SDP, enabled) | fit |
| C-port bus-mode limit switch x2 | TPS2553DBVR | C55266 | 0.3296 | 50499 | ti.com/lit/ds/symlink/tps2553.pdf (read: notes/datasheets/tps2553.md); 7 V abs max: needs series P-FET Q_bus to VBUS_C; ILIM 226 k / 89 k / 43.2 k = 120 / 299 / 605 mA typ | kb_power:TPS2553 | Package_TO_SOT_SMD:SOT-23-6 | 0.12 each (enabled), 0.0001 off | fit (Ron 85 mohm, limit I = 25230/R^1.016 min) |
| 3V3_D and 0V9_CORE bucks x2 | TPS62933FDRLR | C5219272 | 0.7244 | 967 | ti.com/lit/ds/symlink/tps62933.pdf | Regulator_Switching:TPS62933F | SOT-583-8 | 0.125 each | fit |
| +-5.5 V pre-regulator | TPS65131RGER | C87663 | 2.28 | 2539 | ti.com/lit/ds/symlink/tps65131.pdf | Regulator_Switching:TPS65131RGE | VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias | 0.5 (inside audio block) | fit |
| upstream CC + SS mux (hub group) | HD3SS3220IRNHR | C701817 | 1.0942 | 556 | see parts-hub.md | (hub group) | (hub group) | (hub group) | - |
| 8.2 uH inductor (x3: 5V_SYS, 2 C ports) | FEXL0660A-8R2M (Isat 8.4 A, Irms 8 A, 26.4 mohm, 6.6x6.4; peak need 4.95 A at 5V_SYS, 3.95 A at a C port) | C5378547 | 1.09 (100 qty) | 550 | datasheet.lcsc.com/datasheet/pdf/f40adaf039c1475297db7da23c31da4d.pdf | Device:L | project fp needed (6.6x6.4 shielded: circuit/layout stage from the LCSC datasheet) | - | n/a |
| 8.2 uH fallback | FXL0630-8R2-M (Isat 6 A, Irms 5 A, 60 mohm, 7x6.6) | C167222 | 0.1117 (150 qty) | 47730 | LCSC listing | Device:L | as above | - | n/a (0.17 W more loss per C port) |
| 2.2 uH (3V3, 0V9) | FSA0630-2R2MT (Isat 10 A, 15 mohm, 7x6.6) | C41384288 | 0.0518 | 1550 | LCSC listing | Device:L | project fp needed | - | n/a |
| 4.7 uH (+-5.5 V, x2) | CKCS5040-4.7uH/M (Isat 3.5 A, 32 mohm, 5x5) | C354606 | 0.0405 | 3790 | LCSC listing | Device:L | project fp needed | - | n/a |
| bulk cap 22 uF 25 V X5R 1206 | CL31A226KAHNNNE | C12891 | 0.0932 (200 qty) | 897720 | Samsung | Device:C | Capacitor_SMD:C_1206_3216Metric | - | n/a (derate: about 8 uF effective at 20 V) |

Group IC cost at 100 boards: about $19 (down from the $28 estimate); the full board cost limit of $125 is not threatened by this group.
Open decisions and blockers are in "Iteration 2: open risks" at the end of this file.

Tool limits in this run: the hwflow `parts` index returned 404 for buck_converters, diodes and inductors, and has no pure-buck rows in boost_converters; curl/pdftotext were not allowed. Datasheet facts come from a text proxy of the vendor PDFs (page numbers in notes/datasheets/*.md). LCSC numbers and prices are only given where seen; others are "verify". Prices marked (est) are class estimates, not quotes.

No layout block of the library fits this group (esp32c3, rev_guard_2pfet, tps61299_boost). rev_guard_2pfet (two P-FETs in parallel, SOT-23) is too small for 3.2 A at 20 V.

## Table

| role | chosen | LCSC | $ (100) | datasheet | KiCad sym / fp | key specs | alternatives |
|---|---|---|---|---|---|---|---|
| PD sink 20 V | CH224K | C970725 | 0.28 (stock 0-11.7k, sources disagree) | wch-ic.com CH224DS1.PDF | none (project symbol); fp ESSOP-10 not in lib, project fp | 4-22 V, PD3.0, PG drives PMOS | AP33771 (sym Interface_USB:AP33771, QFN-24, I2C), CH224A |
| sink FETs (back-to-back) | 2x P-FET 30 V, <=12 mohm, SO-8/PDFN (e.g. AO4407A class) | verify | ~0.25 ea (est) | - | Device:Q_PMOS + SOIC-8 | 3.2 A, 0.1 W budget | - |
| TVS VIN20 | SMBJ22A-class (Vrwm 22 V, Vbr 24.4, Vc ~35.5 V at Ipp ~17 A) | verify | 0.10 (est) | - | Device:D_TVS, Diode_SMD:D_SMB | downstream parts need >= 36 V rating (LM61440 42 V ok) | SMBJ24A (Vc 38.9 V: LM61440 abs max 42 V still ok) |
| TVS host VBUS | SMBJ5.0A / SMF5.0A-class bidirectional or unidirectional | verify | 0.08 (est) | - | Device:D_TVS | Vc ~9.2 V | - |
| ideal diode host -> 5V_SYS | LM74700-Q1 + N-FET (<=5 mohm, 20 V) | verify | ~0.9 (est) | ti.com LM74700-Q1 (not read) | Power_Management:LM74700 | Iq ~80 uA (memory, verify) | TPS2121 (rejected: 56 mohm in the 3.5-4 A main path = 0.7-0.9 W, eats the whole 65 W margin) |
| host eFuse | TPS25947 | verify | ~1.2 (est) | ti.com SLVSFC9 | none; fp QFN-2x2 0.45 mm pitch (project) | 2.7-23 V, R_ILM=3334/I, +-10 % only above 1 A, Iq 0.43-0.61 mA, Ron 28 mohm | TPS2592xx (sym exists, VSON-10, easier pitch; datasheet not read), TPS25982 (too big: 13.5 A min limit) |
| ILIM switching + CC decode | 2x NFET (parallel R on ILIM) + 2 nanopower comparators on max(CC1,CC2) with Rd 5.1 k, thresholds 0.66 V / 1.23 V (Rp 80 uA / 180 uA / 330 uA classes) | verify | ~0.6 (est) | - | generic | trips 0.8/1.35/2.7 A nominal for <=0.9/1.5/3.0 A | MCU ADC + GPIO (RP2040) sets the ILIM, with 0.8 A hardware default: removes the comparators, boots at 0.8 A |
| 5V_SYS buck 20 -> 5.1 V 4 A | LM61440 (400 kHz, L 8.2 uH, 3x22 uF) | verify | ~2.2 (est) | ti.com lm61440.pdf | none; fp 14-pin VQFN-HR project fp | 36 V (42 V abs), Rds 41/21 mohm, 95 % at 20 V/5 V/4 A/400 kHz (p10), Iq 7 uA, shutdown 0.6 uA, FPWM mode (select method TO VERIFY) | LMR33640ADDA (sym exists, SO-8 PowerPAD, 24 uA Iq, 94 % at 20 V/4 A, but PFM only: fails forced-PWM), TPS62933F x2 (3 A each, sym exists) |
| input isolation PMOS on 5V_SYS buck VIN | P-FET 20 V <=10 mohm, drain to VIN20 | verify | 0.2 (est) | - | generic | stops bus-mode back-feed (5 V on SW through the high-side body diode) | - |
| C-port source controller x2 | CH236D (QFN-20 3x3) | verify | ~0.6 (est) | WCH CH236DS1.PDF (not read) | Interface_USB:CH236D, fp QFN-20-1EP_3x3mm_P0.4mm_EP1.65x1.65mm | PD2.0/3.0 + PPS + BC1.2, gate drivers GATEP/GATEN, FB inject | IP2736U_EF (C42437970, $0.56, controller only, 4.5k stock, no symbol), SW3516 (not in index/lib) |
| C-port buck x2 (20 -> 5/9 V) | LM61440 each (FB driven by the CH236D) | verify | ~2.2 | as above | as above | 95 % at 20 V in/5 V/4 A; 9 V/2.2 A est. 95-96 % (curve not read) | TPS54540-class (not FPWM) |
| C-port bus-mode path x2 | P-FET/N-FET VBUS switch with CH236D gates + current limit 100/250/500 mA (limit switch such as TPS2553 + switched ILIM R, or reuse TPS2546 without BC1.2) | verify | ~0.5 ea (est) | - | TPS2553 none | R21: 250/500 mA | FPF2004-class fixed 100 mA (sym exists) |
| USB-A switch + BC1.2 x2 | TPS2546RTER | C117009 | listed 0.198 (suspect, verify; est 1.0) | ti.com tps2546.pdf | none (project sym); fp Texas_RTE_WQFN-16-1EP_3x3mm_P0.5mm_EP1.2x0.8mm | 73 mohm, Iq 165-230 uA enabled, 0.1 uA disabled, ILIM=50250/R, 2 banks | TPS2549 (C2150133 555 stock, C181611 Q1 2.4k), TPS2514A DCP only + switch |
| 3V3_D buck | TPS62933F, 3.3 V, 1.2 MHz, L 2.2 uH, Cout 30 uF | verify | ~0.8 (est) | ti.com tps62933.pdf | Regulator_Switching:TPS62933F / SOT-583-8 | Iq 125 uA, FCCM, 3.8-30 V, Vref 0.8 V | TPS62A02 (not read) |
| 0V9_CORE buck | TPS62933F, 0.9 V (check the audio bridge supply first) | verify | ~0.8 | same | same | on-time at 5 V in, 1.2 MHz: 150 ns, min on-time to check | TPS62A02 |
| +-5.5 V pre-regulator | TPS65131RGE (boost + inverter, 1.38 MHz, L 4.7 uH x2) | verify | ~2.5 (est) | ti.com tps65131.pdf | Regulator_Switching:TPS65131RGE / VQFN-24-1EP_4x4 | Iq 0.5 mA, inverter 81 %, pulse-skip at light load | 2 separate: boost (MT3608 class in index, not FPWM) + TPS63700 inverter |
| inductors | 8.2 uH shielded >=6 A (5V_SYS, C-bucks x3), 2.2 uH (3V3), 4.7 uH x2 (+-5.5 V) | verify | 0.4-0.7 ea | - | Device:L | saturation > 5.5 A on LM61440 | - |

## Bus-mode quiescent (from datasheets, 5 V rail; PD path and C-port chips are off)
eFuse TPS25947 0.43-0.61 mA; LM74700 ~0.08 mA (verify); 3V3 + 0V9 TPS62933F 2 x 0.125 = 0.25 mA (plus switching loss in FCCM); TPS65131 0.5 mA (inside the 67 mA audio block of the architecture); 2 x TPS2546 0.33-0.46 mA; 2 x C-port limit switch ~0.2 mA; comparators ~0.02 mA. Overhead of about 1.0-1.4 mA excluding TPS65131 (the architecture reserves 6 mA for "power-path, CC logic, encoder"). The 12-51 mA shortfall of C12 is not worsened by this group.

## PD-mode efficiency at 20 W per C port
LM61440 datasheet: 95 % at 20 V in / 5 V out / 4 A / 400 kHz (p10); 93 % at 2.1 MHz. The 9 V / 2.22 A point is not in the text I read (expect 95-96 %). Add the CH236D and gate drive overhead (est 10-30 mW) and board losses: use 94 % in the budget; passes the 93-94 % target with ~1 % margin. For 5V_SYS buck: 95 % at 4 A, 400 kHz; the architecture assumes 92 %.
Discovery: any mux IC (TPS2121, 56 mohm) in the 5V_SYS main path costs 0.7-0.9 W at 3.5-4 A, so the OR is done after the buck output with a diode-OR on the host side only.

## Vendor SPICE models
None found for LM61440, TPS62933F, TPS65131, TPS2546, TPS25947 (datasheets say none; TI product pages usually offer PSpice or TINA - not downloaded: no network write access). Behavioral model parameters: LM61440 Vref 1.0 V (FB), fsw 400 kHz, Rds 41/21 mohm, Ilim_hs 6-8 A, Iq 7 uA; TPS62933F Vref 0.8 V, fsw 500 kHz/1.2 MHz, Rds 76/32 mohm, Ilim 4.2-5.8 A, Iq 125 uA; TPS65131 Vref 1.213 V, 1.38 MHz, Ilim 1.95 A, Iq 0.5 mA; TPS2546 Ron 73 mohm, Ilim = 50250/R mA; TPS25947 Ron 28 mohm, ILIM = 3334/R A. FET and TVS values to be taken when the exact MPNs are chosen.

## Group cost (est, 100 qty)
About $28: sink 1.1, protection 0.3, host path 2.9, 5V_SYS 3.8, 2 C ports 11.4 (controller, buck, L, FETs, limit switch, caps), 2 A switches 2.4 (est), 3V3/0V9 2.6, +-5.5 V 4.5. All with at least +-30 % uncertainty until stock and prices are verified.

## Risks
1. LCSC stock and price unverified for most parts (index endpoints down). CH224K stock conflicts (0 vs 11.7k).
2. No KiCad symbol for CH224K, TPS25947, TPS2546, LM61440, TPS2121, IP2736U; no ESSOP-10 / VQFN-HR-14 footprints. Project symbols and footprints needed; pin and pad numbers then from the datasheet pin tables (notes/datasheets), not guessed.
3. C-port PD source: CH236D datasheet not read (FB-injection scheme, gate polarity, bus-mode pass-through unverified). Single point of failure for R7. IP2736U is the fallback but also needs an external buck reference.
4. LM61440 FPWM selection method not confirmed; if it needs a different suffix, the 400 kHz FPWM claim (R35) fails.
5. TPS25947 gives +-10 % only above 1 A: trip <= 0.9 A (R19) not guaranteed. Needs a nominal 0.7-0.75 A setting (R_ILM 4.5-4.8 k) and a check that the 900 mA host still works, or an MCU-controlled limit. 2x2 mm 0.45 mm pitch QFN is hard to route.
6. TPS65131 is pulse-skipping at light load: not strict forced PWM.
7. TPS2546 min limit ~90-100 mA: the 0.1 A bus-mode port limit sits at the edge; a switched resistor network is needed.
8. 65 W margin: 0.7 W at peak; each 1 % of C-port buck efficiency is 0.4 W.

# Iteration 2 (2026-10-22), progress log

Tooling in this session: shell PDF tools and python refused; WebFetch cannot decode PDFs; TI HTML pages truncate at the features section. LCSC price/stock were read from `https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=C<number>` (works through WebFetch, 403 on its search endpoint). Stock figures are as of 2026-10-22.

## Closed so far
- LM61440 FPWM: the LCSC part C3188034 LM61440AANRJRR is the AUTO (PFM, no spread) variant: $3.44 at 100, stock 15. TI orderable naming (seen in TI search results: LM61440AFSQRJRRQ1 "FPWM, spread spectrum, adjustable, Q1") shows FPWM is an orderable SUFFIX (letter F), not a pin. No FPWM LM61440 is stocked at LCSC; the pin-compatible 6 A sibling LM61460AFSQRJRRQ1 (FPWM + pseudo-random spread spectrum, 200 kHz-2.2 MHz, same 14-pin VQFN-HR pinout) is C2876600: $1.51 at 100, stock 145 (3 needed per board, so 300 per 100 boards: JLC global sourcing / reel). The sibling is cheaper than the AUTO 4 A part.
- Stock/price (100): TPS2546RTER C117009 $0.5212 stock 20738 (the earlier "0.198" was wrong); TPS62933FDRLR C5219272 $0.7244 stock 967; TPS65131RGER C87663 $2.28 stock 2539; TPS2553DBVR C55266 $0.3296 stock 50499; HD3SS3220IRNHR C701817 $1.0942 stock 556; HD3SS3212RKSR C129320 $1.0341 stock 336; CH224K C970725 $0.2847 stock 0 (out of stock); LM74700QDBVRQ1 C2941042 $1.83 stock 0 (out of stock); CH236D and TPS25947 are not on LCSC (searches found nothing).

## Iteration 2: decisions and arithmetic

### 1. C-port PD source: CH236D NOT confirmed
Two attempts to read WCH CH236DS1.PDF failed (wch-ic.com pages return only the company name; no text mirror; LCSC does not list CH236D). Known: KiCad symbol Interface_USB:CH236D (pins FBOX, VHV, GATEP, GATEN, ~NMOS, VDD, UD+/-, CC1/2, IS+/-, VFB, IFB, VBUS, FBO), PD2.0/3.0 + PPS + BC1.2 source controller. NOT confirmed: the 5 V/3 A + 9 V/2.22 A PDO list, how FBO drives the buck FB, bus-mode pass-through with a settable limit, VBUS discharge, any CC orientation output. CH236D is not in LCSC stock: global sourcing only. The CH224D pin snippet (same QFN-20 3x3 0.4 mm, GATE, ~NMOS, ISP/ISN, CC1/2) suggests the same die family, but that is not a datasheet check.
Fallback (also unverified): IP2736U_EF (C42437970, 4526 in stock, $0.56; read earlier: PWM/opto/I2C feedback to an external buck, NMOS driver). TPS25750-class rejected: boot firmware flash, about $5, no FB drive.
Orientation for the HD3SS3212 SEL pins must not depend on the CH236D: one nanopower comparator per C port compares CC1 and CC2 (the line pulled down by the sink Rd 5.1 k is the attached one), with an RC of about 10 ms on the output so PD BMC traffic cannot toggle the mux. About $0.15 per port.
Needed from the product person or orchestrator: the CH236D datasheet text (pin table, application circuit), or approval of IP2736U. The circuit stage must not wire CH236D pins before this closes.

### 2. LM61440 forced PWM
FPWM is an orderable-suffix choice, not a pin. TI names the FPWM + spread-spectrum Q1 variants LM61440AFSQRJRRQ1 / LM61460AFSQRJRRQ1 (A = adjustable, F = FPWM, S = spread; seen in search-result text, to be confirmed in the Device Comparison Table, SNVSBD6 section 5). LCSC's LM61440AANRJRR (C3188034, $3.44, stock 15) is the AUTO variant (PFM at light load): rejected for R35. Chosen: LM61460AFSQRJRRQ1 (C2876600, 6 A, same pinout, $1.51 at 100, stock 145): three per board, so 100 boards need 300 pieces: order a reel or use JLC global sourcing.
- fsw: RT 33.2 k gives 400 kHz, 6.04 k gives 2100 kHz (datasheet BOM, TI HTML snippet). 95 % at 20 V in/5 V out/4 A/400 kHz (LM61440 p10). Spread spectrum is pseudo-random (depth not read): set RT 31.6 k (about 420 kHz nominal, 1/f scaling) so the low edge stays near 400 kHz.
- L 8.2 uH, Cout 3 x 22 uF at 5 V/400 kHz (p31). At 9 V out: ripple (20-9) x (9/20)/(400k x 8.2u) = 1.5 A pk-pk, acceptable at 2.22 A. 9 V/2.22 A efficiency from 20 V: curve not read, estimate 95 %. Budget uses 94.5 %: 40.0/0.945 = 42.3 W in (architecture 42.55 W), 0.2 W better.

### 3. R19 enforcement arithmetic (900 mA host)
Host current = internal + sum of port currents. R19: internal <= 600 mA, ports >= 75 mA each: 600 + 4 x 75 = 900 mA exactly; architecture worst 585 + 300 = 885 mA.
- The host eFuse TPS25940A is +-8 % (TI page snippet); ILIM about 96.2/R_ILIM(kohm) (derived from "20 k = 4.81 A", estimate). It must not trip at 885 mA, so min trip >= 885 mA, nominal >= 962 mA, max trip 1.04 A. R19's literal "trip <= 900 mA" cannot hold with the 75 mA floor and +-8 %. PM choices: (a) accept max trip about 1.0 A on the 900 mA host (nominal 0.93 A, R_ILIM 103 k, window 0.86-1.0 A); (b) cut internal loads to <= 540 mA (540 + 300 = 840 min trip, nominal 913, max 986); (c) accept a 70 mA port floor. My default: (a).
- Port limits are the primary sum limit, the eFuse is the fault limit. Bus mode: TPS2546 ILIM_LO R 562 k gives 89 mA typ per USB-A port; C ports use TPS2553 (75 mA-1.7 A typ range) at about 90 mA. The limit is not the load: a device draws its limit only if it asks for it. Worst simultaneous case (all four at limit, internal at peak): 585 + 4 x 89 = 941 mA, 41 mA over 900 and inside the 1.0 A eFuse max of choice (a). Recommended and cheap: TPS25940 IMON (+-8 %) to the MCU ADC; firmware lowers port limits (EN or ILIM_LO resistor) when host current exceeds 0.85 of trip.
- Host Rp level: owned by the HD3SS3220 (upstream CC Rd). It reports the advertised current in a register read over I2C (register and field to verify in its datasheet); the MCU drives the NFET gates on the eFuse and port ILIM networks. No second Rd. With the MCU off, all NFETs are off = lowest limit (eFuse 0.93 A nominal, ports 89 mA).
- eFuse ILIM levels (nominal, 96.2/R): 900 mA host 103 k (0.93 A), 1.5 A host 63.4 k (1.52 A), 3 A host 31.6 k (3.04 A); with +-8 % these are max 1.0 / 1.64 / 3.29 A: all estimates until the TPS25940 equations are read.

### 4. TPS2546 levels
ILIM_HI (PD mode) 29.4 k = 1.71 A typ (R8 needs 1.5 A). ILIM_LO (bus mode): R_A 562 k = 89 mA; add R_B 332 k through an NFET (host >= 1.5 A): parallel 209 k = 240 mA; add R_C 196 k through a second NFET (host 3 A): parallel 100.8 k = 499 mA. ILIM_SEL and CTL pins pick the bank (CTL table p28, not re-read). Accuracy: the datasheet formulas carry an additive offset of about +-30 mA (notes/datasheets/tps2546.md, sign unverified): at 89 mA the window is about 60-120 mA, so the 75 mA floor is not guaranteed by the part alone; at 240 mA about 210-270 mA (floor 225 ok); at 499 mA about 470-530. Mitigation: R_A 470 k (107 mA typ, window 77-137) if option (a) of section 3 is taken.

### 5. TPS65131
Not strict forced PWM: fixed 1.38 MHz PWM with light-load pulse skipping (threshold not read). The 26 mA static load per rail (op-amp quiescent) plus a 2.2 k dummy load on each rail (2.5 mA at 5.5 V) keeps it in PWM. 1.38 MHz is above the 400 kHz R35 floor. Confirm in simulation. No charge-pump inverter needed. C87663 $2.28 at 100, stock 2539.

### 6. Exact parts: remarks
- TPS25947 is not on LCSC, LM74700Q is out of stock (0). TPS25940ARVCR (C2653873, 763, $1.51) replaces both: 2.7-18 V, 0.6-5.3 A +-8 %, 42 mohm, IMON, reverse current blocking (stops 5V_SYS back-feed into the host in PD mode), WQFN-20 3x4 0.5 mm. Bus-mode drop 42 mohm x 0.9 A = 38 mV (budget 54 mV). Alternative TPS25942ARVCR (C181295, $0.61, same package) needs a separate ideal diode.
- TVS VIN20: SMBJ22A (Vrwm 22 V, Vc 35.5 V at 16.9 A) is below the LM61460 42 V abs max. The CH224D/CH236D VBUS abs max is NOT read ("4-30 V" supply): if it is below 36 V the clamp needs a lower part, but SMBJ20A (Vrwm 20 V) conducts at 20 V +5 % PD, so the controller pin would need its own series R + zener. Resolve with the abs max.
- Input isolation PMOS on the 5V_SYS buck VIN: AO4407A (one per board).
- 8.2 uH: FXL1040-8R2-M (C167232) has stock 0. Alternatives FEXL0660A-8R2M (550 in stock, 8 A) and FSA0630-8R2MT (Isat 5.5 A; 4.3 A + 0.75 A half-ripple = 5.05 A, margin 9 %): their LCSC numbers were not read.

### 7. Library items (project lib; `hwflow lib show` does not read project libs, so loaded with hwflow Libs(extra_dirs))
- kb_power:LM61460AFS: 14 pins 1-14 (LM61440 datasheet p4-5 pinout, same family). Loads.
- kb_power:TPS2546: pins 1-16 + EP 17 (notes/datasheets/tps2546.md, datasheet p3; function assignment not re-read from the PDF). Loads; matches pads 1-17 of Package_DFN_QFN:Texas_RTE_WQFN-16-1EP_3x3mm_P0.5mm_EP1.2x0.8mm.
- (SUPERSEDED in iteration 3, footprint now VQFN-HR-14_RJR0014A_4.0x3.5mm) kb_power.pretty/VQFN-HR-14_RJR_4.0x3.5mm_P0.5mm_PRELIM: pads 1-14, 0.5 mm pitch, 7 per side. PRELIMINARY: pad positions and sizes are NOT from the TI land-pattern drawing (unreadable here); replace from the SNVSBD6 package outline before layout release.
- Not created (pin table unreadable): CH224D, TPS25940, TPS2553. The circuit stage must not bind them before the pin tables are in notes/datasheets/.

### 8. SPICE
No vendor model downloaded (WebFetch cannot save zip files; TI models target PSpice/TINA). TI lists PSpice/TINA models for LM61440AAN, LM61440-Q1 and on https://www.ti.com/product/LM61460-Q1. Use spice.source: fit: LM61440 Vref 1.0 V, fsw 400-420 kHz, Rds 41/21 mohm (LM61460 Rds not read: 30/15 mohm est), Ilim 8 A est; TPS2546 Ron 73 mohm; TPS25940 Ron 42 mohm; TPS2553 Ron about 85 mohm (est).

### Bus-mode quiescent (5 V rail, sum without TPS65131)
TPS25940 0.5 (est) + 3V3/0V9 bucks 0.25 + 2 x TPS2546 0.4 + 2 x TPS2553 0.2 (est) + 2 x CH236D 2.0 (est, unverified) + comparators 0.05 = about 3.4 mA, inside the 6 mA reserve of the architecture (TPS65131 0.5 mA is inside the audio block). LM61460 x3 off (EN low, 0.6 uA each); CH224D unpowered (no PD input).

### Open risks
1. CH236D unconfirmed and not on LCSC; R7 depends on it.
2. R19: 75 mA floor + 600 mA internal + +-8 % eFuse cannot give "trip <= 900 mA": PM choice (a)/(b)/(c).
3. LM61460AFS stock 145 for 300 pieces; AFS naming from search text.
4. Pin tables or land patterns not read for CH224D, TPS25940, TPS2553, VQFN-HR-14: footprint is preliminary.
5. TPS2546 low-current offset can break the 75 mA floor.
6. TVS clamp 35.5 V against the unread CH224D/CH236D VBUS abs max.
7. No stocked 8.2 uH >= 6 A part confirmed.

# Iteration 3 (2026-10-22), PM decision round 5 q1 applied (host eFuse is a fault limit, max trip <= 1.0 A, never trips below 900 mA of legitimate load; port switches keep host draw <= 900 mA nominal; ports >= 75 mA normal, >= 60 mA heavy case)

## 3.1 C-port PD source controller: IP2736U_EF chosen (CH236D dropped)
- CH236D: datasheet still unreadable (WCH page returns no text; not on LCSC; web search finds nothing). Rejected for that reason only.
- IP2736U_EF (Injoinic, LCSC C42437970, QFN-24 4x4, 3944 in stock, $0.5634 at 100): datasheet read from datasheet.lcsc.com (notes/datasheets/ip2736u.md). PD2.0/3.1/PPS source with a voltage loop that acts on an external buck's feedback node through pin FB (Fig. 1 p4: R1 100 k from converter output to the FB node, R2 sets the 5 V default), NMOS driver and Vds sense for the VBUS path, current sense CSP/CSN (10 mohm shunt), AUTOMATIC VBUS bleed, standby mode, CC/DP/DM 20 V tolerant. It drives the LM61460 FB node directly: no forced-PWM buck of its own is needed.
- TI option rejected: TPS25750/TPS26750 need a boot image from SPI flash/I2C, cost about $5, and have no feedback-injection output, so they would need a separate voltage-select resistor network on the buck; TPS25751-class is the same. The IP2736U gives the FB drive and bleed with no firmware flash.
- NOT confirmed (risk): the PDO list of the stocked _EF variant (5 V/3 A + 9 V/2.22 A required), the current limit setting method, the CC pin behaviour with VIN = 0, quiescent current. The datasheet says the part is customizable by Injoinic (online upgrade). Action for the PM/orchestrator: order a sample or ask Injoinic/LCSC for the _EF default PDO table before the layout release; I2C (CMPI/SDA, SCL) is available to set it from the MCU if the vendor documents it.
- Supply scheme: VIN (pin 24) through 28 ohm from the buck output node (datasheet Fig. 1), so the IP2736U is unpowered (0 uA) in bus mode and when VIN20 is absent. The buck EN comes from a VIN20 divider (EN threshold 1.263 V rising, 28 % hysteresis; EN is 42 V tolerant). Default Vout 5 V by R2; IP2736U FB then moves it to 9 V (R1 100 k, RFBB 24.9 k as in the TI BOM, 5 V point).
- Orientation for the HD3SS3212 SEL: separate comparator per port on CC1/CC2 (the line pulled down by the sink Rd 5.1 k is the attached one: 5 V x 5.1/(56+5.1) = 0.42 V with Rp bus-mode, about 1.7 V with 330 uA Rp, against the open line near the Rp source voltage). It works in both modes, with 10 ms RC on the output. About $0.15 per port.
- Bus-mode CC Rp: with the IP2736U unpowered, each C port needs its own Rp. 56 k (default USB, +-20 %) from 5V_SYS through a small P-FET (gate pulled up by 100 k to 5V_SYS, pulled down by an NMOS driven by the PDMODE signal) to CC1 and CC2, removed in PD mode so it does not fight the IP2736U Rp. PDMODE = VIN20 > about 8 V (divider into an N-FET).

## 3.2 Bus-mode pass-through of the C ports
- 5V_SYS -> TPS2553DBVR (IN, EN = NOT PDMODE, ILIM network) -> series P-FET Q_bus -> VBUS_C (port side of the IP2736U NMOS). TPS2553 abs max on OUT/IN is only 7 V (p5) while VBUS_C is 9 V in PD mode, so the TPS2553 OUT cannot be tied straight to VBUS_C. Q_bus: drain on the TPS2553 OUT, source on VBUS_C (its body diode points from OUT to VBUS_C: conducts in bus mode, blocks the 9 V in PD mode), gate pulled to VBUS_C by 100 k and pulled to GND by an N-FET from PDMODE-bar. Rds < 30 mohm costs < 20 mV at 600 mA. The IP2736U path NMOS is oriented drain = converter side, source = port side, so its body diode blocks the bus-mode 5 V from the (off) buck output.
- ILIM resistors, 1 % (TPS2553 Table 2, p20; I = 25230/R^1.016 min, 22980/R^0.94 max, mA, kohm): 900 mA host 226 k = 101/120/142 mA min/typ/max (floor 75 mA met); 1.5 A host: 226 k || 147 k = 89 k -> 262/299/343 mA; 3 A host: 226 k || 147 k || 84.5 k = 43.2 k -> 544/605/673 mA (R21 >= 500 mA met at min). Switched by two N-FETs from the MCU (state from the HD3SS3220 Rp register); with the MCU off the resistor is 226 k (lowest limit).
- Host sum at 900 mA host: internal 585 + 2 x 142 (C max) + 2 x TPS2546 89... exceeds 900 mA only if every port draws its limit; the PM rule applies: the eFuse is the fault limit (see 3.3), and firmware (IMON) lowers port limits above 0.85 A.
- TPS2553: 0.1 uA off, 100-140 uA on, 85 mohm: in bus mode two ports add 0.28 mA at worst (both enabled).

## 3.3 Host eFuse with the PM rule (TPS25940ARVCR, datasheet read)
- IOS = 87.5/R(kohm) A typ; window -9 %/+8 % (p5). R_ILIM 88.7 k gives 0.90 / 0.99 / 1.07 A min/typ/max: never below 900 mA over -40..125 C (rule 2 met at the datasheet limit), but the max is 1.07 A, 7 % above the PM's 1.0 A. A +-5 % window is not available in this part. Alternative R 90.9 k: 0.88 / 0.96 / 1.04 A (rule 1 almost, rule 2 violated by 20 mA at min). Chosen: 88.7 k; report the 1.07 A max to the PM. Fast-trip 1.5 x ILIM + 0.375 A (1.86 A) for hard shorts.
- 1.5 A host: 51.1 k parallel switch: 87.5/51.1 = 1.71 A typ is too high for a 1.5 A host; nominal 1.5 A needs R 58.3 k (1.50 A typ, 1.37-1.62 window): chosen 59 k. 3 A host: 29.4 k (2.98 A typ, 2.7-3.2). All switched in parallel resistors from NFETs; the MCU off leaves 88.7 k.
- IMON 52.3 uA/A into R_IMON (>= 1 k): 1 k gives 52 mV/A; use 10 k = 0.52 V/A for ADC use (max 6 V on pin, fine up to 3 A = 1.6 V).
- Quiescent (p5): 210 uA enabled, 95 uA with DEVSLP; replaces my earlier 0.5 mA estimate.

## 3.4 CH224D VBUS limits vs the SMBJ22A clamp
- CH224D abs max (p13): VBUS 24 V, CC1/CC2 20 V, other pins VDD+0.5. SMBJ22A Vc = 35.5 V at 16.9 A: it exceeds both (CC TVS needed too). No unidirectional TVS conducts below 24 V and still stands off 21 V (PD 20 V +5 %), so the VBUS pin needs its own limit: 100 ohm (0603, 0.25 W) in series with the VBUS pin, 22 V zener (BZT52C22, SOD-123) from the pin to GND, 1 uF/50 V at the pin (ceramic, to GND). At the TVS clamp the zener carries (35.5-22)/100 = 135 mA for the TVS pulse only; pin clamp about 22.5 V < 24 V. Normal drop: Icc (not listed for the D, assume <= 3 mA) x 100 ohm = 0.3 V: acceptable at 5 V because VDD is 4.7 V LDO output (check by simulation or at bring-up). CC pins: bidirectional TVS with Vrwm 12-13 V (clamp about 19 V) on each CC, or a 15 V zener pair; CC to VBUS cable faults will stress it, accepted.
- CH224D has NO PG pin. Sink path P-FETs (AO4407A, Vgs max +-20 V) must be driven without it: gate to GND through 10 k at the source side, a 12 V zener gate-source clamp (Vgs max 20 V < 21 V VBUS), so the FETs turn on at attach (5 V) and conduct before the 20 V contract (inrush into the buck input caps limited only by the adapter: add 1 nF dV/dt capacitor on the gate or accept). Alternative: CH224A (PG pin, I2C, 32 V abs max) needs ESSOP10/DFN10: not chosen (stock unknown). CH224D: 20 V = CFG1/CFG2/CFG3 open and DRV open (Rset NC), NMOS# to GND, ISP/ISN to GND.

## 3.5 Inductors
Peak current from the datasheet-ripple formula at the low edge of the fSW window (360 kHz), L = 8.2 uH, Vin max 21 V:
- 9 V / 2.22 A: dI = 9 x (1-9/21)/(8.2u x 360k) = 1.74 A pk-pk, Ipk = 3.09 A.
- 5 V / 3 A: dI = 5 x 0.762/2.95 = 1.29 A, Ipk = 3.65 A; with the 3.3 A source OCP: 3.95 A.
- 5V_SYS 5.1 V / 4.3 A peak: dI = 1.31 A, Ipk = 4.95 A.
(TI's own 400 kHz BOM uses 4.7 uH: dI would be 3.0 A at 9 V; 8.2 uH keeps it at 1.7 A.)
- Chosen for all three bucks: FEXL0660A-8R2M, LCSC C5378547, 8.2 uH, Isat 8.4 A, Irms 8 A, DCR 26.4 mohm, 6.6 x 6.4 mm, $1.09 at 100, stock 550 (183 boards at 3 per board). Isat margin 70 % at 4.95 A. Loss at 2.22 A: 0.13 W per C port.
- Stocked fallback: FXL0630-8R2-M, C167222, Isat 6 A, Irms 5 A, DCR 60 mohm, 7 x 6.6 mm, 47730 in stock, $0.1117 at 150. Isat margin 21 % at 4.95 A (5V_SYS) and 52 % at 3.95 A (C ports). Extra loss vs the chosen part 0.17 W per C port, 0.4 W at 4 A on 5V_SYS: inside the 0.7 W peak margin of the budget for the two C ports alone but not for all three: use it only for the two C ports if FEXL stock runs out.
- LM61460AFSQRJRRQ1 stock: C2876600 has 145 pieces (price $1.51 at 100, $1.41 at 500; a web snippet showed 392 earlier), 3 per board. Alternatives with FPWM: (a) order a reel through JLC global sourcing or Digikey/Mouser (the part is listed at Digikey as LM61460AFSQRJRRQ1); (b) LM61440AFSQRJRRQ1 (4 A, same pinout/footprint, FPWM): stock at LCSC not found (the AUTO variant C3188034 has 15); (c) LMR33630CDDAR (3 A, FPWM, SO-PowerPAD-8, 3.8-36 V): fits the 9 V/2.22 A ports only (5 V/3 A at the 3 A limit has no margin), different footprint, not verified at LCSC.
- BOM changes from the datasheet: 5 V point RFBB 24.9 k, RT 33.2 k (400 kHz, spread span only +-2 %, so RT 33.2 k is right, not 31.6 k), CBOOT 100 nF, BIAS to Vout (< 12 V), COUT 4 x 22 uF.

## 3.6 Library items (verified)
- Symbols in lib/symbols/kb_power.kicad_sym: LM61460AFS, TPS2546 (iteration 2), IP2736U (25 pins), CH224D (21 pins, EP = pin 21), TPS25940A (20 pins + PAD 21), TPS2553 (6 pins). Pin numbers from notes/datasheets/*.md.
- Footprints in lib/footprints/kb_power.pretty: VQFN-HR-14_RJR0014A_4.0x3.5mm (replaces the PRELIM footprint, deleted; KiCad has no RJR VQFN-HR-14; L-shaped pads 1, 5, 9, 11 are custom pads read from the TI drawing by pixel scaling: re-check against the drawing at layout release), QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_IP2736U, WQFN-20_RVC0020A_4.0x3.0mm_P0.5mm_EP2.6x1.6mm. CH224D uses KiCad's QFN-20-1EP_3x3mm_P0.4mm_EP1.65x1.65mm (datasheet EP 1.8 x 1.8, pitch 0.4: matches); TPS2553 uses KiCad Package_TO_SOT_SMD:SOT-23-6 (pads 1-6 as in the DBV pin table).
- Scratch check (a folder inside the project, because /tmp is outside the permitted paths; deleted afterwards): one component per symbol with every pin on a net, `libraries: [../../lib]`. `hwflow check` reported no pin/pad mismatch and no package_pads/package_pitch error for U1 (LM61460, 14 pads), IP2736U (25), CH224D (21), TPS25940A (21), TPS2553 (6) and TPS2546 (17); the only errors were the artificial single-connection nets and unpowered power_in nets of the scratch design.
- Still open for layout: thermal vias are not part of the new footprints (IP2736U datasheet: 9 x 0.3 mm on a 0.9 mm grid; TPS25940A: 6 x 0.2 mm; use `thermal_vias` in design.yaml).

## 3.7 Open risks (iteration 3)
1. IP2736U_EF: the factory PDO list (5 V/3 A + 9 V/2.22 A), current-limit setting, quiescent current and CC behaviour with VIN = 0 are not in the datasheet. Get a sample or Injoinic's PDO table before layout release. Single source (Injoinic).
2. R19 with the PM rule: the TPS25940A at R_ILIM 88.7 k guarantees >= 0.90 A but its max is 1.07 A (not <= 1.0 A): the part's +9 %/-8 % window cannot meet both limits. PM to accept 1.07 A max or choose a +-5 % eFuse.
3. LM61460AFSQRJRRQ1: 145 in stock at LCSC (3 per board): about 48 boards. Reel through JLC global sourcing, Digikey or Mouser for more.
4. FEXL0660A-8R2M stock 550 (about 183 boards at 3 per board); the fallback C167222 is stocked (47730) with 0.17 W more loss per C port.
5. CH224D VBUS pin protection (100 ohm + 22 V zener) costs up to 0.3 V at 5 V VBUS if its supply current is 3 mA (current not in the datasheet): check at bring-up or with a more precise Icc.
6. CH224D has no PG: the sink FETs conduct from 5 V attach; inrush into the buck input capacitors is limited only by the adapter (add a gate RC or accept).
7. TPS2553 OUT abs max 7 V: Q_bus (series P-FET) is mandatory; the TPS2553 reverse protection alone is not enough against the 9 V port voltage.
8. LM61460 land pattern L-pads were read from the PDF image by pixel scaling (about 0.03 mm accuracy): re-check at layout release. BIAS pin: tie to Vout (5 V and 9 V both under 12 V).
9. No vendor SPICE models: all parts use fit models (spice.source: fit).
