# Parts: POWER group (stage 3), home-thermostat-v2

Date 2026-10-20. Sources: JLCPCB index (`hwflow parts`), LCSC/JLCPCB part pages, TI product pages, KiCad library via `hwflow lib`. The TI PDF could not be converted to text in this session (pdftotext and curl need approval; WebFetch returned binary). Every number marked "verify" must be read from the TI PDF (SLVSD88) before stage 4 SPICE is frozen. Facts also in `notes/datasheets/tps61099.md` and `notes/datasheets/ymin-npxl.md`.

## 0. Issues that affect the architecture (read first)

1. **Variant error in architecture.md.** The TPS61099x family is TPS610993 = 3.0 V, TPS610994 = 3.3 V, TPS610995 = 3.6 V, TPS610996 = 4.5 V, TPS610997 = 5.0 V (KiCad descriptions and Digikey "TPS610995DRVT ... Fixed 3.6V"). Architecture says "TPS610995 ... 3.3 V fixed": wrong. A 3.6 V part with +2 % tolerance gives 3.67 V, which breaks R1 (3.0-3.6 V) and the ESP32-C3 limit. The architecture needs **TPS610994** (3.3 V). The rest of the arithmetic (Iq, ILIM, 0.7 V start) is the same for the whole family.
2. **TPS610994DRVR is not in the JLCPCB index.** Only the adjustable TPS61099DRVR (C2842395, WSON-6 2x2, stock 8746, 1.01 USD) is. R29 needs stock >= 15 at order time, so one of these must happen: (a) check lcsc.com / jlcpcb.com for TPS610994DRVR by hand (not found by search or index); (b) use the adjustable TPS61099DRVR with an FB divider (section 1b, costs about 0.3-0.5 uA of the 1.1 uA margin in R3, and FB bias current adds 1-2 % Vout error); (c) hand-solder or consign TPS610994DRVR. Decision for the parent / product person; stage 4 can proceed with either because the symbols have the same pins.
3. **Bulk capacitor leakage (C7) cannot be closed from datasheet.** The only 8200 uF / 6.3 V / 12.5 mm part with stock is a **polymer** aluminium capacitor (Ymin NPXL1800J822MJTM). Polymer series usually specify leakage as 0.1-0.2 x C x V (up to about 10 mA for this part at 6.3 V), much worse than the wet-electrolytic 0.01 CV (517 uA) that C7 assumed. The 3.1 uA acceptance in R3 is then a bench test on first article only, with a real risk of failing. I could not read the Ymin datasheet (JLCPCB CDN PDF). Fallbacks in section 4. Needs a decision before layout is frozen.
4. **AA end clips: height not verified.** LCSC lists MY-AA-04 (C2979179) as "Battery Clip compatible with AAA battery SMT reflow" while the JLCPCB listing says "No. 5" (= AA in Chinese naming); neither page gives dimensions. C6 fix (parts <= 1.2 mm under the cell) needs the cell axis at >= 8.75 mm above the PCB surface (7.25 mm cell radius + 1.2 mm part + 0.3 mm clearance). Order samples and measure before layout (section 5). Clip height is therefore **unknown**.
5. **Reverse polarity: the architecture has no protection** (mechanical: clip shape + silkscreen; section 7.2 of architecture.md). No part to select. Risk: a reversed cell puts about 1.5 V on VOUT through the converter body diode and the 22 uF input cap; the TPS61099 abs-max for reverse VIN must be checked in the PDF.
6. **Tooling bug (not edited):** `hwflow parts capacitors|resistors|mosfets ... package=...` crashes (stock or package is None for ceramic/resistor rows) and the `inductors` category returns HTTP 404. Ceramic, resistor and MOSFET picks below come from LCSC pages, not from the index.

## 1. Boost converter U1

