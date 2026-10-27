# Parts: POWER group (stage 3)

Date 2026-10-20. Sources: LCSC/JLCPCB product pages (stock and qty-10 price read the same day), TI product page and HTML datasheet extract (SLVSGS9G), Vishay and LCSC listings. TI and Vishay PDFs could not be converted to text here (pdftotext not permitted, WebFetch does not decode PDFs), so the electrical tables of the TPS61299 were NOT read; values from the datasheet are marked (DS) only where a text source gave them. Everything marked (confirm) must be checked against the PDF before the circuit stage freezes.

## Table

| Ref | Role | MPN | LCSC | Price qty 10 | Stock | Symbol | Footprint | Height | Key numbers |
|---|---|---|---|---|---|---|---|---|---|
| U2 | Boost 3.3 V | TPS61299DRLR (1.2 A limit, true shutdown) | C41689719 | 1.898 USD | 3698 | none in KiCad: project symbol (pins 1 VIN, 2 SW, 3 EN, 4 VSEL, 5 VOUT, 6 GND) | Package_TO_SOT_SMD:SOT-563 (pads 1-6 match) | <= 0.6 mm | VIN 0.5-5.5 V, start 0.7 V, Iq 95 nA into VOUT (DS), input current limit 1.2 A typ, Rds(on) 150 mOhm high / 88 mOhm low at VOUT 5 V (DS, search extract), eff up to 94 % at 200 mA, 91 % at 10 uA (3.6 V to 5 V) |
| U2 alt | Same, 1.9 A | TPS612997DRLR / TPS612997YBHR (WCSP 0.9x1.3) | YBHR: C21713664 | 2.32 USD | 4 | same | WCSP not assemblable at JLCPCB standard; DRLR not found at LCSC | | 1.9 A (1.5-2.3 A range) input limit, 1.8-5.0 V output |
| Q1 | Reverse-polarity ideal diode | 2 x CJ2305 in parallel (P-ch 12 V, Vgs +-8 V, Vth 0.9 V) | C8549 | 0.042 each | 169070 | Transistor_FET:AO3401A (pins 1 G, 2 S, 3 D, same SOT-23 pinout) or Device:Q_PMOS_GSD | Package_TO_SOT_SMD:SOT-23 (pad 1 G, 2 S, 3 D) | 1.1 mm | Rds(on) 90 mOhm at Vgs -1.8 V (LCSC attribute) per FET, 45 mOhm paralleled: 31 mV at 0.7 A, meets R4 50 mV. Leakage: IDSS spec only at -12 V, not read (confirm <= 1 uA each at -3.4 V) |
| Q1 alt | single FET | Si2333CDS-T1-GE3 (12 V, 35 mOhm at -4.5 V) | C48179 | 1.20 | 1429 | same | SOT-23 | 1.1 | 1.8 V value not read (confirm). Si2333DDS shows 63 mOhm at -1.8 V (search result), 44 mV at 0.7 A, but no LCSC code found |
| Q2 | VBAT divider switch | CJ2305 | C8549 | 0.042 | 169070 | same | SOT-23 | 1.1 | 90 mOhm at -1.8 V; load 11 uA so Rds irrelevant; Vgs +-8 V: GPIO 3.3 V high with source 1.8 V gives +1.5 V, fine |
| L1 | Boost inductor | 1 uH, Isat >= 2.3 A (covers 1.9 A variant), DCR <= 60 mOhm, 2016 | LCSC code NOT found (hwflow inductors index returns 404) | ~0.1-0.3 | ? | Device:L | Inductor_SMD:L_Murata_DFE201610P or L_Cenker_CKCS201610 | <= 1.0 mm | TI recommends 1 uH (product page). Candidates: Murata DFE201610E-1R0M, Cenker CKCS201610 1R0; pick by LCSC stock |
| C1 | Bulk on VBAT_P | Al electrolytic, radial, 6.3 V or 10 V, low-leakage series, 6800 uF (see change 2) | NOT found (index and web search gave nothing) | ~0.5-1 | ? | Device:C_Polarized | Capacitor_THT:CP_Radial_D10.0mm_P5.00mm for 4700 uF (D12.5 for 6800 uF); lying | 10 to 12.5 mm (diameter, lying) | leakage must be bench measured (C7) |
| C2, C3 | Boost output 10 uF, input 2.2 uF-10 uF | 10 uF 0805 X5R/X7R >= 10 V | C15850 (10 uF 25 V 0805, basic, confirm) | ~0.03 | high | Device:C | Capacitor_SMD:C_0805_2012Metric | 1.25 mm | TI recommends Cin 2.2 uF, Cout 10 uF; DC bias derating: use 25 V part, 2 in parallel on VOUT |
| BT1 | 2 x AAA contacts | BH-AAA-B5AA001 x2 (horizontal SMT holder, 1 cell each, Myoung) | C964881 | 1.21 each (2.42) | 8240 | Device:Battery_Cell x2 | none in KiCad: project footprint needed from datasheet (Keystone 2468 is 2xAAA THT, 13 mm: fails R2) | not given (confirm; target <= 12 mm) | Alternative loose clips MY-AAA-19-J (C6937104, 0.18) + MY-AAA-20-J (C6937107, 0.14) + MY-AAA-07 (C6937109, 0.12) per cell: 0.4 USD per cell but a bare clip needs a plastic seat |

