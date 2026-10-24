# DAC/headphone amplifier: circuit checkpoint

The accepted **140 × 80 mm, four-layer board** now has a component-level circuit for USB-C PCM audio through 384 kHz: Amanero receiver interface, ESS DAC, analog output filter, physical-volume interface, OPA1622 headphone driver, DC protection/relay, and monitored/sequenced supplies. The separate USB module has a measured mechanical envelope. The proposed enclosure remains 160 × 100 × 40 mm.

Source checks and KiCad ERC pass with zero errors; all 683 assigned pins match the exported netlist. Real-footprint packing passes with 60% estimated fill, a 29.6 mm height stack and zero quick-placement violations. The schematic export now includes the whole circuit. These results establish a reviewable circuit and feasible packing, not a finished routed board.

## What is blocked

The inherited TI-derived OPA1622 model fails to converge even in a direct single-amplifier test. The distinct official TINA model is not directly compatible with this ngspice flow and fails parsing. There is no valid measured or simulated 4 Vrms result. Startup/shutdown and DC-fault timing also need qualified scenario tests. The repository hardware workflow requires the circuit measures to pass before layout; routing, DRC, post-layout checks and manufacturing files therefore remain NOT CHECKED.

## Compiler improvements exercised

- Manual verification now needs explicit, current evidence; a note saying NOT CHECKED cannot be counted as a pass, and simulation cannot satisfy a bench-test requirement.
- Final placement grid rounding preserves hard clearance/keep-out/bounds constraints instead of introducing a small violation after optimization.
- Generated schematic paper size expands to include the complete drawing instead of silently clipping a large circuit at A3.

Each compiler change has targeted regression coverage and independent review. The final fast suite passed 293 tests (2 skipped, 14 deselected). [notes/circuit-baseline.md](notes/circuit-baseline.md) records evidence and timing; no routing-speed improvement is claimed.

## Remaining product gates

| Area | Required evidence |
|---|---|
| Audio | Qualified models, actual DAC full scale and gain, both-channel 300 Ω load tests, clipping/stability/noise/distortion |
| Power and protection | Sequencing and brownout waveforms, current/hold-up/inrush, positive and negative DC faults, relay opening/closing |
| Layout | Converter current loops and local decoupling, fine-pitch pad escape, controlled clock paths, continuous return plane, routed DRC and post-layout checks |
| Mechanics | Actual pot/jack/enclosure, module orientation/support/harness, connector openings, assembly clearances |
| Cost | Exact remaining passive MPNs and a dated delivered BOM/assembly/enclosure/adapter estimate within EUR 250 |
| USB | Vendor firmware/configuration, host format enumeration and playback at every accepted rate, cable and power-order tests |

All 10 product requirements remain unverified. No board has been fabricated, no parts ordered, and no headphone-connected test performed. The next implementation step is model compatibility and nominal/control simulation, then the frozen-input routing benchmark. No further product decision is needed to continue that technical investigation.