| Item | Value |
|---|---|
| Role | U1 boost, always on, 3.3 V rail |
| Chosen MPN | **TPS610994DRVR** (3.3 V fixed), WSON-6 2x2 mm |
| LCSC | not found in JLCPCB index (see issue 2) |
| Buildable now | **TPS61099DRVR** C2842395, stock 8746, 1.01 USD (qty 1; qty 10 price not in index, assume about 0.95) |
| Datasheet | https://www.ti.com/lit/ds/symlink/tps61099.pdf (SLVSD88L) |
| KiCad symbol | `Regulator_Switching:TPS610994DRV` (3.3 V) or `Regulator_Switching:TPS61099DRV` (adjustable); same pins |
| KiCad footprint | `Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm` (symbol default; pads 1-6 + EP pad 7) |
| Pins (lib show) | 1 GND (power_in), 2 VOUT (power_out), 3 FB (passive), 4 EN (input), 5 SW (power_in), 6 VI (power_in), 7 GND (EP, hidden) |
| Pad check (lib pads) | pads 1,2,3 left side top to bottom, 6,5,4 right side, 7 = EP: matches pin table; connect EP (pad 7) to GND |

Wiring: VI to cell + (with 10 uF-22 uF MLCC at pins 6/1), EN tied to VI (always on; EN threshold verify), FB to GND for the fixed part, SW to L, VOUT to output caps. For the adjustable part see 1b.

SPICE (behavioural / averaged), source `fit` from product page and architecture; verify against PDF:

| Parameter | Value | Status |
|---|---|---|
| VOUT | 3.3 V nominal; tolerance assumed +/-2 % (3.234-3.366 V) | verify in PDF (fixed versions) |
| Vin start / min run | 0.7 V (TI product page) | page |
| ILIM peak switch current | 0.8 A min, 1.0 A typ (page/search), max assumed 1.2 A | max verify |
| IQ | 0.4 uA (VIN) + 0.6 uA (VOUT) | page |
| EFF at 10 uA, Vin 1.2 V | 0.70 (TI "up to 75 %" derated by the architecture) | assumption |
| EFF at 10 mA | 0.85 (assumption; TI: up to 93 % over 10-300 mA) | verify from curve |
| EFF at 300 mA | 0.85 at Vin 1.2 V; 0.80 at Vin 0.6-0.7 V | assumption |
| Switching | synchronous PFM, no fixed frequency at light load | page |
| Recommended L | 2.2 - 6.8 uH (typical application 2.2 uH) | search summary of PDF |
| Recommended Cin / Cout | 10 uF in; 22 - 100 uF out (typical 10 uF/10 uF) | search summary of PDF |
| Package | WSON-6 DRV 2x2 mm, also DSBGA 6-ball YFF | page |

Alternatives: MCP1640-class (about 19 uA Iq, rejected in architecture); TPS61291 (bypass mode, rejected). No equivalent 0.7 V-start / 1 uA-Iq boost with a JLCPCB listing was found in the index.

### 1b. If the adjustable TPS61099DRVR is used (3.3 V)

VFB = 500 mV typ, FB bias 0.01 uA typ (search summary of PDF; verify). Vout = 0.5 x (1 + Rtop / Rbot). Choice: Rbot = 1.0 MOhm, Rtop = 5.62 MOhm (0.1 %, 0603; 5.6 MOhm E24 also ok): Vout = 3.31 V, divider current 0.5 uA. FB bias error 10 nA x (5.62 M || 1 M = 0.85 M) = 8.5 mV = +1.7 % -> Vout up to about 3.37 V (+ VFB tolerance, to verify) which still sits in 3.0-3.6 V but eats 0.5 uA of the R3 margin (1.1 -> 0.6 uA) and tightens the R4 droop margin (rail minimum at the droop end is a function of the nominal, so a higher nominal helps). Add 10 pF across Rtop for FB noise. Trim Rtop in stage 4 once the PDF number for VFB tolerance is read.

## 2. Inductor L1

| Item | Value |
|---|---|
| Chosen | **Murata LQM2HPN2R2MG0L**, 2.2 uH +/-20 %, 1008 (2520 metric) |
| LCSC / stock / price | C86092, stock 74430, 0.188 USD (qty 1; qty 10 about 0.17) |
| Datasheet | Murata LQM2HP series, https://www.murata.com (search LQM2HPN2R2MG0L); not downloaded |
| Rated current / DCR | 1.3 A / 100 mOhm (index text) |
| KiCad symbol | `Device:L`, pins 1 and 2 (passive) |
| KiCad footprint | `Inductor_SMD:L_1008_2520Metric`, pads 1 (-1.07, 0) and 2 (+1.07, 0) |
| Height | about 1.0 mm (Murata LQM2HP; verify) |

