# Circuit notes (round 3, 2026-10-21; U2 refit 2026-10-22)

## Review-2b round (2026-10-22): AO3413 at VGS(th) max (finding M1, minor m1)

**Corner model.** AO3413 data sheet EC table (p.2): VGS(th) max -1.0 V; RDS(on) max 130 / 100 / 80 mOhm at -1.8 /
-2.5 / -4.5 V. Level-1 fit with VTO -1.0 V: R(Vgs) = 1 / (KP (|Vgs| - 1.0)) + RD + RS; through the 130 and 80 mOhm points
KP = 19.29, RD + RS = 65.2 mOhm (RD = RS = 32.6 mOhm); check: 99.8 mOhm at -2.5 V (data sheet 100). Applied per
instance with `altermod m.xmN.m1` and `alter @r.xmN.rd/rs` (M1, M2, M4) in two information analyses; limits, typical-value
verification and the PM decision are unchanged.

| Info analysis (0 / 25 / 45 C) | Result | Typical-value measure |
|---|---|---|
| burst_vbat20_vthmax_info: +3V3 min (R3, 3.2 V) | **3.2165 / 3.2165 / 3.2165 V** | v3v3_min_b20c 3.2165 V |
| same: VIN_BST min; M4 gate | 1.372 / 1.349 / 1.330 V; Vgs = -1.34 / -1.32 / -1.31 V | VIN_BST 1.479 / 1.467 / 1.457 V |
| same: VBAT_P min | 1.539 / 1.533 / 1.528 V | 1.562 / 1.559 / 1.556 V |
| guard_forward_drop_vthmax_info (R4, 0.7 A, 2.0 V) | 37.2 / 41.1 / 44.1 mV FETs | 24.9 / 27.6 / 29.7 mV |

R3: the corner minimum is set by the boost VOUT corner (3.234 V - 0.35 A x RREG = 3.2165 V), not by the input path. At
VTO -1.0 V M4 still has 0.31 V of overdrive; VIN_BST falls 0.13 V, the boost input rises to about 350 mA x 3.234 / (0.85 x
1.33) = 1.0 A, under the 1.24 A corner input limit at 1.30 V (notes/models.md). R3 passes, so the reviewer's fix (R41 10 k +
C23 100 nF) and a PMOS change are **not applied** (no part, value or cost change; BOM 20.96 USD). R4: 44.1 mV FETs + 5.9 mV
copper (guard_copper 8.39 mOhm, last layout) = 50.0 mV against 55 mV; agrees with the 45.45 mV x 1.53 estimate the PM
budget used.

Post-layout note (for the layout round): the last layout's post-layout corner gives v3v3_min_b20c 3.2066 V (typical FETs,
VIN_BST 1.33-1.36 V): the margin there comes from the +3V3 copper drop (U2 -> U1), not from M4.

**Minor m1: sleep Vgs of M1/M2/M3.** R14 (1 M) against R41 + R42 (1.1 M) to VIN_BST puts VBAT_EN 0.476 x Vd4 under
VBAT_P: Vgs = -0.15 / -0.11 V at 0 / 45 C (Vd4 0.31 / 0.23 V). Subthreshold estimate at 45 C: ID = 250 uA (VGS(th)
definition, EC table) x 10^-((|Vth| - |Vgs|) / S), S about 95 mV/decade (n 1.5, assumed), Vth -2 mV/K (assumed):
typical |Vth| 0.61 V: 1.3 nA per FET; |Vth| min 0.46 V (data sheet min -0.5 V, recall): about 50 nA per FET. M1/M2
conduct this from VBAT_RAW to VBAT_P, in the same direction as the body diodes that already carry the 16 uA sleep
current: it is not an added drain (R6 change 0 uA) and lowers the diode drop by under 0.2 mV (R35 unchanged). The only
path where such a leak is a loss is M3 (CJ2305, into the 30 k divider): at the model VTO -0.7 V about 0.4 nA; at an
assumed |Vth| 0.40 V about 0.2 uA, R6 25.53 -> 25.75 uA (limit 26). The CJ2305 VGS(th) minimum is not read: bench check
(sleep current, prototype test list). The R14/R41/R42 bleed (Vd4 / 2.1 M = 0.11-0.15 uA, VBAT_P -> VIN_BST) feeds the
boost input and is already in the simulation.

**burst_vbat20_corner errors (0 and 45 C).** They came from the post-layout runs only (build/post, last layout): ngspice
printed "Error: 1.3e238, 2 out of range for ^" in `b.xu2.bio` (a non-converged Newton iterate inside the averaged boost
smooth-min); the run recovered and printed all measures, but hwflow scored the analysis as an error. Fix (model numerics,
no limit): `Bio` in MAX17227A_AVG clamps ir and im to +-1e4 A before the smooth min (|ir|, |im| < 100 A at every converged
point). Re-running the two post-layout netlists: no error, v3v3_min_b20c 3.2066 V. The pre-layout runs had no error.

## Review-2 round (2026-10-22): AO3413 guard (PM D4) and the R35 rework

**Part change (D4).** M1, M2 CJ2305 -> AO3413 (C86648). Pinout: AO3413 data sheet p.1 (SOT-23 top view: G 1, S 2, D 3)
= symbol Transistor_FET:AO3401A pins 1 G / 2 S / 3 D (`hwflow lib show`) = Package_TO_SOT_SMD:SOT-23 pads 1-3
(`hwflow lib pads`). Model: subckt AO3413 in models/thermo.lib, fit in notes/models.md.

