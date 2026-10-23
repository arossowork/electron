# Simulation models (stage 4, round 4: 2 x AAA)

No vendor SPICE model is usable for these parts, so each one is approximate. The table gives the reason and what the model must get right for the measures that use it.

| Ref | Part | Model | Why it is enough |
|---|---|---|---|
| U2 | TPS610994 | `models/tps610994_avg.lib`, averaged, behavioral | TI ships only an encrypted PSpice model (ngspice cannot read it). R3/R7/R9/R16 need average power transfer, EFF(VIN), the average input limit and UVLO. No switching ripple. Details below. |
| U6 | TPS3839K33 | `models/tps3839_beh.lib`, behavioral | SBVS193D values (notes/datasheets/tps3839.md). TI PSpice SBVM235 could not be unpacked. Details below. |
| M3 | AO3416 | `models/nmos_lowvgs.lib` NMOS_LOWVGS_MAX, fit | Level-1 fit to the datasheet max RDS(on) (22 / 26 / 34 mOhm at VGS 4.5 / 2.5 / 1.8 V). VTO 1.22 V is an effective fit value: the channel is off below 1.22 V, which is pessimistic against VGS(th) max 1.1 V. Used because it is the worst case for the pack drop. |
| U4, U5 | DRV8837 | `models/drv8837_beh.lib`, behavioral switch | Ron 0.65 ohm HS + LS and sleep currents from notes/parts.md; awake Iq is an estimate. |
| K1, K2 | PE014F03 coil | `models/pe014f03_coil.lib`, R + L | Coil R at the datasheet minimum (36.9 ohm at 20 C) with the copper tempco: worst case for the bridge drop and the rail droop. L 0.1 H estimate. Contacts not simulated. |
| M2, M4 | AO3401A | generic `PMOS_LOGIC` | M2: 0.17 V drop at 92 mA against < 10 mV for the real part (pessimistic for the coil voltage). M4: in series with 300 k, its Ron is irrelevant; it only has to be off with VGS = 0 (tap held at GND by R4). |
| M1 | 2N7002 | generic `NMOS_LOGIC` | Pulls the M4 gate down; only on/off matters. |
| D1 | KT-0603R LED | generic `LED_RED` | Off in every analysis (IO8 high); leakage only. |
| Q1 | MMBT3904 | `models/mmbt3904.lib`, Fairchild/onsemi 2N3904 Gummel-Poon (vendor) | Only the turn-on speed into Ciss and the base current matter (`reverse_tran`). Junction leakage is not in GP, so the datasheet maxima are testbench sources in `sleep_wc`. |

## Review-2 model changes
- U2 PFM: the target is VOUT x tps_vscale x (1 + 0.03 x w), where w = 1 below about 1 mA of output current and 0 above 2 mA (SLVSD88M: 3.4 V typ in PFM). `tps_vscale` = 1.0212 gives the 3.37 V max corner (PFM 3.47 V), which `sleep_wc` uses.
- U2 UVLO hysteresis: on above 0.65 V, off below 0.45 V (200 mV typ, SLVSD88M 6.5), with a latched state (`uv`/`uvf`).
  - The old smooth step (60/V, no hysteresis) left VIN at 0.72 V after pack removal, against 0.45-0.62 V now.
  - r3_vin_tx_110 at 0 C is 0.724 V with the 2.56 V cutoff.
- `exp()` arguments are clipped to +/-50. An unclipped PFM weight overflowed at Wi-Fi load currents and corrupted single MC runs.
- U6 IQ default: 150 nA typ (was 500 nA). `sleep_wc` sets 500 nA.
- M3 in `reverse_tran`: `altermod nmos_lowvgs_max vto=0.4` (VGS(th) min) and `vto=0.7` (typ). These are the worst cases for a reversed pack, because the channel conducts at the residual gate voltage.
- Sleep loads are switched by the global `.param wc` (0 typical, 1 worst). The table is in notes/circuit.md "Review-2 fixes".

## TPS610994 averaged model (`models/tps610994_avg.lib`)

### Efficiency EFF(VIN), heavy load (>= 1 mA)
Source: TI SLVSD88M Fig 6-5 (efficiency vs output current, VOUT 3.3 V). The 0.7 V and 1.0 V points were digitised in review-1. The 1.5 V and 2.5 V points are the architecture-stage reading (architecture.md 3.3: "0.80 at VIN 0.7 V, 0.83 at 1.0 V, 0.89 at 1.5 V, 0.93 at 2.5 V"; page 1 text: "up to 93 % at 10-300 mA"). The PDF was not re-opened in this stage.

| VIN | EFF | used as |
|---|---|---|
| 0.7 V | 0.80 | EFFV07 |
| 1.0 V | 0.83 | EFFV10 |
| 1.5 V | 0.89 | EFFV15 |
| 2.5 V | 0.93 | EFFV25, held up to the down-mode entry |

Linear between the points, held outside them.

### Down mode
SLVSD88M 7.3.8: for VIN > VOUT - 50 mV (3.25 V) the high-side PMOS regulates linearly, up to the 3.8 V pass-through entry. In that mode the input current equals the output current, so the efficiency is VOUT/VIN. The model uses min(EFF(VIN), VOUT/VIN). This is continuous, and above 3.25 V it is never better than the boost value (conservative). A fresh lithium pack (3.6 V, 3.47 V under the 150 mA wake) runs in down mode; the rail stays at 3.30 V (r3_vmax 3.304 V). The current limit stays active in down mode (7.3.5). Pass-through is not modelled (the pack is <= 3.6 V). The PFM output of 3.4 V typ at light load is modelled since review-2 (see above).

