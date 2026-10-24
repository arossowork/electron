# Amplifier model integration repair — 2026-10-24

## Accepted integration

`lib/spice/opa1622_ti.lib` and `lib/spice/opa161x_ti.lib` are now byte-for-byte copies of TI's original PSpice libraries. No amplifier component value, equation, protection limit, or electrical target was changed. Run ngspice 42 with explicit per-process `-D ngbehavior=ps`; native/default parsing is not qualified. The verification runners also use `-n` so user initialization files cannot silently change the test. The pinned runtime is `hwflow-audio:ca10fa8e` (ngspice 42, Python 3.11).

| File/source | Release | SHA256 |
| --- | --- | --- |
| [TI SBOM958 ZIP](https://www.ti.com/lit/zip/SBOM958) | OPA1622 PSpice, Final 1.5, 05JUL2022 | `1bafa50b657ea2ece0c1d3e1ab7684556093d824a7d749aaaf07a933b81e49b8` |
| Extracted `OPA1622.LIB`, installed as `opa1622_ti.lib` | unchanged TI bytes | `9dfaf17ed53e37d8930376f042379e828d96bc7d10349fed0dc058daf695223e` |
| [TI SBOM396G ZIP](https://www.ti.com/lit/zip/SBOM396G) | OPA161x PSpice, Final 1.5, 24AUG2022 | `8b567b729dd8bdc459650d41a5af84a5f7082a7ea06ea77406ba8a9db1348440` |
| Extracted `OPA161x.LIB`, installed as `opa161x_ti.lib` | unchanged TI bytes | `c86df5d4b2d26ec196c0a6158a61004a6747aec5440fcc2031674ad62a448ef7` |
| Rejected inherited OPA1622 translation, retained under `verification/baseline/` | regression fixture only | `988b1a37544cce0a799e3bb975599dabe1cb93c870add03cfcce439324d5a9fc` |

TI copyright/model notices and original CRLF/trailing spaces are intentionally retained. Consequently ordinary `git diff --check` reports whitespace in these two vendor files; authored notes and verification code are checked separately. The previously investigated TINA Final 1.4 model is not the installed source.

## Failure mechanism and evidence

The inherited mechanical translation changed all six PSpice `VSWITCH` models into positive-hysteresis native `SW` models. That changes the continuous switch characteristic, especially the two self-controlled 0–10 mV clamp switches. Ngspice 42's actual PSpice converter emits XSPICE `pswitch` with `%gd` control/output ports and cubic interpolation in logarithmic resistance; neither native `SW` nor a guessed `aswitch` substitution is equivalent. See the [version 42 converter](https://github.com/imr/ngspice/blob/ngspice-42/src/frontend/inpcompat.c), [pswitch implementation](https://github.com/imr/ngspice/blob/ngspice-42/src/xspice/icm/xtradev/pswitch/cfunc.mod), and [SW implementation](https://github.com/imr/ngspice/blob/ngspice-42/src/spicelib/devices/sw/swload.c).

The inherited translation also left `LIMIT(...)` without the PSpice clamp definition. Native ngspice's statistical `limit` function has different semantics; see the [ngspice maintainer's explanation](https://sourceforge.net/p/ngspice/bugs/505/). Adding an explicit clamp and the proper switch primitive repaired a zero-input diagnostic, but a hand-translated native model still produced invalid rail-exceeding results at nonzero input. The same candidate worked under `ps`. Therefore no hand translation is accepted or installed: the complete original library is processed by the simulator's supported PSpice compatibility path, which also handles temperature, functions, switches, and noiseless resistor temperature semantics.

The original isolated failure is reproducible with `verification/opa1622-baseline.cir`: gain 2, 10 kΩ/10 kΩ, ideal ±9 V rails, 300 Ω load, zero input. `timeout 20s ngspice -n -b opa1622-baseline.cir` returned 124 after failed gmin/source stepping and entering transient operating-point fallback. Its log is `verification/baseline/opa1622-translated.log`. The accepted original model reaches a physically checked operating point with `ngspice -n -D ngbehavior=ps -b opa1622-op.cir`. No manual gmin, KLU, tolerance, UIC, or initial capacitor-voltage changes were used in accepted tests.

## Focused results

The final saved-file rerun passed **16 OPA1622 cases** and **13 OPA161x/analog-chain cases**. Reproducible runners are `verification/verify_opa1622.py` and `verification/verify_audio_chain.py`; numerical results are in `verification/opa1622-results.json` and `verification/audio-chain-results.json`. Each simulator invocation has a 20 s subprocess timeout. Host runs used named disposable containers with `timeout 90s` around each suite.

OPA1622 results use 2 Vrms input, gain 2, and 300 Ω loading:

| Check | Result |
| --- | --- |
| Zero-input output, 10 kΩ feedback network | 12.0965 mV; includes input-bias-current error, not solely input offset |
| 20 Hz, ±9 V and ±8.1 V rails | 3.999998 Vrms output; gain 1.999999 |
| 1 kHz, both rail values | 3.999998 Vrms; gain 1.999999 |
| 20 kHz, both rail values | 4.000146 Vrms; gain 2.000073 |
| ±2.82843 V DC input | −5.644754 V / +5.668946 V output, both inside ±9 V rails |
| ±5 V overdrive input | −7.915852 V / +7.913825 V clipping with ±9 V rails |
| Audio AC gain | 1.999999–2.000073; agrees with transient gain |
| Follower input offset / input bias / unloaded Iq at ±18 V | 99.998 µV / 1.200 µA / 2.60119 mA |
| Current limit, inverting test into 1 Ω | −132.620 mA / +145.000 mA |
| Inverting slew-rate test at ±18 V | falling 9.676 V/µs; rising 10.031 V/µs |

These last checks are consistent with the [OPA1622 datasheet](https://www.ti.com/lit/ds/symlink/opa1622.pdf): 500 µV maximum offset, 2.6 mA typical quiescent current, approximately +145/−130 mA output current, and 10 V/µs typical slew rate. The current-limit test keeps the input differential voltage small using an inverting feedback circuit; driving a shorted voltage follower with a large ideal input would instead conduct its input ESD network and is not a clean output-current-limit measurement.

The gain-2 bench’s 12.0965 mV zero-input output is consistent with input bias current through its 10 kΩ feedback resistor. The solved inverting-input voltage is 98.242 µV and the feedback-path input current is approximately 1.190 µA, so `Vout = 2 × Vfb + 10 kΩ × IB− ≈ 12.0965 mV`. The separate follower test measures 99.998 µV input offset, consistent with the datasheet’s 100 µV typical input offset; the 12 mV circuit output must not be compared directly with the input-offset limit. Bias-current error remains relevant to the actual circuit’s DC output.

OPA161x passes zero-input OP, DC sweeps from zero through both ±2.82843 V input polarities, and 20 Hz/1 kHz/20 kHz sine tests at gain 2 into 2 kΩ. Gains are 1.999993, 1.999994, and 2.000001. Follower offset is 100.569 µV and Iq is 3.60004 mA, consistent with the [OPA161x datasheet](https://www.ti.com/lit/ds/symlink/opa1612.pdf) limits/typical values.

The one-channel analog-chain bench uses `Design`, `NodeMap`, and `component_blocks` to generate the current R200–R207/C200–C204 topology, actual U200/U202 dual-package wrappers, and R240–R243 directly from `design.yaml`. The unused U202 channel is terminated with zero input and its actual feedback. The physical potentiometer header defines the maximum-volume connection, with an explicit 10 kΩ pot load and 300 Ω headphone loads. Results record the design SHA256, complete selected component/pin/value maps, wrapper hash, and both model hashes. The validated design snapshot is `556b89df64223c63651a788429b29bce4f98e7e82559a325971bd939ba9d63c1`; rerun after changing the selected circuit or wrappers. It uses explicitly defined ideal complementary DAC currents: 1 mA common DC and 1.571348 mA sine peak per output. Both currents ramp from zero over one tenth of the signal period, starting from a normally solved operating point. After settling, output is 3.944775 Vrms at 20 Hz, 3.944703 Vrms at 1 kHz, and 3.916133 Vrms at 20 kHz, at both ±9 V and ±8.1 V rails. DAC input common-mode remains 1.791–1.809 V. This verifies this analog topology under the defined stimulus; it does not establish the actual DAC's current scale, compliance, startup waveform, or noise.

## Retained limitations

- The original OPA161x model fails a cold operating-point solve starting directly at −2.82843 V input, reporting an internal noise-diode singularity. `verification/baseline/opa161x-cold-op.cir` and its log preserve this negative result. DC continuation from zero and normal sine transients pass without changing solver settings. The coupled I/V stage also fails cold startup with immediate nonzero DAC common current; algebraic `.nodeset` hints did not resolve it. The accepted chain bench explicitly ramps ideal input currents from a solved zero-current state. This is a simulation boundary, not evidence that real DAC power-up or the protection sequence is safe.
- OPA1622 EN, GND-pin behavior, exposed-pad/thermal behavior and package coupling remain unmodeled. The package wrapper maps two single-channel models and does not simulate enable/mute response.
- The macromodel tests do not qualify THD+N, noise, manufacturing variation, board parasitics, capacitive-load stability, relay operation, power sequencing, or protection safety. Sine-fit residuals are only a signal-shape regression check, not a distortion specification.
- Both rails are ideal in these tests. Physical supply converters, the DAC, potentiometer mechanics and real headphones are not modeled. These results alone must not mark whole-product requirements verified.

## Reproduction

Inside the pinned container, with this project as working directory and `PYTHONPATH=/workspace/src` (where `/workspace` is the repository mount):

```sh
python verification/verify_opa1622.py --model lib/spice/opa1622_ti.lib --output /tmp/opa1622-validation --compatibility
python verification/verify_audio_chain.py --model-dir lib/spice --output /tmp/analog-chain-validation
```

The output directories retain every generated bench, simulator log, data table, and summary. Bound the entire named `--rm --network=none --userns=keep-id` container with `timeout 90s`; the scripts independently bound each ngspice process. The original-model failure fixture must be run without PSpice compatibility, while the OPA161x cold-start limitation fixture requires `-D ngbehavior=ps`.