**Why R35 broke.** The AO3413 body diode (Fig. 6) drops 0.30 / 0.26 / 0.22 V at 8 uA per FET (0 / 25 / 45 C), against
the 0.51 / 0.45 / 0.40 V assumed for the CJ2305. With M1/M2 alone and 3.7 V lithium cells (guard OFF), VBAT_P =
3.407 / 3.452 / 3.489 V, above the 3.389 V ULPM level, so +3V3 follows VIN (model bound): **3.407 / 3.452 / 3.489 V
against 3.45 V (fail at 25 and 45 C)**. Also, any VIN between about 3.33 V and 3.389 V gives a ULPM ripple peak of about
VIN + IPK sqrt(L / COUT) = VIN + 0.1 V (above 3.45 V), so VIN must stay <= about 3.31 V for every VBAT up to 3.7 V.

**Options checked (cheapest first).**

| Option | Numbers | Result |
|---|---|---|
| Lower RSEL (Table 1: 3.2 V = 226 k) | does not help: VBAT_P (3.41-3.49 V) stays above any target <= 3.4 V and the rail follows VIN; also the burst minimum becomes 3.2 - 0.0175 = 3.18 V typ (R3 3.2 V fails) | rejected |
| Bleed resistor on VBAT_P | VBAT_P <= 3.45 V at 45 C needs about 42 uA through the diodes (about 130 k): 20 uA at 2.6 V, R6 (26 uA) fails; VIN <= 3.31 V needs about 3 mA | rejected |
| Higher RSEL | ULPM level above 3.45 V | rejected |
| **Series body diode M4 (AO3413) in the boost input, ON with the guard, gate RC** | 0.092 + 3 x 0.002 = **0.098 USD** (R35 <= 0.3 USD); VIN_BST = 3.102 / 3.192 / 3.265 V at 3.7 V | **chosen** |

**Circuit.** M4: drain VBAT_P (C1/C2 side), source VIN_BST (C3, L1, U2 IN/EN), gate VBST_G. R41 100 k VBAT_EN ->
VBST_G, C23 10 nF and R42 1 M VBST_G -> VIN_BST (tau 1 ms). Guard OFF (sleep, lithium wakes, reset, dead board, reversed cells): M4
is OFF and the boost gets VBAT_RAW minus two body-diode drops. Guard ON (alkaline wakes, rule 8): M4 turns ON about 1-3 ms
after IO2 goes low (VIN_BST = VBAT_P minus 0.08-0.1 ohm x I). The RC keeps M4 OFF during the 50 us rule-8 read (the gate
moves 5 %): with a direct gate the read would connect C1/C2 (3.45-3.52 V on lithium) to IN and lift +3V3 to about 3.5 V.
C23/R42 to VIN_BST (M4 source, not GND) so that an insertion step does not turn M4 on and the gate is referenced to
its own source like M1/M2 (R14/C21); with C23 to VBAT_P the DC point had several solutions (MC yield 42-74 %). M4 is downstream of C1.1, so it is not in
the R4 forward-drop path or the guard_copper budget; the insertion surge charges C1/C2 through M1/M2 only.
Firmware: unchanged rules; rule 8c (20 ms recharge with the guard ON) also covers the M4 turn-on delay.

**Results (typical values, 0 / 25 / 45 C; build 2026-10-22, 109/109 pass).**

| Measure | Value | Limit | Req |
|---|---|---|---|
| r35_dc_vbat37 v3v3_dc_37 (VBAT_P / VIN_BST) | 3.389 V (3.406-3.488 / 3.102-3.265 V) | <= 3.45 V | R35 |
| sw_ulpm_li37_sleep peak (78.9 uF, C4 + C5 + C22) | 3.403 / 3.408 / 3.414 V | <= 3.45 / 3.46 V | R35, R3 |
| sw_ulpm_li37_relay peak / coil mean | 3.400-3.409 / 3.372-3.376 V | <= 3.45 / <= 3.4 V | R35 |
| r35_wake_vbat37 rail / coil max; burst min | 3.389 / 3.389; 3.283 V | <= 3.45 / 3.4; >= 3.2 V | R35, R3 |
| r35_swap_to_lithium | 3.389 V (VIN_BST <= 3.226 V) | <= 3.45 V | R35 |
| sw_ulpm_alk33_on peak (guard and M4 ON, VIN 3.30 V) | 3.418 V | 3.2-3.46 V | R3 |
| burst_vbat20_corner (VIN_BST min 1.47 V) | 3.2165 V | >= 3.2 V | R3 |
| burst_vbat34 (alkaline, guard OFF, two diodes) | 3.283 / 3.389 V | 3.2-3.46 V | R3 |
| guard_forward_drop (AO3413 79.5 mOhm at -1.97 V) | 24.9 / 27.6 / 29.7 mV | <= 50 mV | R4 |
| reverse_dead_board / 1 h just-pulled | <= 0.0045 uA / 0.0007 uA | <= 1 uA | R4 |
| sleep_floor (typ) | **25.53 uA** (was 24.32) | <= 26 uA | R6 |
| sleep_floor_iqmax_info / effl80_info | 26.07 / 27.01 uA | information (known risk) | R6 |
| relay_vbat33_on energy | 8.70 mJ | <= 9 mJ | R9 |

R6 cost of the limiter: in sleep the boost input sits one body diode lower (alkaline 2.6 V: VIN_BST about 2.1 V), so
the input current rises (constant power): 24.32 -> 25.53 uA, margin 0.47 uA. Information runs: ULPM maximum (+4.6 %)
peak 3.464-3.471 V (was 3.475-3.486 V with 2 x 47 uF; known risk, PM decision), relay coil 3.433-3.436 V.