## Alternatives and reasons
- Boost: TPS61299DRLR is the only member of the family that JLCPCB stocks in an assemblable package. The 1.9 A TPS612997 exists (DRL package per TI table) but LCSC has only the YBH WCSP with 4 pieces. TPS61099DRV (C2842395, 1.01 USD, 0.8 A) fails R3. TPS613221A (C2071121, 0.18 USD, 3.3 V fixed, SOT-23-5, start 0.9 V) is the cheap fallback if the burst requirement is relaxed (current limit not read).
- Q1: 2 x CJ2305 is cheapest with a published 1.8 V Rds(on). A single Si2333CDS costs 1.2 USD.
- Hold-up: no Al cap or supercap code could be confirmed. Alternative layout: 2 x 3300 uF 6.3 V (D10 x 20 each).

## Architecture numbers that changed
1. **The part is not "fixed 3.3 V".** TPS61299 has a VSEL pin: output 1.8-5.5 V chosen by a resistor VSEL-to-GND; a short to GND gives 3.3 V (search-result summary of Table 7-1, confirm). No external feedback divider, no 10 MOhm divider. Fixed 3.3 V / 1.9 A orderable code does not exist.
2. **Input current limit is 1.2 A typ (DRLR), not 1.9 A.** The 1.9 A is TPS612997 (not stocked in SOT563). Re-check of R3 at 1.8 V, 1 Ohm, 350 mA, 5 ms: boost input current at VIN = 1.0 V would be 1.36 A > 1.2 A, so VIN must stay above 1.13 V (typ limit) or about 1.36 V if the minimum limit is 1.0 A. With 4.7 mF the droop from 1.8 V is about 0.6 A x 5 ms / 4.7 mF = 0.64 V, so VIN falls to about 1.2 V: marginal at the typical limit, fails at the minimum limit. C1 should rise to about 6.8 mF (nominal), which also fixes the point that 4.7 mF - 20 % = 3.76 mF is below the 3.9 mF target. SPICE (circuit stage) must decide; the load sees the 10 uF + output C for the first microseconds.
3. IOUT capability at VIN 1.8 V: 1.8 x 1.2 x 0.85 / 3.3 = 0.56 A (not 0.88 A). Still >= 0.35 A.
4. Rds(on) of Q1: 45 mOhm paralleled (31 mV), from the 90 mOhm at 1.8 V of CJ2305. The "<= 60 mOhm" target is met only with two FETs.
5. Divider switch: CJ2305 (90 mOhm at 1.8 V) instead of unspecified P-FET.
6. Height: AAA holder BH-AAA-B5AA001 height not published in the text sources. Face A stack 12.0 mm is unconfirmed; R2 margin is 0.6 mm.

## Requirements at risk
- R2 depth: BT1 height unknown (needs the Myoung PDF or a 3D model). Keystone 2468 is out.
- R3 burst: depends on the TPS61299 minimum current limit and C1 (change 2).
- R4: IDSS of CJ2305 at -3.4 V not read (typ nA, spec at -12 V is a few uA); check the PDF.
- R30: subtotal of the power group is about 1.90 (U2) + 0.08 (Q1) + 0.04 (Q2) + 0.2 (L1) + 0.9 (C1, estimate) + 0.1 (caps) + 2.42 (holders) = about 5.6 USD.

## SPICE
- TI publishes PSpice models for TPS61299 (product page lists SLVME14, SLVME79, SLVME62; which one is the x/xA family is unconfirmed). URL pattern https://www.ti.com/lit/zip/slvme14 (not downloaded: curl and zip fetch not permitted). Set `spice.source: vendor` only after download into `models/`.
- Built-in fallback: BUCK_AVG ports in out gnd, VOUT=3.3, EFF=0.88, ILIM=1.2 (A, input limit), IQ=0.095u (at 6 uA load use EFF 0.80).
- CJ2305: PMOS_LOGIC with Rds(on) 90 mOhm at Vgs -1.8 V, Vth -0.9 V (no vendor model; source fit).
