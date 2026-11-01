# Parts: block group A, POWER (stage 3, 2026-10-31)

Lookups: `hwflow parts` text search answered only for ldos/mosfets (boost/supervisor/watchdog text returned nothing), so LCSC numbers for TPL5010, TPS7A0230 come from web search (JLCPCB/LCSC pages). Datasheets: notes/datasheets/tps61299.md, gw209f.md.

## Table
| ref role | mpn | lcsc | USD @1000 | footprint (pads checked: lib show/pads) | side / method |
|---|---|---|---|---|---|
| U boost | **TPS612994YBHR** (100 mA avg input limit, true shutdown; **WCSP only, no SOT-563 version** - FIX 2026-11-01) | none on LCSC found; TI/DigiKey/Mouser (TrustedParts 7.9 k in stock); JLCPCB consigned part | ~1.09 (TI list) | Package_BGA:Texas_DSBGA-6_0.855x1.255mm_Layout2x3_P0.4mm_LevelB (pads A1 A2 B1 B2 C1 C2, 0.4 mm pitch, 0.23 mm); symbol hw:TPS61299 pin->pad: 1 VIN A1, 2 SW B1, 3 EN C1, 4 VSEL C2, 5 VOUT B2, 6 GND A2; die 1.27 x 0.88 x 0.4 mm | front, SMT (0.4 mm pitch: via-in-pad or 0.1 mm escape, layout risk) |
| L boost | Murata DFE201610E-1R0M 1 uH 2.7 A 57 mOhm | C161082 | 0.20 | Inductor_SMD:L_Murata_DFE201610P (pads +-0.72 mm) h 1.0 | front SMT |
| CIN | 22 uF 0805 X5R 25 V | C45783 (basic) | 0.05 | C_0805 | front SMT |
| COUT | 10 uF 0805 X5R 10 V (>= 4 uF effective at 3.6 V) next to U | basic, pick at circuit | 0.03 | C_0805 | front SMT |
| RSEL | **22.6 k 1 %** 0402 on VSEL = **4.0 V** (was 49.9 k = 3.6 V; Table 7-1 p.14; C < 10 pF) | basic | 0.01 | R_0402 | front SMT |
| C_STORE | CAP-XX GW209F 160 mF 5 V (kept; listed in CAP-XX guide v6.6 2023, MOQ 300; written production/lead-time/price confirmation needed) | none (CAP-XX quote) | ~6 (assumed, unquoted; +3 vs arch, inside the PM's +3-6) | custom: 28.5 x 17.0 x 2.1 mm, 2 terminals; NO KiCad footprint | BACK, HAND SOLDER (not reflow-safe) |
| Q reverse | DMP2035U-7 P-FET 20 V 3.6 A, 41 mOhm @1.8 V, Vth 0.7 V (alt AO3415A C133233, SI2301) | C110499 stock 271k | 0.14 | SOT-23 (G1 S2 D3, symbol Transistor_FET:AO3401A same pins); gate to GND, S to load, D to cell+ | front SMT |
| U LDO GNSS 3.0 V | TPS7A0230PDBVR (Iq 25 nA, 3 nA shutdown, 200 mA, drop 0.36 V @200 mA, Vin 1.5-6 V, -40..125 C) | C3747031 | ~0.30 | SOT-23-5 (DBV) | front SMT |
| U watchdog | TPL5010DDCR (35 nA, 1.8-5.5 V, -40..105 C, DELAY/M_RST resistor sets 60 s) | C473912, stock 84k | 0.21 | **TSOT-23-6 (DDC)**, not the symbol default SOT-23-6: set Package_TO_SOT_SMD:TSOT-23-6; pins Timer:TPL5010 1 VDD 2 GND 3 DELAY 4 DONE 5 WAKE 6 RST | front SMT |
| U supervisor | **TPS3839G33DBZR** (VIT- 3.003/3.080/3.126 V, 150 nA, SOT-23, Power_Supervisor:TPS3839DBZ 1 GND 2 RESET 3 VDD) | **C485802**, 3.4 k stock | 0.23 | SOT-23 | front SMT |
| VBAT divider | block pfet_vbat_divider, values changed (below) | CJ2305 C8549, R 200 k 0.1 % x2, 10 nF | ~0.25 | block | front |

Alternatives (max 2): boost: TPS612996 (500 mA limit, see below) / TPS61299 or MAX17227A (no input limit; rejected). Supercap: CAP-XX HW209F 150 mF 100 mOhm, 5.5 V rated, 85 C (more ESR; hold-up with 120 mF min = 0.12 x 0.35/0.44 = 95 ms, fails) / GS203F 270 mF 30 mOhm 39 x 17 mm (bigger, 2.1 mm). LDO: BCT2014EUK30-TR (C2887819, 1.5 uA Iq, 0.10 USD) / none better. P-FET: SI2301-ZE C5261052. Watchdog/supervisor: no alternative needed, TPS3839 vs TLV809-class.

## Library blocks
- **tps61299_boost: NOT taken.** Its part is the 1.2 A TPS61299 (3.3 V, VSEL to GND) whose input limit is 1.2 A (0.96 A min): alkaline 2.8 V / 4.0 ohm cold would pull the cell to 2.8 - 4.0 x 0.96 = <0 V, i.e. collapse below the 1.8 V PWR-1 floor; we need a 80-120 mA part (TPS612994) and 3.6 V (49.9 k). Same footprint/pinout (SOT-563, DFE201610P, 0805 caps), so the block layout is reusable: take its placement and swap U2 part, VSEL R and CIN 22 uF. Name it in the circuit stage as "tps61299_boost, with U2 TPS612994, R 49.9k from VSEL to GND".
- **max17227a_boost: NOT taken.** Peak current limit 2 A (1.8-2.2 A) with no average input limit: on 2.8 V / 4.0 ohm alkaline, I_in would be limited only by the source -> Vin collapses; also fixed RSEL 3.3 V step set (3.6 V needs another RSEL), 2.96 USD (1) vs ~1.3, and its COUT 22 uF recommendation is also silent on a 160 mF load.
- **pfet_vbat_divider: taken with changes.** Divider 2 x 200 k 0.1 % (block 20 k/10 k), CF 10 nF (block 1 nF), top from BAT_P. Added: NFET level shifter (2N7002/BSS138 class, gate = VBAT_SENSE_EN active high, drain to P-FET gate, 1 Mohm pull-up to BAT_P), because BAT_P up to 3.65 V exceeds a 3.0 V GPIO high and a direct drive leaves the CJ2305 at Vgs = -0.65 V (Vth up to -0.9 V) = leakage. Matches interface.yaml VBAT_SENSE_EN active high. Net +0.03 USD.

## Boost choice (architecture section 5/16 vs datasheet)
Input limit 0.25 A: TPS612995 (250 mA) is Product Preview only (p.3 note), not buyable. Available: 100 mA (TPS612994) and 500 mA (TPS612996). TPS612996 at 500 mA on the alkaline pair: Vin = 2.8 - 4.0 x 0.5 = 0.8 V (< 1.8 V PWR-1 floor, and P_in = 0.4 W, 0.5 V UVLO in sight) -> rejected. TPS612994 chosen (80-120 mA, 120 mA worst):
- alkaline cold: Vin = 2.8 - 4.0 x 0.12 = 2.32 V, P_in 0.278 W, x 0.85 = 0.236 W -> **0.066 A** at 3.6 V (arch 0.106 A); deficit at 0.5 A TX = **0.434 A** (arch 0.39).
- Li-FeS2 cold (3.0 V, 1.5 ohm): Vin 2.82 V, 0.338 W -> 0.08 A; deficit 0.42 A.
- Efficiency 0.85 assumed (datasheet gives 91 % at 10 uA, nothing at 100 mA/2.3 V in text: unverified).

## Supercap hold-up check (section 16 arithmetic redone, Vout 3.6 V -> floor 3.2 V, dV 0.40 V)
t = C x (0.40 - I_def x ESR) / I_def
| case | C | ESR | I_def | t |
|---|---|---|---|---|
| arch (as written) | 0.16 | 55 mOhm | 0.39 | 148 ms |
| nominal, 23 C, TPS612994 | 0.160 | 55 | 0.434 | 0.16 x 0.376 / 0.434 = 139 ms |
| min C -20 %, ESR 66 mOhm, 23 C | 0.128 | 66 | 0.434 | 0.128 x 0.371 / 0.434 = **109 ms** |
| min C, -20 C ESR ~112 mOhm (1.7x, interpolated from 2x at -40 C) | 0.128 | 112 | 0.434 | 0.128 x 0.351 / 0.434 = **104 ms** |
So PWR-7 (100 ms) holds with only 4 % at the corners, before any capacitance loss at -20 C (not in the datasheet text) and before the 3.6 V accuracy (ULPM/PFM regulation tolerance) and before ageing (EDLC -20 % at end of life typical). **Margin is too thin.** Lever (needs PM/architect, nRF9151 VDD 3.0-5.5 V per notes/datasheets/nrf9151.md): set VOUT 4.0 V (VSEL 22.6 k, normal mode): dV 0.80 V -> min-C corner 0.128 x (0.80 - 0.049) / 0.434 = **221 ms**; supercap cells at 2.0 V each (below 2.5 V rating, less leakage); costs LDO dropout margin only (LDO input 4.0 for 3.0 out), boost efficiency ~-1 %, nRF9151 sleep current at 4.0 V to confirm (nrf9151 PS), supervisor threshold stays below 3.2 V. Needs the lead's decision; I have not changed the architecture numbers.
Charge: per connection 0.16 F x 0.4 V = 64 mC at 0.066 A = 1.0 s (arch 0.58 s at 0.106 A... recharge time bounds the TX repetition rate; firmware waits for 3.5 V as written).
**Power-up charge time 0 -> 3.6 V (arch: 1.9 s) is ~11 s** with the TPS61299 start-up limits (p.15): 0-0.5 V at 20 mA: 0.16 x 0.5 / 0.02 = 4.0 s; 0.5-1.8 V at ~80 mA: 2.6 s; 1.8-3.6 V at ~66 mA (alkaline) 4.4 s; total ~11 s (same order for Li-FeS2: 10 s). Stays a no-requirement boot time, but the first attach is delayed.
Leakage: 1 uA max at 2.3 V/cell, 23 C after 120 h (datasheet Table 2; 0.5 typ); at 1.8 V/cell lower; ~5 uA at 70 C (Fig 10 for GW109); ~2 uA est at 50 C. PWR-5 uses 1.0 uA: holds at 23 C with 11.6 uA; at 50 C (+1 uA x 1.6 = +1.6 uA) = 13.2 uA < 15 uA. First days after insertion leakage decays from higher values (120 h to equilibrium): PWR-5 is an equilibrium figure, note for the test.
Dual-cell balance: CAP-XX recommends passive balancing for series cells; 2 x 10 Mohm adds 0.18 uA at 3.6 V (+0.3 uA battery): circuit stage decision, flagged.

## Findings that change architecture numbers
1. Input limit 250 mA is not purchasable (TPS612995 preview): use 100 mA (TPS612994): P_out 0.066 A (not 0.106 A), deficit 0.434 A (not 0.39), hold-up 104-109 ms at corners (arch 148 ms at nominal). PWR-7 100 ms marginal. Remedy options: VOUT 4.0 V (221 ms), or add a second 160 mF cap (cost +3 USD, +2 g), or limit the PM's 100 ms burst.
2. GW209F: Digikey/Octopart show it obsolete/EOL; CAP-XX site lists it on quote (MOQ 300 in 2018 guide); price unpublished; availability at 1000 units to be confirmed by a quote (stage-3 blocker risk). It is NOT reflow-capable (hand solder / hot bar only): the "second-side reflow" in section 16 is wrong. Cost of a hand-soldered part on the back face is not in the 3.0 USD; assume 0.3-0.5 USD labour (assumption).
3. COUT range 4-1000 uF in the TPS61299 datasheet; 160 mF is outside it (stability not guaranteed): bench check; keep 10 uF ceramic at VOUT pin, supercap through its own 55 mOhm ESR.
4. Footprint of GW209F is a placeholder until the guide's Section 20 land drawing is read (body 28.5 x 17.0 x 2.1 mm from guide table p.3). Mass not published: 2 g is an estimate (TME gross 1.08 g incl. packaging).
5. Power-up charge ~11 s, not 1.9 s.
6. TPL5010 package is TSOT-23-6 (DDC), the KiCad symbol defaults to SOT-23-6.

## limits (pasted by circuit stage)
```
U_BOOST (TPS612994): limits: {abs_max_v: 6.5, rec_max_v: 5.5, pins: {VIN: {abs_max_v: 6.5}, VOUT: {abs_max_v: 6.5}}, source: "SLVSGS9G p.5 abs max -0.3..6.5 V", temp_c: [-40, 125]}
  net VDD_3V6: v_max 3.6 * 1.03 = 3.75 V (VSEL 3.6 V accuracy not in text; assumed 3 %; PFM ripple to be read); BAT_P v_max 3.65
C_STORE (GW209F): limits: {abs_max_v: 5.5, rec_max_v: 5.0, temp_c: [-40, 70], source: "GW209 datasheet rev 4.5 Table 1/2 p.1"}  sourcing: "CAP-XX quote, hand solder"
U_LDO (TPS7A0230): abs_max_v 6.5 (verify), rec 6.0 per Vin range; net VDD_GNSS v_max 3.0 x 1.02 = 3.06 V (accuracy tens of mV, verify)
Q_REV (DMP2035U): abs_max_v 20 (Vds), Vgs +-12; Iload 0.25 A avg, 0.8 A pulse
U_WDT (TPL5010): abs_max_v 6.0 (verify), rec 5.5; U_SUP: TPS3839 abs 7 V (verify)
spice: TPS61299 PSpice models SLVME14/SLVME79/SLVME62 on ti.com (not downloaded; spice.mpn would be TPS61299, not TPS612994 -> model-mpn check: use fit for 612994 with ILIM 0.1 A, source: fit). GW209F: fit RC: C 160 mF, ESR 55/66 mOhm, leak 1 uA, source: fit (datasheet rev 4.5).
```
Entries marked "verify" were not read from datasheets in this pass (time box).

## Cost (power group, @1000, USD)
boost 1.3 + L 0.20 + CIN 0.05 + COUT 0.03 + VSEL R 0.01 + supercap 3.0 + hand labour 0.4 + Q 0.14 + LDO 0.30 + WDT 0.21 + SUP 0.30 + divider 0.28 = **~6.2 USD** vs architecture power lines (boost 1.0 + LDO 0.2 + reservoir 0.5/supercap 3.0 + supervisor 0.3 + TPL 0.35 = ~4.9): +1.3 USD. Whole-project 32.1 -> ~33.4 USD vs 50 USD COST-1: within the limit.

## Risks / open
Boost 612994 LCSC stock not found (check; fallback TPS61299 family at JLCPCB C41689719 has no suitable limit); supercap quote/EOL/hand solder; supervisor variant and LCSC missing; 3 limit lines not read; the supercap footprint is a placeholder; the model for TPS612994 is a fit, not vendor. Decisions for the product person: accept supercap EOL/quote-only single source and hand-soldered back-side part (cost per unit, serviceability); raise VOUT to 4.0 V; accept 11 s first-power-up charge.

---
# FIX PASS 2026-11-01 (PM round 6 q1: keep alkaline to -20 C, 4.0 V rail, in-production supercap)

## F0. Finding that was wrong in the first table
TPS612994 (and 612993/6/7) exist only as YBH WCSP-6 (1.27 x 0.88 mm, 0.4 mm pitch). The SOT-563 (DRL) parts are TPS61299 and TPS61299A only (SLVSGS9G ordering table). The table row said DRLR: corrected above (pad map in the row, notes/datasheets/tps61299.md). Consequence for layout: 0.4 mm pitch, 0.23 mm balls, needs via-in-pad or 0.1 mm escape on the 4L board (JLCPCB capability to confirm); risk of a `layout diagnosis`. No 100 mA boost in a coarser package exists; a fallback is TPS61299DRLR (C41689719, 1.2 A limit) + an external 100 mA input limiter (extra parts) -- not recommended.

## F1. Supercap
| option | status | C min | ESR (23 C / -20 C) | size | mass | price | verdict |
|---|---|---|---|---|---|---|---|
| **CAP-XX GW209F** | in guide v6.6 (2023), MOQ 300, ex-stock list unclear; distributors say EOL | 128 mF | 66 / ~112 mOhm (2x at -40 C, C flat vs T) | 28.5 x 17 x 2.1 | 0.94-1.37 g (derived from Pgrav 83-121 kW/kg; TME 1.08 g gross) | ~6 assumed | **chosen**, subject to written production confirmation |
| 2 x CAP-XX GZ215F (parallel) | active, Digikey stock, $10.33 @1000 each | 144 mF | 33 / ~56 mOhm | 2 x 20 x 15 x 2.5 (does not fit one 28.5 x 17 site; 40 x 15 or two sites) | ~2 x 0.8 est | ~20.7 | second source in function; +17 USD over the GW209F estimate, over the PM's +3-6; needs a second back-face site |
| Kyocera AVX BZ015A104ZSB (BestCap, 100 mF -20/+80 %, 80 mOhm, 28 x 17, 5.5 V) | active family | 80 mF; at -20 C C -30 % -> 56 mF, ESR up to +400 % (5x, 0.48 Ohm) | 96 max / 480 | 28 x 17 x 2.1-6.1 | 3.34 g | ~3-5 (unquoted) | fails PWR-7 at -20 C (below); fits the 28 x 17 site; is a reduced-support fallback only |
Not selected: Eaton PHV (5.4 V EDLC, 0.47-5 F, cylindrical pack, too big, cold OK), Eaton HS/HSL/HSH 3.8 V hybrids (-25 C limit, ESR up to 10x cold, 0.25-0.55 Ohm: fail), LIC/Seiko/Murata not read (time box; LIC min voltage 2.2 V would need a cut-off).
Two GW209F in parallel do not fit one site (28.5 x 17 x2) and are not needed at 4.0 V.

### Hold-up at 4.0 V (t = C x (dV - I_def x ESR_tot) / I_def; floor 3.2 V, dV 0.80 V; I_def 0.434 A from TPS612994 at 0.5 A TX, alkaline 2.8 V / 4 Ohm)
| case | C | ESR | t |
|---|---|---|---|
| GW209F, 23 C, min C, max ESR | 0.128 | 0.066 | 0.128 x (0.80 - 0.029) / 0.434 = 227 ms |
| GW209F, -20 C (ESR 1.7x = 0.112), min C | 0.128 | 0.112 | 0.128 x 0.751 / 0.434 = **221 ms** |
| same, end of life C -30 % (EDLC ageing, guide: <6 %/1000 h only at 4.0 V/50 C) | 0.090 | 0.15 | 0.090 x 0.735 / 0.434 = 152 ms |
| with 0.47 Ohm series R (option B below) at -20 C | 0.128 | 0.58 | 0.128 x 0.548 / 0.434 = **162 ms** |
| BZ015A104 single, 23 C | 0.080 | 0.096 | 140 ms |
| BZ015A104 single, -20 C worst (C 56 mF, ESR 0.48) | 0.056 | 0.48 | 0.056 x 0.592 / 0.434 = **76 ms, fails** |
PWR-7 (100 ms) holds with GW209F at 4.0 V: margin 2.2x (cold, new), 1.5x (end of life). Not included: TPS612994 ILIM spread already at 120 mA; 4.0 V regulation accuracy (not in text; if -3 % = 3.88 V the dV is 0.68 V: 0.128 x 0.63 / 0.434 = 185 ms cold, still OK); Vin collapse of the alkaline under the 120 mA draw already counted.
nRF9151 at 4.0 V: VDD 3.0-5.5 V (notes/datasheets/nrf9151.md); sleep/eDRX currents are quoted at 3.7 V and the PS gives no 4.0 V figure: assume +3 %. BG77 is not in the design (VBAT 4.8 V max anyway).

### Rail consequences of 4.0 V
- Every VDD_3V6 load must tolerate 4.0 x 1.03 = 4.12 V (v_max 4.15 V): nRF9151 VDD 5.5 V ok; TPS7A0230 6.5 V ok; TPL5010 5.5 ok; TPS3839 6.5 ok; supercap 5.0 V (2.0 V/cell) ok. **Parts rated 3.6 V (LIS2DW12 1.62-3.6 V, LC76G 3.6 V, nRF9151 VDD_GPIO 1.7-3.6 V) must not hang on VDD_3V6**: LC76G is behind the 3.0 V LDO, LIS2DW12 and the GPIO domain must come from VDD_GPIO (circuit stage to confirm what drives VDD_GPIO; if it is VDD_3V6 directly, that is a violation).
- PWR-5: the factor 1.60 (battery side per uA at the rail) grows with Vout: 1.60 x 4.0/3.6 = ~1.78 (assumes the same 75 % light-load efficiency, unverified). (2.7 + 2.9 + 1.0) x 1.78 + 1.0 = **12.7 uA at 23 C** (was 11.6); at 50 C with the supercap +1.6 uA x 1.78 = **15.5 uA > 15 uA floor**, using the 1 uA max leakage; with typical 0.5 uA, ~14.6. At 2.0 V/cell the leakage is below the 2.3 V figure, so 50 C is probably ~15, but PWR-5 has no margin at 50 C. Open for the architect (sleep-floor-reallocation.md); it is a 23 C equilibrium requirement if the PM does not name 50 C.

## F2. Boost at 4.0 V, start-up and charge
- VSEL 22.6 k 1 % -> 4.0 V (p.14). Efficiency about 1 % lower at the larger step; ILIM 80/100/120 mA unchanged (input-side), so the deficit 0.434 A is unchanged.
- COUT is 4-1000 uF effective; 160 mF is outside it. Default (option B): ceramic at the pin 22 uF 0805 X5R 10 V (4 uF effective needs >= 2x derating at 4 V: use 2 x 10 uF or one 22 uF, circuit stage reads the vendor DC-bias curve) and the supercap behind **R_S = 0.47 Ohm 0805 1 %** from VDD_3V6 to the cap: HF the boost sees the ceramic + a 0.5 Ohm / 160 mF branch (loop-friendly, like an ESR isolation), DC it sees the supercap; burst 0.43 A x 0.47 = 0.2 V drop (hold-up 162 ms cold above); dissipation peak 0.09 W. Option A (no R_S, 55-66 mOhm cap ESR alone) is the lower-part-count variant; bench-check loop stability and the start-up; if it fails, fit R_S. The start-up of a depleted supercap is the intended case of the datasheet's 20 mA / 80 mA / ILIM current-limited start-up (7.3.7), so no load switch or charger IC is needed; a charger IC would add >1 USD without a benefit.
- Power-up charge 0 -> 4.0 V (Q = 0.16 F x 4.0 V = 0.64 C): 0-0.5 V at 20 mA = 4.0 s; 0.5-1.8 V at 80 mA = 2.6 s; 1.8-4.0 V at 66 mA (alkaline cold) = 5.3 s: **~11.9 s** (arch 1.9 s; at 3.6 V it was ~11 s). Li-FeS2 about 11 s. Per-connection recharge 0.16 x 0.8 = 128 mC at 66 mA = 1.9 s worst-case full droop (firmware waits for VDD_3V6 >= 3.9 V instead of 3.5 V).
- Charge from the cell: 0.64 C is 0.18 mAh once, negligible.

## F3. Battery TVS (D_BAT)
**TPD1E10B06DPYR (TI, LCSC C48260, 126 k stock, 0.025 USD): VRWM 5.5 V, ILEAK 100 nA max at 5 V (datasheet p.4), so below 0.1 uA at 3.65 V, +-30 kV contact (ENV-3 >= 8 kV), bidirectional, X1SON-2 0.6 x 1.0 mm, 12 pF.** Replaces SMF5.0A (200 uA at 5 V; unsuitable). Clamp 10 V @1 A: above the boost abs max 6.5 V during a strike: CIN 22 uF and the P-FET share it; ESD test at the first article. Alternatives: TECH PUBLIC clone C2937015 (leakage not read); CTK ESD03D3X-class 3.3 V parts (100 nA at 3.3 V but breakdown 3.7 V min = at the 3.65 V rail: reject). Button TVS (D_BTN ESD9B5.0S) is on the 3.0 V GPIO side and unchanged.

## F4. Supervisor
**TPS3839G33DBZR, C485802**: VIT- 3.003-3.126 V (3.08 typ). With the 4.0 V rail and the 3.2 V hold-up floor the trip max (3.126) is below the floor (no reset inside the specified hold-up) and the min (3.003) is at the nRF9151 3.0 V minimum. Draws 150 nA. K33 (2.857-2.974 V) is the alternative (C96333). Boost: no LCSC; buy TPS612994YBHR from TI/DigiKey/Mouser (6000-reel at TI; 7.9 k stock at TrustedParts), JLCPCB consigned or hand-placed WCSP (hot-air), sourcing line: "TI/DigiKey, consigned to JLCPCB".

## F5. Deltas
- **Mass**: GW209F 0.94-1.37 g against the 2 g budget: -0.6 to -1.1 g; R_S + 2nd ceramic +0.01 g: total **64.2 g -> ~63.2-63.6 g** (no increase; the 2 g was a conservative class estimate; weigh a sample). With BZ015A104 (3.34 g): +1.3 g -> 65.5 g > 65 (fails MECH-1). Two GZ215F (~1.6 g est): unchanged.
- **Cost @1000** (power group): supercap 3.0 -> ~6.0 (assumption, no quote) +3.0; boost 1.30 -> 1.09 -0.21; supervisor 0.30 -> 0.23 -0.07; D_BAT TVS +0.03 (0.025 + ~0); R_S +0.01; COUT 2nd 10 uF +0.03; WCSP consignment handling not priced (assume ~0.1; JLCPCB fee unknown). Net **~+2.9 USD**: power group 6.2 -> 9.1; whole project 33.4 -> ~36.3 USD vs COST-1 50 USD (ok). Upper case if GW209F costs as GZ215F (~10): +7 USD -> ~43 USD, still under 50.

## F6. limits (additions)
```
U_BOOST (TPS612994YBHR): limits as above; VSEL R 22.6k; net VDD_3V6: v_max 4.0 * 1.03 = 4.12 V (accuracy of the 4.0 V setting not in the text read, assumed 3 % + ripple) ; rec_max_v 5.5
C_STORE (GW209F): limits {abs_max_v: 5.5, rec_max_v: 5.0, temp_c: [-40, 70], source: "CAP-XX guide v6.6 p.4, GW209 datasheet Table 1/2"}; sourcing: "CAP-XX direct, MOQ 300, hand solder on back (guide p.12)"; spice fit C 160 mF (min 128), ESR 55/66 mOhm (112 at -20 C), leak 1 uA, spice.mpn GW209F source fit
D_BAT (TPD1E10B06DPYR): limits {abs_max_v: 5.5, source: "TPD1E10B06 p.4 VRWM 5.5 V, ILEAK 100 nA at 5 V"}; lcsc C48260
U_SUP (TPS3839G33DBZR): limits {abs_max_v: 7.0 (verify), rec_max_v: 6.5}; lcsc C485802
U_BOOST sourcing: "TI/DigiKey/Mouser, no LCSC part; consigned or hand-placed"
```

## F7. Open
1. GW209F production status, lead time, price at 1000 (CAP-XX quote; sales@cap-xx.com): decides; fallback 2 x GZ215F (+17 USD, second site) or accepting BZ (fails -20 C).
2. WCSP 0.4 mm pitch escape at JLCPCB (layout); TPS612994 only from TI/distributors, no LCSC.
3. PWR-5 at 50 C reaches ~15.5 uA with the 4.0 V rail (assumed efficiency): architect to re-derive the 1.60 factor.
4. VDD_GPIO source with a 4.12 V rail (3.6 V parts).
5. Efficiency at 100 mA/2.3 V and the 4.0 V accuracy are not in the text read: bench/datasheet figures.
6. Footprint of the supercap still a placeholder until the CAP-XX landing drawing (guide section 20) is read; the first GW209F mass by weighing.
7. PM decisions: accept ~6 USD (assumed, unquoted) supercap price; accept second-source gap (single maker) and ~12 s first power-up.

---
# FIX PASS 2 2026-11-01 (parts review; PM round 7: 2x GZ215F, assistance over LTE, LC76G backup off)

## G1. Supercap: 2 x CAP-XX GZ215F in parallel (replaces GW209F; rows C_STORE above are superseded)
Datasheet numbers: notes/datasheets/gz215f.md. Per module C 72/90/108 mF, ESR 110/132 mOhm (23 C), leakage 1/2 uA (2.3 V/cell, 23 C, 120 h), 5.0 V rated, 5.5 V abs max, 20 x 15 x 2.5 mm, mass ~0.5-0.7 g (derived), 3 terminals (1 -, 2 +, 3 BAL). Pair: C min 144 / typ 180 mF, ESR 55 typ / 66 max mOhm. Rail 4.0 x 1.03 = 4.12 V: 2.06 V/cell < 2.5 V Vn (>= 4.12 V rating: 5.0 V OK). Footprint `dog_misc:CAPXX_GZ215F` (lib/footprints/dog_misc.pretty, back copper, symbol `dog_misc:CAPXX_GZ215F`), drawn from the p.10 land drawing: 4 round pads dia 2.5 at +-9.0 x +-7.5, BAL tab pad, keep-out for the 6.0 x 3.5 underside copper contact. + / - corner assignment read from a drawing image: confirm on the first sample (risk).
Hand solder, back face, 2 parts (PM round 7 q4). Price $10.33 @1000 each (Digikey 2026-11), 2 x = 20.66 USD (vs 6 assumed).
Hold-up (t = C (dV - I ESR)/I, floor 3.2 V, dV 0.80 V at 4.0 V). Input-limit minimum 80 mA: alkaline cold Vin = 2.8 - 4.0 x 0.08 = 2.48 V; P_in 0.198 W x 0.85 = 0.169 W -> 0.042 A at 4.0 V; deficit at 0.5 A = **0.458 A** (as the brief).
| case | C | ESR | t |
|---|---|---|---|
| 23 C, C min, ESR max | 0.144 | 0.066 | 0.144 x (0.80 - 0.030) / 0.458 = 242 ms |
| -20 C (ESR x1.7 = 0.112) | 0.144 | 0.112 | 0.144 x (0.80 - 0.0513) / 0.458 = **235 ms** |
| -20 C, 4.0 V at -3 % (3.88 V, dV 0.68) | 0.144 | 0.112 | 0.144 x 0.629 / 0.458 = 198 ms |
| end of life (C -30 %, ESR +50 %: assumption, guide gives <6 %/1000 h at 80 % Vn/50 C only) | 0.101 | 0.168 | 0.101 x (0.80 - 0.077) / 0.458 = **159 ms** |
PWR-7 (100 ms): 2.35x cold new, 1.59x end of life. No R_S needed (option A). 
Balancing: needed (guide p.13 and datasheet p.7). Tie both BAL pins together (mid-points of the two modules in parallel) and fit 2 x 10 Mohm 0402 1 % (rail to BAL, BAL to GND): 4.0 V / 20 M = 0.2 uA (x1.47 battery side = 0.29 uA, in the PWR-5 sum). The resistor current is only 0.2 uA against a leakage mismatch up to 2 uA max per module: the resistors do not hold the midpoint against the leakage spread alone; 4.7 Mohm pair (0.43 uA) is the better value if the first-article midpoint drifts > 0.1 V. Decision for the circuit stage: 2 x 4.7 M (+0.23 uA, in the floor below). Cost 0.01 USD.
Mass: GW209F budget 2.0 g -> 2 x 0.7 g = 1.4 g (worst, derived, weigh a sample): 64.2 - 2.0 + 1.4 + 0.1 (2nd LDO, FET, resistors) = **63.7 g** <= 65 (MECH-1). 
Fit on the back face (70 x 35): two 21 x 15 sites side by side at [20,10] and [42,10] (x 9.5-30.5 and 31.5-52.5, y 2.5-17.5, origin as the old [30,10] item); test pads [30,22] 19 x 3 (y 20.5-23.5) clear by 3 mm; bat pads [45,30] clear by > 10 mm. 2.5 mm height: item height 2.7 (G option tape).
Floorplan facts: footprint `dog_misc:CAPXX_GZ215F`, height 2.5 mm; drawing: CAP-XX GZ115/GZ215 rev 4.5 p.10 (dual cell), 3 terminals (4 corner tabs + BAL tab), pad pattern 18.0 x 15.0, body 19.0 x 15.0, 20.0 incl. terminals; side back, hand solder (iron < 70 W, <= 5 s).
limits: `C_STORE1/2 (GZ215F): {abs_max_v: 5.5, rec_max_v: 5.0, temp_c: [-40, 70], source: "GZ215F datasheet rev 4.5 Table 1/2 p.1"}; sourcing: "CAP-XX / Digikey, hand solder on back"; spice fit C 90 mF each (min 72), ESR 110/132 mOhm each, leak 1 uA each, spice.mpn GZ215F source fit; price_usd 10.33`.

## G2. Always-on 3.0 V rail (review #1)
**U_LDO_AON = TPS7A0230PDBVR (same part as U_LDO, C3747031, ~0.30 USD)**, input VDD_3V6 (4.0 V), output net VDD_AON 3.0 V (v_max 3.06 V, below the 3.6 V limit of nRF9151 VDD_GPIO / LIS2DW12), EN tied to VIN. Iq 25 nA typ (assume 0.1 uA max, datasheet max not re-read), 200 mA. Loads: nRF9151 VDD_GPIO (pin 65), LIS2DW12 VDD/VDD_IO, TPL5010 VDD, button/open-drain pull-ups, supervisor pull-up. The GNSS LDO stays separate (switched). Cout 1 uF 0402 (circuit stage: TPS7A0230 Cout from its datasheet p.).
Sequencing (nRF9151: apply VDD before VDD_GPIO, remove VDD_GPIO first, VDD_GPIO low while ENABLE low - DevZone 126378): VDD (pin 14) and the LDO input are the same net VDD_3V6, so VDD_GPIO = VDD_3V6 - dropout (mV at nA) can never lead VDD on the way up; on a collapse the LDO output follows its input down through the 1 uF discharge into the (nA) loads: VDD_GPIO can exceed VDD for ms. The brown-out is covered by the supervisor reset and by the supercap hold-up (rail stays >= 3.2 V in the hold-up): accepted, flagged to the circuit stage. ENABLE (pin 10): drive it from VDD_AON domain; it must be low whenever VDD_GPIO is low: the circuit stage must check that no VDD_GPIO-domain driver sits high into an unpowered pin (the LDO powers VDD_GPIO before VDD at no time).
Cost +0.30 USD, +0.04 g, floor +0.1 uA at the rail (included below).

## G3. Supervisor into the nRESET domain (review #2)
Open drain, TI **TPS3838K33DBVR** (SOT-23-5, LCSC C135468, ~220 nA typ, trip 2.93 V nominal; 10 ms or 200 ms delay variant by the suffix; LCSC listing 250 nA): TPS3838 does not exist as G33/DBZ (TPS3839 only). The output pulls nRESET (a VDD_GPIO-domain pin, internal pull-up) low only, no pull-up to the 4.0 V rail: no overvoltage into the 3.0 V domain. Trip 2.93 V (K33; min/max not read: TPS3839K33 is 2.857-2.974 V) sits 0.27 V under the 3.2 V hold-up floor and just below the nRF9151 3.0 V VDD minimum: a brief 3.0 -> 2.97 V window before reset, accepted (it protects against the 4.0 V rail sagging deeper). The N-FET buffer alternative does not work with the TPS3839 (push-pull, active-low RESET): a buffer needs an active-high output, which the family does not have; so the open-drain part is the choice. nRESET gets no external pull-up (internal) or a 10 k to VDD_AON. Price ~0.30 USD (assumed from TPS3839 0.23 + 0.07; not read). Supply pin from VDD_3V6 (monitored node), 6.5 V rated (verify). limits: `U_SUP (TPS3838K33DBVR): {abs_max_v: 7.0 (verify), rec_max_v: 6.5}`.
Reading the K33 limits needs the TPS3838 datasheet (not fetched in this pass).

## G4. FETs (reviews #6, #16)
- Q_REV: **Vishay SI2301CDS-T1-GE3** (P-FET SOT-23, G1 S2 D3 same as AO3401A symbol). DMP2035U is rejected: IGSS = +-10 uA max at +-8 V (Diodes DMP2035U datasheet read: 10 uA max, 100x over the 100 nA need), RDS(on) 62 mOhm max @ -1.8 V. SI2301CDS (Vishay datasheet 66709: URL returned 404 in this pass, values from the datasheet as I know it, NOT re-verified): IGSS +-100 nA at +-8 V, VGS(th) -0.4..-1.0 V, rDS(on) ~0.112 / 0.142 / 0.19 ohm at -4.5 / -2.5 / -1.8 V max, 20 V, 2.3 A. At 0.8 A pulse drop 0.15 V max at -1.8 V: acceptable (cell side). Stock/LCSC for the CDS variant not confirmed (LCSC has SI2301-ZE C5261052 145 mOhm@2.5 V, 0.016 USD, 198 k; IGSS not read). OPEN: confirm IGSS <= 100 nA from the Vishay PDF (or pick a part with a read datasheet) before binding. 0.10 USD (est).
- Q shifter: **onsemi BSS138LT1G, C82045, 0.0417 USD, 591 k stock**: VGS(th) 0.8-1.5 V max 1.5 V (onsemi BSS138 datasheet read: 0.8/1.3/1.5 V, IGSS +-100 nA, IDSS 0.5 uA at 50 V; that is the BSS138 sheet, BSS138LT1G variant limits to verify, spice.mpn BSS138). 3.0 V gate drive = 2x the Vth max. Alternative BSS138K C5224260 (Vth 1 V typ).

## G5. Items not changed
TPS612994 YBH land: TI YBH0006-C03 drawing read (SLVSGS9G): 2 x 3 balls 0.4 mm pitch, land pad 0.2 mm (example board layout), ball 0.225/0.185 mm dia, die 1.248-1.288 x 0.860-0.900 mm, height 0.4 max. KiCad's Texas_DSBGA-6_0.855x1.255mm_Layout2x3_P0.4mm_LevelB has the same pad centres (+-0.2, 0/+-0.4) with 0.23 mm pads (die 0.855 x 1.255 vs 0.88 x 1.27 actual): not material, no new footprint `TI_YBH0006`; for the layout use 0.2 mm land pads (set the pad size in the layout stage if the 0.4 mm escape fails) and a courtyard for the 1.27 x 0.88 die. CHANGED: none.