**R4 with AO3413.** Forward drop 29.7 mV worst (45 C) at typical RDS(on); budget guard_copper re-derived: (50 - 29.705) /
0.7 = 28.99 mOhm (was 15.72). Corner not covered by the typical-value sims: at the RDS(on) maximum (130 mOhm at
-1.8 V = 1.53 x typ) the two FETs drop about 45 mV, so with the last layout's 11.7 mOhm copper (8.2 mV) R4 would be
about 53 mV. Guard surge against the data sheet: forward insertion (lithium, dead board) 4.0 A per FET peak, I2t
0.023 A2s, 8.0-8.2 mJ (about 3.4 W peak, tau about 2.3 ms) and the guard-ON reversed insertion 7.7-7.8 A per FET,
15-16 mJ: inside IDM 15 A and the Fig. 10 single-pulse power (about 15-20 W at 10 ms, more at 1 ms). Reverse leakage:
the model has only the body-diode IS (nA); the data sheet IDSS is <= 1 uA per FET at -20 V (25 C), 5 uA at 55 C, not
specified at -3.7 V: the prototype test (5 reversed lithium insertions, < 1 uA) stays.

**Testbench changes (numerical, no limit changed).** Switching testbench VIN = v(VIN_BST) (U2 IN), COUT 52.6 -> 78.9 uF
(C22 added, PM D1; ESR 1 mOhm), ctol case 63.1 uF. Nodeset = the guard-OFF state at 2.4 V
(VBAT_RAW 2.4, VBAT_P 2.1, VIN_BST 1.85, +3V3 3.3, SW_BOOST 1.85 V); with the M4 gate referenced to VIN_BST every
operating point converges to the physical solution (109/109, MC 50 100 %), no op prestep or other workaround.


## Delta review fixes (2026-10-22)

**Finding 1, output ripple with VIN near VOUT.** In ULPM each pulse ramps L1 to VIN x tON / L (about 0.95 A) and then
discharges slowly into OUT because OUT - VIN is only 0.08-0.2 V (lithium with the guard OFF, alkaline 3.3 V with the
guard ON). The averaged model cannot show it, so a switching-level testbench was added (sim.sources, block `TB...`, idle
unless VTBSEL = 1; description in notes/models.md). With the old 2 x 10 uF 0805 (about 13 uF effective) it gives
3.489 / 3.530 V peaks at VIN 3.19 / 3.31 V, above R35. Fix (PM decision 2026-10-22, 2 caps of the 3 proposed): C4, C5 =
2 x 47 uF 10 V X5R 1206 (CL31A476MPHNNNE, C96123, JLC basic, 374848 in stock, 0.168 USD), 52.6 uF effective at 3.4 V
(Samsung DC-bias curve: -42.6 % at 3.3 V, -44.7 % at 3.45 V; temperature -1.5 % at 0 C). C4/C5 were replaced, not
added to: no DC-bias curve is published for CL21A106KAYNNNE on the Samsung page. X5R instead of X7R: the 0-45 C ambient
(R29) is inside the X5R range.

Switching testbench results (typical data sheet values, ULPM level 3.389 V, 0 / 25 / 45 C, 2 x 47 uF):

| Analysis | Case | +3V3 peak | Limit | Req |
|---|---|---|---|---|
| sw_ulpm_li37_sleep | lithium 3.7 V, guard OFF, 6.5 uA (VIN 3.19 / 3.26 / 3.31 V) | 3.417 / 3.424 / 3.433 V | <= 3.45 V (R35), <= 3.46 V (R3) | R35, R3 |
| sw_ulpm_li37_relay | lithium, relay pulse 108 mA, repeated pulses 300 us | 3.411 / 3.417 / 3.424 V | <= 3.45 / 3.46 V | R35, R3 |
| sw_ulpm_li37_relay | coil mean = mean rail x 40.5 / 40.78 | 3.377 / 3.380 / 3.384 V | <= 3.4 V | R35 |
| sw_ulpm_alk33_on | alkaline 3.3 V, guard ON, 130 uA (VIN 3.30 V) | 3.432 V | 3.2-3.46 V | R3 |
| sw_ulpm_li37_ctol_info | COUT -20 % (42 uF) | 3.423 / 3.432 / 3.443 V | information | - |
| sw_ulpm_li37_ofsmax_info | ULPM level at its +4.6 % maximum (3.452 V) | 3.475 / 3.479 / 3.486 V | information (known risk, PM decision) | - |
| sw_ulpm_li37_relay_ofsmax_info | same, relay pulse: peak / coil mean | 3.470-3.478 / 3.437-3.442 V | information | - |

Sizing sweep (VIN 3.31 V, one pulse): 13 uF 3.530 V, 40 uF 3.445 V, 52.6 uF 3.432 V, 65 uF 3.426 V, 79 uF 3.419 V.

Coil (R35 coil <= 3.4 V): the 30 mH coil (L/R 0.74 ms) does not follow the us ripple, so the coil voltage is the mean
rail less the DRV8837 drop: 3.377-3.384 V at typical values. The ADW1103HLW rating is set on the applied DC coil
voltage: 3 V nominal, maximum continuous 110 % = 3.3 V (vendor page, parts.md 7.5, unverified). 3.38 V is 113 % for a
31 ms pulse, the same exposure the design already accepted (parts.md item 5); at the ULPM maximum 3.44 V (115 %).

R3 at the ULPM maximum (PM decision 2026-10-22): R3 is verified with typical datasheet values, as R6 and R35. The
maximum-offset peak including ripple (3.475-3.486 V; Note 4: the ULPM accuracy excludes ripple) is a known risk, checked
in prototype test 7; `r3_ulpm_max_info` (averaged, 3.452 V) is information only.

