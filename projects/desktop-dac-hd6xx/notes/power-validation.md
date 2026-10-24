# Power-sequence validation — qualified circuit analysis

> Layout-only revision: `verification/circuit-revision-equivalence.json` compares the validated electrical checkpoint with the later connector-region padding and two pad-specific clearance rules. All other source fields, including every component value, pin assignment, model, net and analysis, are identical; the original electrical results remain evidence for those unchanged inputs. This is an equivalence check, not a new electrical simulation run.

The current source fixes two electrical logic defects: the supervisor no longer charges three RC loads through its 10 kΩ pullup, and the two later TPS7A20 enables now use a logic AND gate whose low output can disable those regulators. The calculations pass their stated envelope. **Real output-rail order, arbitrary power ramps, converter dynamics, and hardware protection remain unverified.** This is not a vendor-model simulation or a fabrication release.

Run `python projects/desktop-dac-hd6xx/verification/power_checks.py` from the repository root. The script reads the live component values and pin maps, rejects a changed topology, and writes `verification/power-results.json` plus `verification/power-waveforms.csv`. The JSON records SHA-256 hashes of both `design.yaml` and the model; rerun after any source edit. Its `qualified_checks_passed` and `hardware_status` fields deliberately report different things. The CSV is an illustrative event trace; its assumed LDO ramp and typical discharge are not evidence of actual rail waveforms.

## Circuit changes and component limits