### Light load
EFFL 0.75 at <= 10 uA ("up to 75 % at 10 uA", p.1), which is the architecture sleep figure. It blends log-linearly to EFF(VIN) at 1 mA. EFFL contains the quiescent current, so IQVIN = IQVOUT = 0.

### Input current limit and start-up
- Average input limit = switch peak limit - ripple/2 = 0.8 / 1.0 / 1.25 A - 0.15 A = **0.65 / 0.85 / 1.10 A** (min / typ / max).
- It is the global `.param tps_ilim` (default 0.65). The `tx_ilim` analysis steps it with `alterparam` + `reset`.
- Below VOUT 2.5 V the start-up limit is 0.7 x 0.5 A - 0.15 A = 0.20 A (ILIMSS).
- UVLO: 0.65 V rising (0.6 V typ, 0.7 V max), 0.45 V falling (200 mV hysteresis, review-2).

### Input node
The averaged input current is taken at the VIN pin (the C4 node). L1 stays in the netlist with zero average current; its DCR is inside EFF. An ideal L1 in series with a constant-power input oscillated with C4 in stage 4 (round 1).

## TPS3839K33 (`models/tps3839_beh.lib`)
- SBVS193D: VIT- 2.857 / 2.93 / 2.974 V, hysteresis 29 mV (release 2.886 / 2.959 / 3.003 V), td 120 / 200 / 350 ms, IDD 150 nA typ / 500 nA max, push-pull.
- The corners are the global params `tps3839_vit`, `tps3839_td` and `tps3839_iq`. Defaults: all typical (IQ 150 nA). `sleep_wc` sets IQ 500 nA.
- The `powerup` analysis steps VIT/td to the min corner (2.857 V, 120 ms) and to the max corner (2.974 V, 350 ms).

## M3 gate (the reason for R16/C21)
With the gate tied straight to pack +, VGS equals the converter VIN. At the 0 C cutoff and the 1.10 A input limit, VIN falls to 0.70 V in a TX burst. That is below VGS(th) max (1.1 V), so the body diode carries the current.

Simulated with the gate on VBAT (same testbench):

| Corner | VIN (limit 0.7 V) | Rail (limit 3.0 V) |
|---|---|---|
| 0 C, 0.85 A | 0.687 V | - |
| 0 C, 1.10 A | 0.673 V | 2.964 V |
| 50 C, 1.10 A | 0.696 V | - |

With R16 100 k / C21 1 uF (tau 0.1 s) the gate holds the pre-burst pack voltage, and all three corners pass.

## Pack model (testbench, design.yaml sim.sources)
- Two cells in series. Voc schedule: VCELL plus the temperature-aware cutoff source BCOLD.
- Cutoff Voc = max(2.20, 0.72 + 1.10 x (2 Rcell(T) + 0.07)), rounded up to 10 mV. That gives 2.56 V at 0 C (review-2 S3) and 2.20 V at >= 8 C.
- Pack removal and reversal (review-2 S1): VREM/VSGN in the global schedule open the pack at 4.30 s and reverse it at 4.80 s; no analysis reaches these times now. `reverse_tran` re-times them with `alter @vrem[pwl]` / `@vsgn[pwl]` (pull at 50 ms in sleep, reverse at 0.55 s) and runs with `option method=gear`; trapezoidal integration rang point to point on Q1/M3 and latched a false conducting state (notes/circuit.md "reverse_tran testbench rebuilt").
- `.param deadop` (reverse_ins only): 100 ohm terms inside BCBUF_LEAK / BIQ2I hold +3V3 and VBAT at 0 V in the operating point, released by 0.5 ms. It replaces the luck of the Newton start for a dead board.
- CS1238 gain error and calibration (ntc_tran): `.param cs_gerr=0.005 cs_gdrift=8e-6`, `.func ntct()` (firmware R-T conversion), track-and-hold nodes h1a/h1b/h2a/h2b (1 F, B-source charge, tau 0.1 ms). These are behavioral testbench elements, not circuit models.
- Rcell(T) = 0.40 x max(1, 2^((25 - T)/25)) per cell: 0.40 ohm at 25-50 C, 0.80 ohm at 0 C.
- Series resistance 0.04 ohm. The requirement's 0.07 ohm covers contacts, wiring and the reverse FET; M3 itself (max fit, about 0.03 ohm at VGS 2 V) is in the netlist.
- `ins` is the pack insertion switch (`.param ins_t`). It is closed for every analysis except `powerup`, which inserts the pack at 1 ms into a dead board. This replaced `uic`, because ngspice applies `.nodeset` values as initial conditions under `uic`.
- `.nodeset` (regulated rail, U6 released) is a Newton starting point. Without it the DC sweep fell back to an unconverged "transient op" (rail 0.78 V at 0 C, 2.999 V at 50 C).
- The GPIO high level follows +3V3 (BIO2, BIO8). A fixed 3.3 V source had back-powered the rail through R9/R14 when there was no pack.

## S1 fix (review-2 S1, 2026-10-19)
- No new model. R20 is an ideal resistor. C23 now goes to GND. Q1 is the same onsemi 2N3904 Gummel-Poon vendor model (`models/mmbt3904.lib`).
- The fix depends on the Q1 VBE(on) and its tempco, both from the vendor model. The model gives a turn-on at VBAT = 0.327 x Vgate - VBE: about +0.2 V at 0 C and +0.3 V at 50 C. The 50 C margin is larger.
- reverse_tran is still Gear with the 0.5 us breakpoint grid. A temporary waveform dump (wrdata, since removed) showed the C23-to-BAT_N latch and the Q1 desaturation with R19 10 k (notes/circuit.md).