**Finding 2, cost (R30 <= 38.60 USD, PM decision 2026-10-22).** +2 x 0.168 - 2 x 0.03 = +0.276 USD: hwflow BOM 22.25 ->
22.52 USD, central estimate 38.32 -> 38.60 USD, at the limit. The optional second C3 (+0.15-0.18 USD) was not added.

**Finding 3, effective capacitance.** Samsung product-page DC-bias data (120 Hz, 0.5 Vrms, 25 C), read 2026-10-22:
CL31A476MPHNNNE -42.6 % at 3.3 V; CL21A226MAQNNNE (C3) -32.3 % at 3.3 V (15 uF); CL31B226KPHNNNE (22 uF X7R 1206,
considered) -15.7 %; CL31A226KAHNNNE (22 uF X5R 25 V 1206) -20.5 %.

**Findings 4-5, RSEL and EP layout.** notes/layout-constraints.md; R40 now has `decouples: U2` so the placer keeps it at
pad 6. Bench test 8 below.

## U2 MAX17227A refit (stage 4, 2026-10-22)

Model: `MAX17227A_AVG` in models/thermo.lib replaces `TPS61299_AVG`; every number and its source is in notes/models.md (U2 rows). Testbench changes: the R3 upper limit is 3.46 V on every R3 measure (PM decision 2026-10-22); the low corner uses the MAX17227A minimums (3.234 V, IPK 1.8 A, tON +19 %, L -20 %, EFFH 0.85, ULPM +1.5 %); new `r35_dc_vbat37_ulpm_max_info` and `r35_wake_vbat37_ulpm_max_info` (ULPM offset at its +4.6 % maximum; information only since the PM decision 2026-10-22: R35 verified at typical values); new R6 information runs `sleep_floor_iqmax_info` and `sleep_floor_effl80_info`. DRV8837 model: 1 Meg output-to-GND numerical aid (notes/models.md).

| Measure | Value (0 / 25 / 45 C) | Limit | Req |
|---|---|---|---|
| burst_vbat20 v3v3_min / max | 3.283 / 3.389 V | 3.2-3.46 V | R3, R25 |
| burst_vbat20_corner v3v3_min_b20c | 3.2165 V (VBAT_P min 1.577 V) | >= 3.2 V | R3, R25 |
| burst_vbat24, burst_vbat34 min / max | 3.283 / 3.389 V | 3.2-3.46 V | R3, R25 |
| burst_vbat33_on min / max (guard ON, VIN inside the 150 mV band) | 3.289 / 3.389 V | 3.2-3.46 V | R3, R25 |
| r35_wake_vbat37 burst min / max | 3.283 / 3.389 V | 3.2-3.46 V | R3 |
| relay_vbat20_set v3v3_min_relay_20 | 3.295 V | >= 3.2 V | R3 |
| relay_vbat34 / relay_vbat33_on rail max | 3.389 / 3.389 V | <= 3.46 V | R3 |
| r35_dc_vbat37_ulpm_max (OFS +4.6 %) | 3.452 V | <= 3.46 V | R3 |
| r35_dc_vbat37 v3v3_dc_37 | 3.389 V (VBAT_P 3.19 / 3.26 / 3.31 V) | <= 3.45 V | R35 |
| r35_wake_vbat37 v3v3_max / vcoil_max | 3.389 / 3.389 V | <= 3.45 / <= 3.4 V | R35 |
| r35_swap_to_lithium | 3.389 V | <= 3.45 V | R35 |
| **r35_dc_vbat37_ulpm_max** (OFS +4.6 %) | **3.452 V: FAIL by 2 mV** | <= 3.45 V | R35 |
| **r35_wake_vbat37_ulpm_max** rail / coil | **3.452 / 3.452 V: FAIL by 2 / 52 mV** | <= 3.45 / <= 3.4 V | R35 |
| guard_forward_drop | 32.9 / 36.3 / 39.0 mV | <= 50 mV | R4 |
| reverse_dead_board, reverse_*_just_pulled | 2e-5 uA, VBAT_P -1e-5 V | <= 1 uA, +-0.3 V | R4 |
| sleep_floor i_bat_floor_ua (typical) | 24.32 uA | <= 26 uA | R6 |
| sleep_floor_iqmax_info (IQ_OUT 660 nA) / sleep_floor_effl80_info (EFFL 0.80) | 24.80 / 25.63 uA | information | R6 |

MC 50: 100 % in every MC analysis (the ULPM-maximum runs are information only and not in MC).

Open point for the product person (R35 at the ULPM maximum): with lithium cells the boost input (VBAT_P 3.19-3.31 V) is close to VOUT, so the ULPM-to-LPM transition current is above 200 mA and the rail stays at the ULPM level in every lithium case, including relay pulses. At the data sheet typical offset (+2.7 %) the rail is 3.389 V and the coil 3.389 V (pass, 11 mV under the 3.4 V coil limit). At the data sheet maximum (+4.6 %) the rail is 3.452 V (R35 3.45 V: 2 mV over) and the coil sees 3.452 V at the start of a pulse (3.4 V: 52 mV over; about 3.43 V steady after the 23 mV bridge drop). Same root cause as the R3 ceiling decision (3.4 -> 3.46 V). No circuit change inside my scope fixes it: a lower RSEL setting (3.2 V) would put the burst corner at about 3.12 V, under R3's 3.2 V. Options: accept 3.46 V for R35 (ESP32-C3 abs max 3.6 V) and about 3.45 V on the coil (relay limit source to check), or verify R35 at typical values as for R6.

