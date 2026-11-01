# progress-layout
- fab rules 0.127/0.15/0.3-0.5 (lead); U1 footprint pads fixed (lead).
- block tps612994_ybh_boost (project blocks/) bound as blocks.boost: U2 L1 C2 C3 R1; block check OK, 0 DRC errors.
- last build: kept attempt 4: 0 router-unrouted, 1 DRC error (GND F.Cu zone island, unconnected_items), all 9 budgets met.
- layout_checks (IF-5, MECH-2, MECH-3) in design.yaml, measured only by post-layout (not run: layout not PASS).
- LTE node lengths routed (build 9): LTE_RF 3.40, LTE_M0 2.17, LTE_M1 1.10, LTE_M2 3.18, LTE_M3 2.12 mm; bare-net budgets removed (not measurable).