- U330/U333/U335 use [SN74HCS14DR](https://www.ti.com/lit/ds/symlink/sn74hcs14.pdf), with the standard 14-pin inverter mapping. HCS has much tighter switching thresholds than HC. Across the published 2/4.5/6 V test points the model takes rising thresholds from 0.498 to 0.685 × VCC and falling thresholds from 0.260 to 0.355 × VCC. Applying this encompassing ratio range between those test points is an explicit qualification assumption. The ±1 µA maximum input leakage is included. Loaded output drop ≤0.5 V is also an assumption below the datasheet's 4.5 V, 6 mA test point.
- U335 buffers `ADAPTER_OK` through two inversions. R352 now supplies only its input, keeping maximum calculated supervisor sink current below 0.532 mA. R365 limits charge current into D340–D342 and their common anode. The startup charge time uses **the sum** of C342/C343/C344, not just C344. D347/D348 clamp `PWR_GOOD`/`DAC_EN` to the buffered output. R118 = 2.2 kΩ limits the additional DAC-delay capacitor discharge current; the calculated total buffer sink bound is 2.215 mA.
- U336 is [SN74LVC2G08DCUR](https://www.ti.com/lit/ds/symlink/sn74lvc2g08.pdf), powered by `3V3_D`; its inputs tolerate the hold-domain logic voltage. Pins 1,2 → 7 drive `EN_C`; pins 5,6 → 3 drive `EN_A`; ground is 4 and VCC is 8. The KiCad 10 `74xGxx:74LVC2G08` symbol and VSSOP-8 footprint pads were checked in the pinned image. At the enable input's sub-microampere load, powered VOL ≤0.1 V. At VCC = 0, 10 µA Ioff through each 10 kΩ +1% pulldown gives ≤0.101 V. Both are below the regulator's 0.3 V disable limit. Operation during intermediate, out-of-range VCC is still a bench check.
- [TPS7A20](https://www.ti.com/lit/ds/symlink/tps7a20.pdf) guarantees disable only at EN ≤0.3 V; a Schottky diode added to another output's low level did not establish this. Its 145 mV maximum dropout specification is measured at **95% of nominal output**. The full ±1.5% regulation table instead requires VIN ≥3.6 V for this 3.3 V part. The 150 Ω discharge resistance is typical only; the published startup bound uses 1 µF output capacitance and 1 mA load, unlike this circuit.

## Conditional timing and hold-up results

The model uses source R/C values with ±1% resistors, ±10% timing capacitors, C340 −20%, 40/20/50 mA D/C/A rail allowances, and 5 mA extra hold-domain current. Those currents are engineering allowances, not an ESS maximum. It assumes D301 drop ≤0.5 V and charge-diode forward drop ≤0.4 V. [BAT54WS](https://www.diodes.com/datasheet/download/BAT54WS.pdf) gives 2 µA maximum reverse leakage at 25 °C; using it as a hot-temperature bound is **not guaranteed**. Cold forward drop and hot leakage must be measured. The driver-voltage assumptions must also hold at the minimum reservoir voltage.

With [TPS3808G50](https://www.ti.com/lit/ds/symlink/tps3808.pdf) at its −2% trip corner, initial `V5_HOLD` is 3.801 V. The supervisor release delay is 180–420 ms; the 20 µs assertion allowance applies to its stated sense pulse/overdrive conditions, not every shallow brownout. After the buffer falls, the source's 100 kΩ × 2.2/10/47 nF hold networks give:

| Disable | Earliest | Latest |
|---|---:|---:|
| A / AVCC | 0.125 ms | 0.357 ms |
| C / VCCA | 0.566 ms | 1.622 ms |
| D / AVDD | 2.662 ms | 7.623 ms |

Each interval independently includes resistor/capacitor, threshold, and leakage corners. The calculation allows the switching threshold supply to fall as far as 3.6 V and then checks that the calculated supply stays above it. Integrated charge is 0.396 mC; with 3760 µF the final reservoir is at least **3.696 V**, a conditional 96 mV margin to 3.6 V. A sweep of 101 shared initial supply values checks that independent gate corners preserve A-before-C and C-before-D. This is enable ordering; unknown discharge-resistance spread prevents claiming the same proof for rail voltage crossings.

For startup, the 100 kΩ/22 nF AVDD-to-VCCA stage gives an ideal rail-step delay of 1.295–3.000 ms. Later stages give 1.243–3.187 ms, including independent ±1.5% source-rail and logic-supply corners. The first delay exceeds the [ESS approximately 200 µs AVDD-before-VCCA recommendation](https://www.esstech.com/wp-content/uploads/2026/05/ES9039Q2M_Datasheet_v0.2.3.pdf). Actual LDO rise time and logic behavior while unpowered are outside that ideal-step calculation. A source-derived interrupted-startup analysis checks five cutpoints over 101 common supply corners: before C can turn on there is at most D to turn off; after C can turn on, all hold RCs have charged beyond 99.9%. The pessimistic common charge time constant is 65.77 µs and 99.9% settling takes 454 µs, earlier than C's earliest modeled enable. Reverse order remains separated using 99.9%, rather than perfect, initial charge. The 180 ms minimum supervisor restart delay also exceeds the entire modeled shutdown interval.

The logic check exhausts all 64 combinations of the actual U102 qualifiers. `AUDIO_READY` requires power good, USB cable, unmute, PCM, DAC enable, and the DAC delay. Adapter loss removes the direct DAC-enable qualifier before the earliest modeled A disable. This verifies the intended combinational logic, not the receiver's undocumented startup or clock behavior.

The [LM3880](https://www.ti.com/lit/ds/symlink/lm3880.pdf) was rejected as a replacement: interrupted startup after FLAG1 causes the remaining startup sequence to finish, followed by approximately 120 ms before shutdown. The existing reservoir cannot sustain the assumed 110 mA for that interval. Its 0.4 V maximum flag low also does not directly satisfy the TPS7A20 0.3 V requirement.

## Required first-board measurements

Probe `V5_SYS`, `V5_HOLD`, `ADAPTER_OK_BUF`, `DAC_EN`, all three enables, and all three DAC rails; repeat synchronized captures if channel count is limited. Measure actual mode-8 rail currents at maximum sample rate and silence/full output, while checking that the total rail allowances plus hold overhead are respected.

Capture startup with fast and slow adapter ramps, and unplug before D starts, during D ramp, during C ramp, during A ramp, and after normal operation. Repeat with 20 µs, 0.1 ms, 1 ms, 5 ms, and 50 ms brownouts, shallow threshold crossings, and repeated interruptions. Confirm no `DAC_EN`/`AUDIO_READY` pulse occurs while rails are invalid, including the interval where the logic supply is below its specified operating minimum. Confirm EN_A/C stay below 0.3 V when disabled, and that hold-up remains above 3.6 V until D is disabled.

Measure the falling rail waveforms at both 95% and 0.3 V crossings with the lowest and highest realistic loads, USB attached and detached, temperature extremes, and capacitor tolerance/aging allowance. Demonstrate the ESS shutdown order: CHIP_EN, AVCC, VCCA, AVDD. Add controlled discharge hardware or revise timing if rail waveforms violate that order; changing an analytical typical value is not a fix.

Separately measure hot-plug inrush and R354 pulse temperature, the real B340A drop, reservoir capacitance/leakage, converter startup/ripple, USB back-power, and hard rail shorts. None of those checks is replaced by this model, ERC, or routed DRC.
