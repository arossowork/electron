stage: 8-signoff
status: done
iteration: final (2026-10-20)
sign-off gates:
- `hwflow build --reuse --mc 100`: Verdict PASS. check 0, ERC 0 errors (2 intended AC_NC warnings), SPICE 163/163 at 0 and 50 C pre and post layout, MC 100 yield 100 % on every analysis except reverse_tran 99 % (one aborted run, SUMMARY risk 9).
- Layout: 130 x 40 mm panel (two 62 x 40 boards), 4 layers, DRC 0 errors with board.drc_rules (R8 4.0 mm clearance / 6.0 mm creepage AC_* to all), 0 unrouted; fab outputs in build/fab.
- Approximate models (D1, K1, K2, M1-M4, U2, U4, U5, U6, Q1) justified in notes/models.md.
- Reviews: review-1, review-2 closed; final review MUST (relay housing 1.0 mm under front copper) closed by PM round 6 (0.4 mm insulating sheet, stack 28.6 mm).
- `hwflow trace`: 21/21 verified, 0 failing, 0 unverified.
- SUMMARY.md written: requirement table, cost 27.94 USD, size, PM decisions rounds 1-6, first-article risk list (14 items).
engine changes made during this project (src/hwflow, not yet committed):
- postlayout.py: post-layout transients reduce the trace network to pads (exact DC R, no trace C); board.post_tran_lump (default true). Post stage 600 s timeout -> about 18 s.
- pcb.py: board.drc_rules written to <name>.kicad_dru for KiCad DRC.
- docs/design-yaml.md and tests/test_postlayout.py updated.
remaining warning: decap-distance C8 4.3 mm from U1 (limit 4.0), accepted (+3V3 IR drop 0.00 mV).