Why: only 2.2 uH part >= 1.2 A with a JLCPCB listing found in the index (inductors category is broken, found via `any` search of the 1008 package). Isat margin against ILIM max (1.2 A assumed) is thin: the multilayer type's 1.3 A is the self-heating rating; Isat for -30 % L is not in the index. DCR loss at 0.6 A average: 0.6^2 x 0.10 = 36 mW (about 10 % of the 0.36 W input at the R4 corner Vin 0.6 V); this lowers the 0.80 efficiency assumption of section 2.1 slightly (about 0.75). Stage 4 SPICE should use L = 2.2 uH, DCR = 0.1 Ohm, Isat = 1.3 A.

Alternatives: MPL2520S3R3MHT (C98339, 3.3 uH, 1.2 A, 100 mOhm, stock 9782) - lower ripple, same DCR; MLP2012H2R2MT0S1 (C383369, 2.2 uH, 1 A, 195 mOhm, stock 55932) - Isat below ILIM max, DCR double: rejected. A shielded wire-wound 2.2 uH, 2 A+, <= 50 mOhm (e.g. 2.5 x 2 or 3 x 3 mm) would be better; none could be listed through the index (broken category), so a manual LCSC search is recommended if stage 4 shows efficiency below budget.

## 3. Ceramics

| Ref role | MPN | LCSC | Stock | Price qty 10 | Symbol | Footprint | Key params |
|---|---|---|---|---|---|---|---|
| C_in (boost input, also takes contact ESD per architecture 7) | Samsung CL21A226MQQNNNE 22 uF 6.3 V X5R | C5674 | 6.79 M | 0.0128 USD | `Device:C` (pins 1,2) | `Capacitor_SMD:C_0805_2012Metric` | 22 uF +/-20 %, X5R, 6.3 V; DC bias at 1.65 V small (derate to about 85 %) |
| C_out (boost output, next to VOUT pin) | same part | C5674 | same | same | `Device:C` | same | 22 uF; datasheet range 22-100 uF; the bulk cap adds the rest |
| C_dec 100 nF VBAT/rail | not selected here (belongs to other groups) | - | - | - | - | - | - |

Datasheet: https://www.lcsc.com/product-detail/C5674.html . Alternatives: 10 uF 0805 X5R (C15850 is 25 V class, not verified here), 2 x 10 uF 0603. Stock and price from LCSC page search result, not from `hwflow parts` (broken for capacitor package filter). SPICE: C = 22 uF, ESR 3 mOhm, DC-bias derating 0.85 at 3.3 V on the output (6.3 V part at 3.3 V: derate about 0.6, so use 13 uF effective on the output).

## 4. Bulk buffer capacitor C_bulk

| Item | Value |
|---|---|
| Chosen | **Ymin NPXL1800J822MJTM**, 8200 uF +/-20 %, 6.3 V, polymer aluminium, radial D12.5 x L18 mm, 5 mm pitch, -55..105 C, 2000 h @105 C |
| LCSC / stock / price | C2891077, stock 365, 1.053 USD (qty 1; qty 10 about 1.0; the architecture estimated 0.25) |
| ESR | 7 mOhm @ 100 kHz (JLCPCB page); architecture assumed 60 mOhm |
| Ripple | 9.45 A @ 100 kHz |
| Datasheet | JLCPCB CDN PDF linked from https://jlcpcb.com/partdetail/NPXL1800J822MJTM/C2891077 (not readable here) |
| KiCad symbol | `Device:C_Polarized` (pin 1 +, pin 2 -) |
| KiCad footprint | `Capacitor_THT:CP_Radial_D12.5mm_P5.00mm`, pad 1 (rect, +) at x = 0, pad 2 at x = 5.0 mm; bbox 13 x 13 mm; THT, so hand-solder or JLCPCB THT assembly |
| Height | 18 mm (fits the 38 mm depth) |