VIN near VOUT: the data sheet states regulation only for VOUT > VIN + 150 mV and no pass-through mode. The model bounds the rail at max(target, VIN); the lithium (guard OFF) and alkaline 3.3 V guard-ON cases sit inside the 150 mV band. Bench check: notes/models.md (U2 VIN near VOUT row) and prototype test 7 below.


## Round 3 changes

- R3: VBAT range is now 2.0 V (loaded, firmware cut-off) to 3.4 V alkaline / 3.7 V lithium. The 1.8 V cases are now debug probes (`burst_vbat18_info`: corner at 1.8 V, rail 2.97 V, information only). C1/C2 are back to 2 x 3300 uF (C240437) because the corner run passes at 2.0 V (below).
- Testbench: C1+C2 leakage back to the architecture.md figure, 2.0 uA at 2.6 V for 6.6 mF (RLEAKC1 1.3 M, assumed, bench check).
- R35: the reverse guard M1/M2 is now OFF by default and is the lithium limiter (below). The gates moved from GND (R1) to VBAT_EN (IO2), with R14 1 M to VBAT_P and the new C21 100 nF gate-source. R1, Q1, R17 and C20 are removed.
- R4: CJ2305 model Rds(on) is 120 mOhm at Vgs -1.8 V (KP 8.26, search summary, unverified), with a body diode added (IS 2.1e-13, about 0.455 V at 10 uA, assumed).
- VBAT divider: R15/R16 20 k / 10 k 0.1 %, C15 1 nF, so the read is 50 us (tau 6.7 us) and the guard is ON for only 50 us when VBAT is high.
- J2: 4-pole 3.5 mm screw terminal (parts.md section 0). The TVS and 1 k series resistors are unchanged.

## R35: lithium cells (VBAT up to 3.7 V)

(Round 3, TPS61299; the MAX17227A model keeps the same upper bound, rail = max(target, VIN), see the U2 refit section.) With the guard ON a 3.7 V pack puts about 3.66 V on +3V3 (`r35_guard_on_fault_info`: 3.70 V), which is above the 3.45 V limit and the 3.6 V ESP32-C3 absolute maximum.

Chosen solution: guard OFF by default, at 0 USD added. R14 pulls the M1/M2 gates to VBAT_P, so the cells reach VBAT_P only through the two body diodes in parallel. VBAT_P is therefore at most VBAT_RAW - Vf: 3.19 / 3.25 / 3.305 V at 0 / 25 / 45 C with 3.7 V cells (`dbg_vbatp_dc_37`, Vf 0.51 / 0.45 / 0.395 V at about 16 uA). Correction to round 3: the rail does follow the cells at the hot end. At 45 C VBAT_P is above 3.3 V, the boost is in pass-through and +3V3 = 3.305 V (`v3v3_dc_37`). The R35 margin (3.45 V) therefore relies on the body-diode Vf staying >= 0.25 V at about 16 uA over temperature and part spread. The model value is assumed and the bench measurement has not been done (prototype test list below). The firmware turns the guard ON only for alkaline cells (rule 8: last guard-ON reading < 3.35 V), where the diode drop would cost run time. Reset, crash, brown-out, a dead board and reversed cells all leave the gate at the source, so the guard is OFF: the design is fail-safe.

Results (0 / 25 / 45 C where run, limits in brackets):

| Measure | Value | Limit |
|---|---|---|
| r35_dc_vbat37 v3v3_dc_37 | 3.300 / 3.300 / 3.305 V (TPS61299; MAX17227A: 3.389 V, U2 refit section) | <= 3.45 V |
| r35_wake_vbat37 v3v3_max_w37 (includes a 50 us guard-ON read) | 3.300 / 3.320 / 3.363 V | <= 3.45 V |
| r35_wake_vbat37 vcoil_max_w37 (relay pulse) | 3.30 V | <= 3.4 V |
| r35_wake_vbat37 350 mA burst min / max | 3.282 / 3.311-3.354 V | 3.2-3.4 V (R3) |
| r35_swap_to_lithium (cells out 5 s while running on C1/C2, 3.7 V in) | 3.30 V | <= 3.45 V |
| dbg_vbat_read_w37 (the reading the firmware sees, guard ON) | 3.26 / 3.32 / 3.36 V | see below |

The 50 us read at 3.7 V gives 3.26 V at 0 C (C1/C2 not yet recharged in 50 us), which is under the 3.35 V rule-8 threshold. This is why rule 8 decides on the reading after the 20 ms recharge (guard ON) and not on the 50 us read alone, and why it uses the stored last guard-ON reading. After a cold boot, VBAT_P(off) < 2.85 V decides instead (lithium: 3.19-3.31 V, so the guard stays OFF). The sims follow the rule: the alkaline cases up to 3.3 V run with the guard ON (`burst_vbat20/24`, `burst_vbat33_on`, `relay_vbat20*`, `relay_vbat33_on`, `sleep_floor`; rail max 3.300 V at 3.3 V). The 3.4 V alkaline and 3.7 V lithium cases run with the guard OFF (`burst_vbat34`, `relay_vbat34`, `relay_backemf_brake`, `r35_*`).

