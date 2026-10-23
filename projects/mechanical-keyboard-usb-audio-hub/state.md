stage: 5-review
status: waiting-for-answers

Rounds 1-6 answered (inbox/answers.md). Round 6: XU316 kept (ordered now, ~2027-01-07).
Stage 3 done: notes/parts.md (merged; cost ~$126 est. vs R17 $125, range 119-135; bus normal 483 typ/600 design-worst, heavy 657 vs 660 limit).
Stage 4 plan: pass A keyboard+MCU+hub/USB (check/ERC clean), pass B power/PD + sims R7/R8, pass C audio + sims R10/R11/R22/R29/R31/R32/R33, then --mc 50.
iteration: 1 (stage 4)
Pass A done (notes/circuit.md): 357 parts, check 0 errors (~720 s), ERC 2 (U311/U312 SEL, pass B). Floorplan PASS w/ risks. Pass B/C refs from 600.
Pass B done (notes/circuit.md pass B): check 0/0, ERC 0 errors, 121 measures pass at 0/25/40 C, MC 100 %. R19 host 0.8994 A vs 0.900 (typ models, no MC on eff/loads). Budgets L1-L4. BOM $45.87 (no audio yet).
Open for review: J311/J312 GND on IP2736U sense-shunt nets (SS return/ESD); IP2736U PDOs unconfirmed (not simulated); HOST_IMON not wired; 50 approximate power models need notes/models.md.
Pass C done: 180 measures pass, MC >= 95 % all (--mc 50), check/ERC 0/0. R22 coupled 1.6e-4 uV (AC transfer method); R10/R11 intrinsic re-allocated to 0.9999 uV (notes/noise-allocation.md); worst hp 0.967 uV @40C/300 ohm. Post-layout coupled must stay <= 0.256 uV. 400 kHz tone ~470 uVpk at HP out (out of band, uninvestigated). ES9822PRO pins 24-41 third-party. BOM $103.96 (+PCB/PCBA ~$20 -> ~$124 vs R17 $125).
Floorplan PASS w/ risks (default 20000x2, 63 s): routability 14 %, ~350 overlaps left for stage 7; regions resized (architecture.md 9).
Stage 4 done. Stage 5: full review 4 must/10 should -> fixed in 2 passes (notes/circuit.md "Stage 5 fixes, power/audio"); 225/225 measures, MC 100 %, BOM $104.60.
Delta review 1: 3 must, 5 should, 4 could -> fix pass 3 (notes/circuit.md "Stage 5 fixes, delta 1"): D1 mic preamp inverting/switched -4.5 V, D2 M657 back-feed P-FET, D3 shed HW path, D5-D8, D10 done; D11 not done.
Build now FAIL 211/257: R19 floor/heavy 0.902/0.911 A vs 0.900 (zero margin by construction), SHED_HW half-on at floor, bus_3a sheds, bus_nosink; audio analyses at 0/40 C land on a dead-board op (convergence, 25 C passes). No MC run. Unexplained: corner_max_def_hw 0.987 vs 0.761 A.
Round 7 open (C16): q1 R19 floor (55 mA calibrated / eFuse 1.13 A / no shed), q2 adapters < 65 W (CH224Q + expander vs label).
PM note 2026-10-23: MC solver aborts accepted as numerical (listed in notes/circuit.md); C701/C751 -> 50 V CL05B104KB54PNC (C307331, verify LCSC); R36 declined (inspection). Round 7 answered: q1 keep 75/60 mA, eFuse max up to ~1.13 A; q2 65 W label (notes/manual-text.md). Fix pass 4 done (notes/circuit.md "pass 4"): R654 86.6 k 0.1 % (eFuse 0.9185/1.010/1.097 A), SHED_HW rework (PGOOD-based, all 4 ports, 3 A clamp), bus_nosink window, audio 40 C twins.
Round 7 verified by `hwflow sim` (PM: no 2 h check per iteration): 265/271, the 6 fails are solver aborts/false ops (numerical, listed); MC on pd_*/bus_* in scratch/mc_bus: all >= 94 %, misses numerical or the imon_fw limit double-count (fixed: 0.4820 V). Check of the pass 4 design completed in the stopped build (0 errors); 2 warnings cleared. First-article risk: overload shed.
Shed iterations (stage 5): firmware IMON (calibration needed, rejected) -> PGOOD RC (no latch: 10-20 Hz toggling) -> PGOOD + hold/kick (pass 5; latches when host VBUS < ~4.4-4.6 V, delta review 3 must). 6 iterations: architecture change to an edge-triggered monostable (74LVC1G123 class) timer, pass 6.
Pass 6 done: monostable shed U661 74LVC1G123 (C123302); 17 scenario testbench pass, hwflow sim 265/271 (6 numerical: mic_noise 0/25 C dead-board op), MC bus_floor/heavy 100 %. Delta review 4: no must; R686 -> 100 k applied; shoulds open (VT- sweep re-run, corner window typical-only, C664/C665 LCSC).
Round 8 open (C17): eFuse max trip on 1.5 A / 3 A hosts vs R21. After the answer: set R655, one hwflow sim, then stage 6 (optimize: cost weight vs R17 $125) / stage 7 layout.
Next after answers: fix pass 4 (shed thresholds per q1, S8 per q2, audio convergence at 0/40 C), MC 50, delta review 2.
hwflow gaps: no back-side placement (sockets/diodes use back-copper footprints, CPL must be marked bottom by hand); no diff-pair field (USB pairs 45 ohm SE); hub 1V2 L/FB and VBUS divider not from datasheet (Genesys guide missing).