SPICE: C = 8200 uF (6560 uF at -20 %, the R4 corner), ESR = 7 mOhm (use 15 mOhm at 0 C margin), ESL 10 nH, **leakage: unknown for this part**. The 0.01 CV figure of wet types (517 uA) is not a valid model; polymer types are usually 0.1-0.2 CV (spec after 2 minutes at rated voltage). The 2 uA budget of section 4.1 must become a bench-test pass/fail criterion (R3/C7), and the datasheet value must be read from the Ymin PDF before R3 can be marked verified.

Alternatives (all from `hwflow parts capacitors capacitance=...`):

| Option | LCSC | Stock | Price | Why not first |
|---|---|---|---|---|
| 2 x Polymer 4700 uF 6.3 V D10 x L15 (4726R31015R00), 9400 uF total | C2887196 | 10844 | 0.4247 each | two parts, 2 x 10 mm footprint (about 21 x 11 mm), same leakage doubt; best stock |
| Polymer 6800 uF 6.3 V D12.5 x L16 (NPXL1600J682MJTM), 5440 uF at -20 % | C2891076 | 387 | 0.8997 | meets the 4.4 mF effective minimum but with only 24 % margin (droop 0.19 -> 0.23 V; R4 margin 0.03 V disappears) |
| Wet Panasonic EEUFR0J822 8200 uF 6.3 V D16 x L25, 14 mOhm | C242144 | 0 | 1.428 | out of stock, 16 mm can; lowest leakage class (0.01 CV spec, real value 1-2 uA after conditioning) |
| Wet 6800 uF 6.3 V D16 x L25 (EKY-6R3ELL682ML25S) | C709512 | 354 | 0.6557 | 16 mm can instead of 12.5 mm (+60 mm2 area, conflicts with C6 area balance) |

Recommendation: keep C2891077 as first choice for R4 (ESR 7 mOhm instead of 60 mOhm raises the R4 margin), measure leakage on the first article at 3.3 V after 24 h conditioning, and keep the wet 6800 uF C709512 as the fallback if leakage exceeds 3.1 uA (needs product decision on 16 mm can and area, conflicts.md C6/C7).

## 5. AA battery contacts BT1

| Item | Value |
|---|---|
| Chosen | **MYOUNG MY-AA-04** battery clip (SMT reflow), x1 for one end, with MY-AA-03 as the other end |
| LCSC | MY-AA-04 C2979179, stock 17549, 0.34 USD (qty 5+: 0.3263); MY-AA-03 C19184098, stock 2094, 0.1297 USD (spring contact) |
| Datasheet | PDF linked on https://www.lcsc.com/product-detail/C2979179.html (dimensions not shown in the page text) |
| KiCad symbol | `Device:Battery_Cell` for the cell, pins 1 (+) and 2 (-); contacts as `Connector_Generic:Conn_01x01` (pin 1) x2 |
| KiCad footprint | none in the KiCad libraries for MY-AA-0x. `Battery:BatteryClip_Keystone_54_D16-19mm` exists but is for 17-19 mm cells, THT, three pads: not AA. Needs a project footprint from the MYOUNG drawing (EasyEDA footprint exists on LCSC). |
| Height | **not verified**; requirement: cell axis >= 8.75 mm above the PCB (1.2 mm parts + 0.3 mm gap + 7.25 mm cell radius), i.e. clip contact point height >= 8.75 mm |
| Mount | SMT reflow per LCSC text ("SMT Reflow"); hand-solder fallback allowed by R29 |

Risks: (1) LCSC says "AAA" in the title while JLCPCB says "No. 5" (AA); (2) no pad drawing, no height; (3) the 57 mm clip-to-clip spacing in the floorplan (AA length 50.5 mm + clips) needs the real clip length. Alternatives with a KiCad footprint: Keystone 2460 1xAA full holder (`Battery:BatteryHolder_Keystone_2460_1xAA`), THT, hand-soldered; the body raises the cell but leaves no room under it (C6 fix lost). Other LCSC AA parts: BH-AA-A1AJ029 (C7525429, AA holder, 0.71 USD, stock 3018), MY-AA-03 pair. Next step: order 10 MY-AA-04 + MY-AA-03 samples, measure axis height; if < 8.75 mm, C6 reverts to the open conflict.