Rejected options (numbers from the R6 budget, about 0.19 uA margin before the restructure):
- LDO after the boost (with VSEL set to a higher VOUT): an LDO with >= 450 mA and below about 0.1 uA Iq was not found, and the LDO drop costs efficiency in every wake. Fails R6.
- Nano-power supervisor to switch the guard: TLV809EA26 (250 nA, but the 130 ms delay is on the wrong edge and the 2.64 V threshold is wrong), ME2801A33 (700 nA) and HX61CN (open drain, 2 uA) all fail on Iq, delay or threshold. TPS3839 (150 nA) is not in the hwflow index.
- N-FET follower with the gate from +3V3: intrusion depends on Vth, and costs drop at 2.6 V.
- Charge-pump or switched-boost schemes: cannot be simulated with the averaged model.
- Firmware-only guard ON in sleep (R6 24.81 uA): fails a reachable case. A user swaps alkaline for lithium while the board runs on C1/C2 (hold-up about 13 min). +3V3 then sits at about 3.66 V until the next wake (up to 60 s).

## R6: sleep floor (round 3; limit now 26 uA, MAX17227A result 24.32 uA in the U2 refit section)

`sleep_floor` (firmware: guard ON, then 20 ms light sleep at 130 uA while C1+C2 recharge, then 20 ms of 25 mA work, then IO2 high; third-minute average): **25.26 uA against 25 uA**. Without the recharge wait (`sleep_floor_nopre_info`): 25.43 uA. With the round-2 behaviour (guard ON through sleep, before the restructure): 24.81 uA.

Cause: in sleep the cells deliver nothing (dbg_i_bat_sleep_ua about 0), so C1+C2 carry the load and sag from 2.6 V to 2.45 V per minute. The boost input current at a lower VIN is higher (constant power), and the wake runs partly from the sagged caps. This is the cost of the fail-safe R35 limiter: about 0.45 uA, against the 0.19 uA margin. Options for the product person:
- A: keep this design and accept R6 at about 25.3 uA (+1 %).
- B: firmware-only guard ON in sleep, and accept the lithium hot-swap exposure (3.66 V on +3V3 for up to 60 s). Correction: the 24.8 uA figure is from round 2, when the guard gate was separate. Now VBAT_EN also drives M3, so the guard ON in sleep also turns on the R15/R16 divider: about 3.4 V / 30 k = 114 uA, which fails R6 by a wide margin. B is only possible with a hardware change that separates the guard gate from the M3 gate (one more IO), so it is not a firmware-only option.
- C: a TPS3839-class detector (about 150 nA, about 24.96 uA expected) if a stocked source is found.
- D: bench-measure the C1/C2 leakage (2 uA assumed; architecture.md expects about 1 uA per part after conditioning, which would give about 24.8 uA).

## R3 boost corner (2.0 V)

(Round 3, TPS61299 corner; MAX17227A values in the U2 refit section.) `burst_vbat20_corner` (EFF 0.80, ILIM 1.0 A, VOUT 3.234 V, cells 2.0 V through 1 ohm, 350 mA for 5 ms, guard ON): rail minimum 3.2165 V at 0 / 25 / 45 C (limit 3.2 V), VBAT_P minimum 1.54 V. MC 50: 100 % (3.216 V in every run). The nominal burst at 2.0 V gives 3.282 V. At 3.4 V and 3.7 V (guard OFF) the minimum is also 3.282 V. At 3.3 V with the guard ON (`burst_vbat33_on`) the rail stays at or below 3.3 V.

## R4: reverse insertion and Q1

Forward drop (`guard_forward_drop`, 0.7 A, cells 2.0 V, Vgs -2.0 V, 120 mOhm per FET): 32.9 / 36.3 / 39.0 mV (limit 50 mV).

| Case | Who / how often | Result |
|---|---|---|
| Both reversed, dead board, -3.7 V | user, rare | about 0 uA (2e-5 uA; limit 1 uA). The level 1 model has no IDSS; the CJ2305 IDSS (<= 1 uA at -8 V per FET, search summary) is a bench check |
| Both reversed, just pulled, after 1 h | user, rare | about 0 uA |
| Both reversed during sleep (the normal state; guard OFF) | user, rare | peak 0.29-0.30 mA (`dbg_ipk_dump_sleep`; 0.12 mA before the CBD fix below), VDS -6.0 V: no dump. C21 holds the gate against Cgd coupling |
| Both reversed during a wake (guard ON, about 40 ms per minute, 0.07 % of the time) | user, rare | `dbg_ipk_dump_both`: 14.5 / 14.1 / 13.8 A total, **7.2 / 7.1 / 6.9 A per FET**, energy **19.5 / 20.2 / 20.7 mJ per FET** at 0 / 25 / 45 C (debug). Rule 8b turns the guard OFF again after one 50 us read if the cells are reversed at the start of a wake. Verified by the prototype test (list below) |
| Forward insertion, lithium 3.7 V, 0.3 ohm, dead board (`forward_insert_dead`) | user, every battery change | guard OFF, C1+C2 charge through the body diodes: **4.07 / 4.12 / 4.16 A per FET peak**, I2t **0.020 / 0.021 / 0.021 A2s**, energy **8.2 / 8.0 / 7.8 mJ per FET** at 0 / 25 / 45 C (debug); +3V3 max 3.30 V |
| Forward insertion, alkaline 3.2 V, 0.3 ohm, dead board (`forward_insert_dead_alk`) | user, every battery change | **3.37-3.45 A per FET peak**, I2t **0.014-0.015 A2s**, energy **6.5-6.9 mJ per FET** (debug) |

Model fix (delta review): the CJ2305 `.model` line had `IS=2.1e-13CBD=150p` (no space), so ngspice did not read CBD as intended. With the space restored, every reverse case was rerun. R4 still passes: i_rev_dead 2e-5 uA, i_rev_1h_both 2.3e-5 uA, i_rev_1h_one 8e-7 uA (limit 1 uA). The forward drop is unchanged (32.9 / 36.3 / 39.0 mV), and so is the guard-ON dump. Only the sleep-time reverse peak moved, from 0.12 to 0.30 mA, through the CBD displacement current.