Decisions: full-size ANSI 104, hot-swap (sockets consigned by PM), upright EC11 knob, all user USB + jacks front, host + 65 W PD in back, USB 3, 4-layer, studio audio 120 dB, cost <= $125 (R17), bus floor >= 75/225/500 mA per port, heavy case >= 60 mA, eFuse fault trip near 1.0 A (R19/R21), FCC/CE B by design, ESD 8/15 kV, 0-40 C.

Stage 3 parts (notes/parts-{hub,power,audio,keyboard}.md, final tables on top; merge into notes/parts.md after round 6):
- Power: LM61460AFS FPWM x3 (LCSC 145, rest global sourcing), IP2736U_EF PD source x2 (+ CC comparator for orientation, bus Rp 56 k), TPS2553 + P-FET bus pass-through (120/300/605 mA), TPS25940A eFuse (0.90/0.99/1.07 A), TPS2546 x2, TPS62933F, TPS65131 (+ dummy load), CH224D (+ 100 ohm/22 V zener on VBUS), FEXL0660A-8R2M.
- Audio: bridge per round 6 (XU316 QF60B vs STM32F723VET6 vs RP2040), ES9039Q2M (datasheet read), ES9822PRO (pins 24-41 unverified), OPA1612/OPA1622, LT3045 x2, LT3094, ADP7118, SJ-3524-SMT. HP noise 0.93/0.97 uV (needs 100 ohm diff-to-SE network).
- Hub/keys: GL3523 x2, HD3SS3220, HD3SS3212 x2, TPD4E02B04, RP2040 12x11 matrix (4 spare GPIO), Kailh socket (kiswitch geometry), EC11E153440D.

Library (lib/): kb_hub, kb_keys, kb_power, kb_audio; all load in hwflow check without pin/pad mismatch.
Release blockers (human check of vendor drawings in a browser; agents cannot read them): USB-C 24P HCTL C5307768 footprint + board cut-out, USB-A HC-ST-003-01-Z height, ES9822PRO pins 24-41 + full scale, IP2736U factory PDO list (needs 5V/3A + 9V/2.22A), LM61460 RJR L-pads, encoder shaft.
Stabilizer parts need a pin map / nc in stage 4.

Next: read round 6; if XU316, recreate its QFN-60 footprint from the XMOS drawing and restore the 1.8 V/0.9 V rails; merge notes/parts.md; stage 4 circuit.
