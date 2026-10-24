# Desktop DAC and headphone amplifier

A USB-C DAC and desktop headphone amplifier for Luca's 300 Ω Sennheiser HD6XX, and a reference circuit for improving the code-to-PCB compiler. Target: stereo PCM through 384 kHz, physical volume knob, 6.35 mm output, external regulated 5 V adapter, and a EUR 150–250 complete prototype. No parts have been ordered.

The PCB remains **140 × 80 mm, four layers**, inside a provisional **160 × 100 × 40 mm** enclosure. `design.yaml` is the authoritative circuit source: **238 real components, 133 connected nets**, four fixed connector anchors and 234 movable components. A separate measured envelope reserves the Amanero module.

The circuit contains an Amanero Combo768 interface, ES9039Q2M hardware-mode DAC, two OPA1612 packages, OPA1622 headphone driver, relay/DC protection, and sequenced/monitored supplies. Exact panel pot, jack, enclosure and harness construction remain open.

## Current evidence

| Gate | Result |
|---|---|
| Source checks / generated KiCad schematic ERC | PASS: zero errors; 5 source and 4 ERC warnings remain documented |
| Source-to-KiCad connectivity | PASS: all 703 assigned pins match |
| Real-footprint floorplan | PASS: 61% front fill, 29.6 mm depth, zero placement and fixed-rule violations |
| Original TI models in PSpice compatibility mode | PASS: 16 OPA1622 checks; 13 OPA161x/analog-chain checks |
| Project amplifier-stage simulation | PASS: 14 measures; all 50 tolerance trials meet limits |
| Power-sequence calculation | 17 checks pass within the documented assumptions; physical rail order is unverified |
| Protection circuit | 24/24 qualified checks plus independent passive ngspice comparison pass; hardware unverified |
| Routed PCB and DRC | Partial PCB: 3 signal connections open; zero other copper DRC errors; 9 decoupling-path warnings |
| Bench tests / delivered cost / whole-product requirements | NOT CHECKED; 0 of 10 requirements verified |

Model checks use ideal supplies and a defined ideal DAC-current stimulus. The modeled analog chain delivers 3.92–3.94 Vrms into 300 Ω across the tested audio frequencies. This does not establish real DAC full scale, noise/distortion, power transitions or safety of headphone-connected hardware.

See [SUMMARY.md](SUMMARY.md) for the checkpoint and [state.md](state.md) for the restart point. The generated schematic and floorplan live in `build/`. The retained PCB is `build/layout-repair-followup/desktop_dac_hd6xx.kicad_pcb`, with a native `pcb.svg` preview beside it. These outputs must be regenerated through the engine rather than edited manually.

## Evidence and design decisions

- [requirements.yaml](requirements.yaml), [architecture.md](architecture.md), [analog-design.md](analog-design.md), [parts.md](parts.md): accepted targets and architecture.
- [notes/dac-circuit.md](notes/dac-circuit.md), [notes/analog-circuit.md](notes/analog-circuit.md), [notes/power-circuit.md](notes/power-circuit.md): connections and manufacturer references.
- [notes/model-repair.md](notes/model-repair.md), [notes/power-validation.md](notes/power-validation.md), [notes/protection-validation.md](notes/protection-validation.md): qualified electrical analysis and limits.
- [notes/layout-validation.md](notes/layout-validation.md), [notes/mechanics.md](notes/mechanics.md): placement/routing evidence and remaining assembly work.
- [notes/simulation-build-repair.md](notes/simulation-build-repair.md): simulator/audit/YAML repairs; final fast suite 327 passed, 2 skipped, 14 deselected.
- [compiler-experiments.md](compiler-experiments.md): controlled benchmark protocol; no routing-speed improvement is claimed without a matched comparison.
- [notes/circuit-baseline.md](notes/circuit-baseline.md): historical circuit checkpoint before these repairs.