The forward insertion happens at every battery change. The body diode carries 4 A for a few ms, which is the same order as the CJ2305 4.1 A continuous ID (search summary, unverified). The body-diode IS and the pulsed rating were not read, so the prototype test checks it.
| One cell reversed, just pulled | user, rare | about 0 A (debug) |
| Staggered insertion | user, every change | open circuit: not simulated |

Q1: in the old topology Q1 shortened the dump (15.5 A peak, 11 us, 0.022 mJ per FET with Q1, against 14.7 A, 2.6 ms, 12.1 mJ per FET without). In the new topology the gate is driven by IO2 (25 ohm) during a wake and is at the source in sleep, so Q1 cannot pull the gate and does nothing useful. It was removed with R17 and C20. R1 is not restored: R14 1 M does its job, pulling the gate to VBAT_P instead of GND. The reversed cell makes the firmware brown out within ms, and IO2 then releases. The CJ2305 IDM for 7 A sub-ms pulses was not read (bench, prototype test).

## Failure modes (guard, R35 / R4)

- Firmware hang with the guard ON, then lithium inserted: not reachable. A board hung awake (about 25 mA) with the cells out runs C1+C2 down in about 0.3 s, far shorter than a hand swap (several seconds). The board browns out, IO2 releases, and R14 turns the guard OFF before lithium cells can go in. With the cells in, a hang only happens on alkaline (the guard is ON only for a reading < 3.35 V).
- C21 release delay: when IO2 goes hi-Z without the rule-8e high drive (reset, brown-out), R14 1 M x C21 100 nF lets the gate reach the source in about 150 ms. The guard stays partly ON for that time. Rule 8e drives IO2 high for 0.5 ms to remove this delay in normal operation.
- Dead-board start: with the guard OFF, the boost starts from VBAT_P = VBAT - Vf, which is 1.55 V at the 2.0 V cut-off (`dbg_vbatp_min_b20` 1.57 V under load). That is above the MAX17227A start-up voltage, 0.88 V typ / 0.95 V max (VIN_START, RL >= 3 k; start-up between 0.88 and 2.0 V is load-current limited, data sheet Soft-Start section; the ESP32 at reset draws more than 1.1 mA at 3.3 V, so a dead-board start at the 2.0 V cut-off is a bench check).
- Body-diode Vf (since the review-2 round: AO3413, data sheet Fig. 6): the R35 margin needs the two drops in series (M1/M2 and M4) >= 0.39 V at about 16 uA at 45 C (model 0.435 V). Bench test 4.

## Prototype test list (guard and power path)

1. 20 reversed insertions (both cells), waiting >= 90 s after each removal and insertion (C21 and C1/C2 states settle), on a running board. Check the FETs (VSD, leakage) afterwards.
2. 5 reversed lithium insertions (3.7 V), each left >= 3 min. Check that the reverse current is < 1 uA (R4) and that no part heats.
3. 20 forward lithium insertions into a dead board (C1+C2 empty): body-diode surge of 4.1 A per FET for a few ms (`forward_insert_dead`). Check the FETs afterwards.
4. Body-diode VSD at 10 uA, at 25 C and 45 C, on 3 AO3413 samples (was CJ2305; PM D4). Pass: VBAT_RAW - VIN_BST (M1/M2 + M4 body diodes in series, guard OFF, 3.70 V bench supply, board in deep sleep) >= 0.39 V at 45 C, so VIN_BST <= 3.31 V (model 0.435 V at 45 C).
5. C1/C2 leakage after conditioning (2 uA assumed for the pair; this sets the R6 sleep floor).
6. Relay coil inductance (30 mH assumed; sets the back-EMF in `relay_backemf_*`).
7. MAX17227A with VIN near VOUT (no pass-through mode in the data sheet), at 0, 25 and 45 C:
   a. Bench supply on IN at 3.20, 3.30, 3.40 V, loads 10 uA, 25 mA, 100 mA, 350 mA: +3V3 mean and peak (scope, 20 MHz
      bandwidth limit, probe tip at C4) against 3.45 V (R35), 3.46 V (R3) and 3.2 V (R3 minimum).
   b. Real lithium cells (2 x Energizer L92) through the guard, guard OFF (normal firmware): deep sleep, a K1 and a K2
      set/reset pulse and a Wi-Fi burst; scope +3V3 peak against 3.45 / 3.46 V and the coil voltage (differential, mean
      over the 31 ms pulse) against 3.4 V. Same with alkaline 3.3 V and the guard ON.
   c. At 3.70 V on IN and 10 uA (guard-ON fault): record +3V3, check no reset and no short-circuit mode.
   d. +3V3 in deep sleep on 3 samples (ULPM level, typical +2.7 % = 3.389 V, maximum 3.452 V).
   e. Dead-board start at 0 C: cells at 2.0 V loaded (bench supply + 1 ohm), C1/C2 empty: the ESP32 completes a full
      boot to the first wake log (start-up is load-limited below 2.0 V, VIN_START 0.95 V max).
   f. L1 saturation: inductor current (current probe or 10 mOhm sense in series) in the 350 mA burst at 2.0 V and in a
      short-circuit-free overload at the 2.2 A maximum peak limit: no saturation knee below 2.2 A (DFE201610E-1R0M Isat
      2.7 A, vendor table, definition not read).