## 6. Reverse-polarity protection

Architecture (interface table, BT1): **none electrically**. Polarity is set by clip shape and silkscreen; no series diode because 0.3 V is 30 % of a 1 V cell. 22 uF MLCC at the boost input absorbs contact ESD. No part selected. Optional improvement (not in architecture, needs product decision): a P-channel FET in the cell + path (gate to ground, source to VIN), e.g. a PFET with Vgs(th) well below 0.9 V is not available in SOT-23; a 0.9 V cell cannot enhance a normal PFET, so an ideal-diode IC for sub-1 V (none found in the index) would be needed. Recommendation: stay mechanical.

## 7. VBAT divider (R5)

Per architecture section 2.6 / 5.2: 33 k (top, cell +) and 100 k (bottom, to the NMOS drain), both 0.1 %, 25 ppm; low-side NMOS gated by VSW; 100 nF at the ADC node.

| Ref role | MPN | LCSC | Stock | Price | Symbol | Footprint | Params |
|---|---|---|---|---|---|---|---|
| R_top 33 k | Panasonic ERA-3AEB333V (0.1 %, 25 ppm thin film, 0603) | not confirmed (LCSC search returned only 1 %/100 ppm UNI-ROYAL parts: 0603WAF3302T5E-class) | - | - | `Device:R` pins 1,2 | `Resistor_SMD:R_0603_1608Metric` (pads 1,2) | 33 kOhm +/-0.1 %, +/-25 ppm/C |
| R_bot 100 k | Panasonic ERA-3AEB104V (same family) | not confirmed | - | - | `Device:R` | same | 100 kOhm +/-0.1 %, +/-25 ppm/C |
| Q_div low-side NMOS | AOS AO3400A, SOT-23 | C20917 | 434180 | 0.0737 USD (qty 5), about 0.058 at 50 | `Device:Q_NMOS_GSD`: pin 1 G, 2 S, 3 D | `Package_TO_SOT_SMD:SOT-23`: pad 1 (-0.94,-0.95), 2 (-0.94,0.95), 3 (0.94,0) - the standard SOT-23 pad 1 = G, pad 2 = S, pad 3 = D matches the symbol | Vth 0.65-1.45 V (verify), 30 V, Rds(on) about 30 mOhm at 2.5 V; Idss spec 1 uA at 30 V is far above the architecture allowance of 0.05 uA, real leakage at Vds = 1.65 V is nA-class: verify by bench |

The 0.1 % / 25 ppm resistors are the open item: the index `resistors` category crashes with these filters and the web search found no LCSC number. R29 requires a JLCPCB-stocked part; if none exists, the accepted alternative is 0.1 % 50 ppm (R5 budget: TCR row is 0.3 mV of 16.6 mV used, so 50 ppm costs 0.3 mV more and still passes) from the same Panasonic ERA-3 or Yageo RT0603BRD series. This pick must be resolved in stage 3b with the signal-chain group's resistor search (same 0.1 % R_ref 10 k parts).

## 8. Summary of key SPICE inputs

| Block | Parameter | Value |
|---|---|---|
| U1 | VOUT / ILIM / IQ | 3.3 V / 0.8-1.0 A (1.2 A max, verify) / 1.0 uA |
| U1 | EFF 10 uA / 10 mA / 300 mA | 0.70 / 0.85 / 0.85 (0.80 at Vin <= 0.7 V) |
| L1 | L / DCR / Isat | 2.2 uH / 100 mOhm / 1.3 A |
| C_in, C_out | C / ESR | 22 uF / 3 mOhm each (derate to about 13 uF on 3.3 V) |
| C_bulk | C / ESR / leakage | 8200 uF (-20 %: 6560 uF) / 7 mOhm @100 kHz / unknown (polymer: 0.1-0.2 CV spec; bench target 3.1 uA) |
| Q_div | Vth / Rds(on) | 0.65-1.45 V / about 30 mOhm at 2.5 V |
