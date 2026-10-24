stage: 7-layout
status: blocked

Product: USB-C DAC/headphone amp for HD6XX; regulated 5 V adapter; EUR 150-250 target.
Branch: design/desktop-dac-headphone-amp; electrical checkpoint 3c002e1.
Geometry: 140 x 80 mm, four layers; provisional 160 x 100 x 40 mm enclosure.
Formats: stereo PCM 16/24/32-bit through 384 kHz; physical playback not checked.
Source: 238 real components, 133 connected nets, 703 assigned pins; 4 fixed / 234 movable.
Source SHA256: 8d99069b290cd461d95c22192f326537330d9ada447b19f2e4fa2150d384b96f.
Layout-only changes: two region edges + two internal-pad clearance rules; electrical equivalence PASS.
Source check: PASS, 0 errors/5 warnings. KiCad ERC: PASS, 0 errors/4 warnings.
Connectivity: PASS, 703/703 source-assigned pins match schematic and retained PCB.
TI models: original libraries, explicit PSpice parsing; 16 focused + 13 chain checks PASS.
Current-source simulation: 14/14 measures PASS; 50 tolerance trials, 100% yield; audit 0 issues.
Power: 17 qualified analytical checks PASS; physical rail waveforms/order NOT CHECKED.
Protection: 24/24 qualified scenarios + passive ngspice PASS; independent review complete.
Protection startup: model midpoint 18 s, selected slow corner 37 s; physical timing NOT CHECKED.
Floorplan: PASS in 58.9 s; 61% fill, 29.6 mm depth; zero placement/fixed-rule violations.
PCB: build/layout-repair-followup/desktop_dac_hd6xx.kicad_pcb; native preview pcb.svg beside it.
Layout gate: 3 open signals (DAC_L_P, I2S_BCLK, I2S_LRCLK); zero other copper DRC errors.
All 238 placements unchanged; ground islands closed; 1666 segments / 238 vias.
Post-layout: 9 decoupling-path warnings, 398 silk warnings, 78 NC/metadata parity warnings.
SI/return-path and copper-region containment NOT CHECKED (edge rates/net envelopes undeclared).
First route hit 750.4 s outer timeout; local repairs ended at their 300 s search limits.
Neighbor-expanded retry did not improve connectivity; retained accepted board, no cache seeded.
This failed layout gate is not a proof the board cannot route; no blocker bypass was used.
Next: refine DAC escape/neighbor routing and decoupling paths; clean silk, then rerun full DRC/inspection.
Engine fixes: 64f32d5 floorplan, 5422f97 bounded routing, 27f00af repair-neighbor expansion.
Final fast tests: 327 passed, 2 skipped, 14 slow deselected; independent reviews clean.
Evidence: notes/layout-validation.md and verification/layout-*.json; compact checkpoint ZIPs retained.
Bench/USB/mechanics/cost/product gates incomplete; 0/10 whole-product requirements verified.
No orders, fabrication, publication, headphone testing, full slow suite or merge; no jobs left running.
