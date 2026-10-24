stage: 4-circuit
status: blocked

Product: USB-C DAC/headphone amp for HD6XX; external regulated 5 V adapter; EUR 150-250 target.
Branch: design/desktop-dac-headphone-amp; baseline engine ca10fa8e0b9d67745787734b979e784f4422a3ec.
Working geometry: 140 x 80 mm, 4-layer PCB; provisional 160 x 100 x 40 mm enclosure.
Format: stereo PCM 16/24/32-bit through 384 kHz; actual module configuration/playback unverified.
Source: design.yaml; 235 real components, 128 nets, 4 fixed connectors, 231 movable parts.
Circuit: Combo768 interface, ES9039Q2M, 2x OPA1612, OPA1622, supply sequence and DC/relay protection.
Source check: PASS, 0 errors, 5 warnings. KiCad ERC: PASS, 0 errors, 4 warnings.
Connectivity: 683/683 assigned source pins match exported KiCad netlist.
Floorplan: PASS with real footprints; 60% fill, 29.6 mm depth, zero quick-placement violations.
Blocker: TI-derived OPA1622 model fails OP convergence in single-channel isolation.
Alternate official TINA model fails ngspice parsing; no valid amplifier measurement.
Next: qualify model compatibility; simulate audio and startup/shutdown/DC faults, then Monte Carlo.
Routing/DRC/post-layout/fabrication: NOT CHECKED; do not skip the circuit gate.
Open: exact remaining passive/panel/enclosure parts, delivered cost, firmware configuration, bench tests.
Trace: 0/10 verified; no product evidence claimed. No orders or fabrication.
Compiler: explicit evidence, clearance-preserving grid snapping, automatic schematic sheet size.
Tests: final fast tier 293 passed, 2 skipped, 14 deselected.
Evidence: SUMMARY.md and notes/circuit-baseline.md; compiler changes committed separately.
Custom MCU firmware: not applicable; vendor USB firmware qualification remains required.