8. RSEL power-up: 20 power-ups from a dead board at 0 C and 20 at 45 C (cells in, C1/C2 empty each time): +3V3 settles at
   3.3 V (3.389 V ULPM) every time, never at another Table 1 voltage (RSEL read wrong because of pad-6 capacitance or
   LX coupling).

## Firmware rules

1. Console and boot: UART0 console off, or moved to USB-Serial-JTAG (TXD0/RXD0 are K2_IN1/K2_IN2). Bootloader log level none; `esp_deep_sleep_disable_rom_logging()`. IO20/IO21 are driven low before SENS_EN goes high.
2. Sleep pin states: IO5, IO6, IO7, IO10, IO20, IO21 inputs without pulls, or held low. IO2 is hi-Z in deep sleep (R14 holds the guard OFF; do not enable a pull-down or a gpio hold low on IO2 in deep sleep; the hold is only for the 20 ms light-sleep recharge, rule 8c). LED (IO8) off.
3. Relay pulse: IN1 or IN2 high for 31 ms, then brake 5 ms, then coast; SENS_EN high around it. Energy 8.25 mJ (limit 9 mJ); coil at the rail minimum 3.18 V; t_on 31 ms.
4. Only one relay pulse at a time, and no relay pulse during Wi-Fi TX.
5. When VBAT > 3.3 V with the guard ON (only possible between 2.85 V and about 3.3 V of VBAT_P), the bridge is PWMed at duty 3.3 / VBAT. With the guard OFF the rail is regulated at 3.3 V.
6. After a cold boot the firmware re-asserts both relay states.
7. Cut-off (R23): at loaded VBAT <= 2.0 V, reset both relays OFF, then stop relay activity.
8. Guard (R35, R4). IO2 low turns on the M3 divider and the guard together.
   a. Decision: keep the guard ON for the wake while the last guard-ON VBAT reading (stored in RTC memory, taken at the end of the 20 ms recharge) is < 3.35 V. Use "VBAT_P(off) < 2.85 V" (VBAT_P read before IO2 goes low) only after a cold boot, when there is no stored reading.
   b. Reverse check before every guard ON: drive IO2 low and sample VBAT_P twice inside the 50 us read window. If the second sample is > 30 mV below the first, drive IO2 high, set it hi-Z, keep the guard OFF and report a fault. Reversed cells dump C1+C2 at about 14 A: 14 A x 50 us / 6.6 mF is about 0.1 V. A normal read changes by a few mV.
   c. Guard ON (alkaline): keep IO2 low with `gpio_hold_en(IO2)` during the 20 ms light-sleep recharge wait, then `gpio_hold_dis(IO2)`, read VBAT and store it as the new guard-ON reading. If it is >= 3.35 V, end the guard at once (step e).
   d. Guard OFF (lithium, or alkaline >= 3.35 V): the 50 us read from step b only (rail max 3.363 V at 3.7 V during it). Store its value as the guard-ON reading.
   e. At the end of every wake (and on a fault), drive IO2 high (to +3V3) for >= 0.5 ms to discharge C21, then set it hi-Z. IO2 is never held low in deep sleep (rule 2).
9. IO2 is a strapping pin. At reset R14 holds it at VBAT_P (high), which is the required strap level. At VBAT_P below about 2.5 V this is under the 0.75 x VDD input-high level (datasheet figure, recall, unverified), and the same was true in earlier rounds: bench check of boot at VBAT 2.0 V.
10. R17 conversion: R = 10 k x / (1 - x), with the optional ADC-loading correction.
11. If the R3 corner fails on the bench, cap the TX power (15 dBm) and disable 802.11b rates.
12. ADS1115 (R17): run the NTC conversions at a slow data rate (8 or 16 SPS). The +3V3 rail carries ULPM ripple of up to
    about 45 mV at kHz rates (sw_ulpm_* testbench) and SENS_EN (IO4) follows +3V3; the measurement is ratiometric and the
    ADS1115 sinc filter at 8-16 SPS averages the ripple, at higher rates residual ripple appears as reading noise.

## Relay back-EMF (assumed coil inductance 30 mH)

With the guard OFF at 3.4 V the boost regulates, so the brake case gives +3V3 max 3.389 V with the MAX17227A (ULPM level; 3.30 V with the TPS61299) (`relay_backemf_brake`). The coast debug case gives 3.50 V. Brake mode stays required (rule 3).

## Other results near a limit

- R9 coil voltage: with the guard ON at 3.3 V (`relay_vbat33_on`) the energy is 8.25 mJ and the rail max 3.30 V. The 3.4 V / 113 % case of earlier rounds no longer occurs, because the guard is OFF above 2.85 V.
- VBAT divider error (R22): 30.0-33.0 mV in MC (limit 50 mV).
- Hold-up (R24): 3.297 V minimum.

## Testbench mechanics

`sim.sources` is global, so every analysis starts with a 2 us dummy `tran` and then uses `alter` on the source PULSE vectors. IO2 is modelled by three behavioural switches: VIO2/VIO2B pull VBAT_EN to GND through 25 ohm, and VIO2H pulls it to +3V3 through 25 ohm (the end-of-wake release). ILSLP is the 130 uA light-sleep load. BPFET and BVDS are probes for the per-FET power and VDS.

## Monte Carlo (50 runs)

100 % in every analysis with MC enabled: burst_vbat20, burst_vbat20_corner (3.216 V), burst_vbat24, burst_vbat34, burst_vbat33_on, r35_dc_vbat37, r35_wake_vbat37, reverse_dead_board, the NTC cases, vbat_divider, holdup_60s. sleep_floor and the swap case are nominal only (monte_carlo false, 181 s and 5 s transients).
