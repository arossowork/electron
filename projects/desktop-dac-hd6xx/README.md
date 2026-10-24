# Desktop DAC and headphone amplifier

A USB-C DAC and desktop headphone amplifier for Luca's 300 Ω Sennheiser HD6XX, and a reference circuit for improving the code-to-PCB compiler. Target: stereo PCM through 384 kHz, physical volume knob, 6.35 mm output, external regulated 5 V adapter, and a EUR 150–250 complete prototype. No parts have been ordered.

The working PCB remains **140 × 80 mm, four layers**, inside a provisional **160 × 100 × 40 mm** enclosure. `design.yaml` now contains **235 real components and 128 nets**, plus a measured envelope for the separate Amanero module. It is the single authoritative circuit source. Four connector anchors are fixed; the other 231 components are movable within explicit regions.

The circuit includes an Amanero Combo768 interface, ES9039Q2M hardware-mode DAC, two OPA1612 output-stage packages, OPA1622 headphone driver, relay/DC protection, and sequenced/monitored supplies. The custom receiver footprint follows Amanero's sequential row numbering. The exact panel pot, jack, enclosure and harness construction remain open.

## Current evidence

| Gate | Result |
|---|---|
| Source checks and symbol/footprint mapping | PASS: 0 errors, 5 warnings |
| Generated KiCad schematic ERC | PASS: 0 errors, 4 warnings |
| Source-to-KiCad connectivity | PASS: all 683 assigned pins match |
| Real-footprint floorplan | PASS: 60% fill, 29.6 mm depth, 0 quick-placement violations |
| Amplifier model simulation | FAILED to converge; no valid output measurement |
| Startup/shutdown and fault waveforms | NOT CHECKED |
| Routed PCB, DRC and manufacturing output | NOT CHECKED; circuit verification prerequisite remains open |
| Delivered cost and product requirements | Unpriced/incomplete; 0 of 10 requirements verified |

This is a reviewable circuit checkpoint, not a fabrication-ready board. [SUMMARY.md](SUMMARY.md) and [notes/circuit-baseline.md](notes/circuit-baseline.md) distinguish the completed checks from the remaining gates. The generated schematic and floorplan are in `build/` and can be regenerated; never edit them manually.

Three compiler corrections are separately reviewable: [explicit verification evidence](notes/verification-baseline.md), [clearance-preserving final placement rounding](notes/placement-clearance.md), and [complete schematic exports](notes/schematic-page-sizing.md). The final fast suite passed 293 tests (2 skipped, 14 deselected). No routing-speed improvement is claimed.

- [requirements.yaml](requirements.yaml): user choices, targets and acceptance criteria.
- [architecture.md](architecture.md), [analog-design.md](analog-design.md), [parts.md](parts.md): architecture and original calculations.
- [notes/dac-circuit.md](notes/dac-circuit.md), [notes/analog-circuit.md](notes/analog-circuit.md), [notes/power-circuit.md](notes/power-circuit.md): circuit decisions and source documents.
- [notes/mechanics.md](notes/mechanics.md): module CAD measurements and remaining assembly work.
- [notes/models.md](notes/models.md): model provenance and limitations.
- [compiler-experiments.md](compiler-experiments.md): controlled comparison protocol.
- [state.md](state.md): restart point.
