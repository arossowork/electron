# Footprints drawn from manufacturer drawings (review 2 follow-up, 2026-10-22)

Footprint files: `lib/footprints/thermo.pretty/`. Generator (not in the project): the files were written once from the numbers below; each `descr` repeats the source.
No `hwflow build` was run for this change. design.yaml and requirements.yaml are unchanged.

## K1, K2: Panasonic ADW1103HLW (DW, 1 Form A, 1-coil latching, low profile HL)

Source: Panasonic DW relays catalog ASCTB8E 202403, https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8811/mech_eng_dw.pdf,
page 7 (printed "-6-"), section "Inrush type (low profile)". Cross-check: Panasonic Industry Europe datasheet "DW relay Low Profile High Inrush Type" (June 2024),
https://industry.panasonic.eu/storage/custom-upload/Components/Relays/Power/ds_dw_yl_relay_en.pdf, pages 7-8 (same pattern and body).

| Item | Value | Source (ASCTB8E p.7, low profile) |
|---|---|---|
| Hole pattern (bottom view) | pin 1 / pin 3 on one row 17.5 + 3.5 = 21.0 mm apart; pin 6 / pin 5 on the other row 17.5 mm apart; rows 7.5 mm apart | "Recommended PC board pattern (BOTTOM VIEW)", tol +-0.1 |
| Hole | 4 x d1.2 (1-coil) | same figure, "4 or 5-1.2dia." |
| Body | 24 x 10 mm, height 15.8 +0.2/-0.3 mm | "External dimensions", general tol +-0.3 |
| Pin 1 to body end | 1.5 mm (so pins at +-10.5 from the body centre) | "External dimensions" (1.5 / 17.5 / 3.5) |
| Pin section | 0.8 mm wide, 0.2 / 0.27 mm thick, 3.2 mm long | "External dimensions" |
| Schematic (bottom view, reset) | 1 = coil -, 6 = coil +, 3 and 5 = Form A contact | "Schematic (BOTTOM VIEW) 1 coil latching (Reset)" |

Pin mapping (same 4-pin set as the placeholder, pad names kept): pad 13 = pin 3, pad 14 = pin 5 (contacts; Form A is symmetric),
pad A1 = pin 1 (coil -, at reset), pad A2 = pin 6 (coil +, at reset). The coil pins stay one coil (A1/A2), so the H-bridge nets are unchanged.

Footprint coordinates (front file = top view = bottom-view pattern mirrored in x; `_back` = front mirrored in x, as lib/mkback.py does):

| Pad | front (x, y) | back (x, y) | pad d / drill |
|---|---|---|---|
| 13 | (-10.5, -3.75) | (10.5, -3.75) | 2.5 / 1.2 |
| 14 | (-7.0, 3.75) | (7.0, 3.75) | 2.5 / 1.2 |
| A1 | (10.5, -3.75) | (-10.5, -3.75) | 2.0 / 1.2 |
| A2 | (10.5, 3.75) | (-10.5, 3.75) | 2.0 / 1.2 |

Pad diameters are a design choice (annular ring 0.65 / 0.4 mm), not from the drawing. Courtyard 24.5 x 10.5 (body + 0.25). Silk is cut at pads 13 and 14.
Origin = body centre, as before: origin shift 0 mm. The pads moved: contacts from (-9.75, +-3.75) to (-10.5, -3.75) / (-7.0, 3.75); coil from (9.75, +-2.5) to (10.5, +-3.75).

Board consequence at the current placements (back, rot 180): K1 pads 13 (2.0, 9.35), 14 (5.5, 1.85), A1 (23.0, 9.35), A2 (23.0, 1.85);
K2 pads 13 (2.0, 44.15), 14 (5.5, 36.65), A1 (23.0, 44.15), A2 (23.0, 36.65). Pad 13 copper is now 0.75 mm from the left edge (was 1.25),
pad 14 of K1 0.60 mm from the top edge, pad 13 of K2 0.60 mm from the bottom edge (y 46). Coil pads moved 0.75 mm toward the SELV side (x 23.0, copper to x 24.0).

## BT1/BT2: Myoung BH-AAA-B5AA001 (LCSC C964881) - the part is a TWO-cell holder

Source: Myoung drawing MY-CP-0144, rev A/0, 2018-09-09, title "电池盒BH-AAA-01 Au", part no. BH-AAA-B5AA001 (the LCSC datasheet,
https://datasheet.lcsc.com/datasheet/pdf/696624b5739b631a3f7e858d4fd8e584.pdf, sheet 1 of 1). The Myoung drawing was reachable; EasyEDA data was not used.

| Item | Value | Source (MY-CP-0144) |
|---|---|---|
| Cells | 2 x AAA side by side, rows 11.00 mm apart, "SMT 180 deg TYPE" | top view (two "AAA 1.5V" cells), ordering code |
| Body | 62.20 +-0.3 (tab ends) x 22.4 mm, housing 54.5 mm, height 11.3 mm | top view, side view |
| SMT pads | 4 x 7.45 x 6.10 mm at 55.75 mm (x) x 11.00 mm (y) centres | "PCB Layout Diagram, Bottom View" |
| Locating hole | d1.80 at 23.45 mm (x) / 10.45 mm (y) from the centre; peg d1.30 | PCB layout, top view |
| Polarity (top view) | upper cell: - left, + right; lower cell: + left, - right | cell markings in the top view |

The placeholder assumed a one-cell holder (two per board). The real part holds both cells and has four terminals, so the pin set differs from
the design (BT1 and BT2 with 2 pins each). I did not map it into design.yaml. New file `thermo:BH-AAA-B5AA001_2xAAA`
(pads 1 = cell A +, 2 = cell A -, 3 = cell B +, 4 = cell B -; NPTH d1.8 at (-23.45, -10.45); courtyard 63.7 x 22.9 mm). A series string uses
a short link from pad 2 to pad 3 at the left end, VBAT_RAW on pad 1 and GND on pad 4 (both at the right end). The old `BH-AAA-B5AA001.kicad_mod`
is left in place for the build, and its descr now says INVALID PLACEHOLDER.

Fix needed (design decision): one holder part (4 pins, nets VBAT_RAW / BAT_MID / BAT_MID / GND) instead of BT1 + BT2, a 4-pin symbol, and a new floorplan.
The holder is 62.2 mm long and 22.4 mm tall (the placeholders took 50 x 25.6 mm). Centred at the old cell centre (53.3, 12.95), it spans x 21.5-85.2, which
enters the right notch keep-out (centre 85.75, 23, r 4.3). To clear the notch it must sit at x <= ~50 (left end ~19), and that touches the 230 V zone (x <= 20) and K1/K2.
BOM: one holder at 1.28 USD (10 pcs) instead of two at 1.21 each.

## J1 (since 2026-10-22, conflicts.md D8): Cixi Kefa KF128-5.08-3P-AA (LCSC C474953), `thermo:TerminalBlock_KF128-5.08-3P_back`

Source: Cixi Kefa drawing "KF128-5.08", rev A, 2021-03-13, sheet 1/1 (third-angle projection), https://datasheet.lcsc.com/datasheet/pdf/a267f2fbdc005306ff962adad87a9bbc.pdf (LCSC C474953).

| Item | Value | Source (drawing view) |
|---|---|---|
| Holes | d1.40 +0.10/0 at 5.08 +-0.03 | PCB LAYOUT; table "PCB hole diameter 1.4" |
| Hole row to rear body edge | 5.40 | PCB LAYOUT, side view |
| Body depth | 10.70, so the wire-entry face is 5.30 from the hole row | side view |
| Body length | P x 5.08 = 15.24, plus 0.60 interlock nubs at one end | front view, top view |
| Height above board | 14.10 | side view |
| Pin | 0.80 x 0.90, 4.30 long | front and side views |
| Ratings | IEC 250 V / 24 A (OVC III, PD 3), 4 kV impulse; UL 300 V / 10 A; 2.5 mm2, 22-12 AWG, strip 6-7, M2.5 at 0.4 N.m max; PA66 UL94V-0 | table |

The footprint is the back-face variant, drawn as seen through the board. Pin 1 is at the origin and pins 2 and 3 are at x -5.08 and -10.16. Wire entry is toward -y.
- Pads: d3.0 (the 230 V pad size kept), drill 1.5 (within the 1.40-1.50 hole tolerance; the pin diagonal is 1.20).
- Body (B.Fab): x -12.70..2.54, y -5.30..5.40. Nub strip x 2.54..3.14.
- Nub chirality: in the top view the nubs are on the right with the wire face at the bottom. Seen through the board with the wire entry toward board -x, this puts them at board -y, which is the pin-1 end.
- Wire-entry clearance (Dwgs.User): 4.65 mm of straight wire past the entry face (y -5.30..-9.95) over the opening span (pins +-1.8). The bend into the box starts there. The distance is limited by the notch_l keep-out.
- Courtyard (B.CrtYd): x -12.95..3.39, y -10.20..5.65 (body + nubs + wire clearance + 0.25).
- Silkscreen: L / R1 / R2 on B.SilkS on the rear side (local y 6.6, beyond the body) (D10).

At J1 (17.0, 18.90, rot 90), board coordinates are as follows:
- Pads: y 18.90 / 23.98 / 29.06.
- Body: x 11.70..22.40, y 15.16..31.60.
- Courtyard: x 6.80..22.65, y 15.51..31.85.
- Labels: x about 23.6.

Fit numbers (pad edges):
- notch_l circle (2.25, 23, r 4): 9.28 mm (6.0 needed). From the centre: 13.28 mm (>= 10).
- K1.A1: 8.78 mm. K2.A2: 7.18 mm (6.0 needed).
- Pad copper: x <= 18.5.
- F1 courtyard (y <= 15.5): 0.01 mm. F1 body (y <= 15.25) to the J1 nubs: 0.91 mm.
- K2 courtyard (y >= 35.15): 3.3 mm. Notch keep-out circle r 4.3 (x <= 6.55): 0.25 mm.

The option-A pin position y 18.30 was 0.60 mm higher. There, the nubs reach y 15.16. They would overlap the F1 courtyard by 0.59 mm and touch the F1 body nominally (0.09 mm).

Not checked: the exact nub position across the depth (the full depth is drawn as a nub strip, which is conservative) and the screw-head access from face B (the screws face away from the board, toward the box).

## J1 before 2026-10-22 (replaced, kept for history): Kangnex WJ2EDGRC-5.08-03P-14-00A (LCSC C8377), right-angle closed header, + plug WJ2EDGK-5.08-03P-14-00A (LCSC C71371)

Header source: Kangnex customer drawing WJ2EDGRC-5.08-XXP-1Y-00A, rev A, 2024-03-10, sheet 1/1,
https://datasheet.lcsc.com/datasheet/pdf/dce87f2b59e72b104c944c4b7a080558.pdf.
Plug source: Kangnex customer drawing WJ2EDGK-5.08-XXP-1Y-00A, rev A, 2024-03-10, sheet 1/1,
https://datasheet.lcsc.com/datasheet/pdf/6f9bbd0ffb3f425ddacaafb272a2e6de.pdf.

| Item | Value | Source |
|---|---|---|
| Holes | 3 x d1.60 at 5.08 mm | header "PCB LAYOUT" |
| Hole row to rear body edge | 1.70 mm | header PCB LAYOUT |
| Body length | N x 5.08 + 1.90 = 17.14 mm; outer hole to body end 3.49 mm | header front view, PCB LAYOUT |
| Body depth on the PCB | 12.20 mm (mating face 10.50 mm from the hole row) | header side view |
| Body height | 7.80 mm, 8.30 mm at the mating lip | header side view |
| Pin | 1.00 mm square, 4.00 mm below the body | header front view |
| Plug | width N x 5.08 = 15.24 mm; side 15.20 x 17.60 mm, of which 8.80 mm is the mating part | plug side and front views |
| Rating | 300 V UL / 320 V IEC, 10 A UL / 15 A IEC, PA66 UL94V-0 | header table |

Footprint (`TerminalBlock_bornier-3_P5.08mm_back.kicad_mod`, the file name is kept so design.yaml still points at it; the content is now the Kangnex part):
pin 1 at the origin (square pad), pins 2 and 3 at x = -5.08 and -10.16 (back = mirrored, as before), pads d3.0 / drill 1.6 (pad size is a choice);
header body x -13.65..3.49, y -1.70..10.50; mating face toward +y (the same wire side as the old bornier: +y local = toward the left board edge at rot 270).
Origin shift: 0 mm (pin 1 stays at the origin, same pad positions as the generic footprint).

Courtyard (B.CrtYd): x -13.90..3.74, y -1.95..29.55 = header body + mated plug + 10 mm wire-bend allowance + 0.25 mm.
- Plug outside the header: 8.80 mm (17.60 - 8.80 mating part, assuming the mating part goes fully into the shroud; the shroud depth is not dimensioned). If the plug sits less deep, the envelope is longer (up to 17.60 mm).
- Wire-bend allowance: 10 mm past the plug rear (my choice: about 3 x the outer diameter of 1.5-2.5 mm2 single-core wire, 3.0-3.6 mm). Drawn on Dwgs.User, included in the courtyard.
- Mated height: the plug side profile is 15.20 mm across, perpendicular to the board, plus the offset of the shroud floor above the board (not dimensioned). Expect about 16 mm from the back surface: above the 15.8 mm face-B budget of architecture 6.2 (R2). Not verified.

Envelope at J1's current placement (x 18.4, y 28.6, rot 270, back):
- Header body: board x 7.9..20.1, y 14.95..32.09.
- Mated plug: x -0.9..7.9, y 15.86..31.14. Wire allowance: x -10.9..-0.9. Courtyard: x -11.15..20.35, y 14.70..32.34.
- The plug envelope covers the screw-boss notch region x 0-6.25, y 19-27 completely: YES, it enters. It also overhangs the left board edge by 0.9 mm (plug) and 10.9 mm (wires).
- New overlap: the header body (y from 14.95) overlaps F1's courtyard (y to 15.50, x 6.96-15.96) by 0.55 mm.
- The header body clears the notch keep-out circle (r 4.3) by 1.35 mm. The plug does not.

## Not changed

F1 (`Fuse_Littelfuse_395Series_back`), C1/C2 (`CP_Radial_D10.0mm_P5.00mm_back`) and `TerminalBlock_bornier-2_P5.08mm_back` (not used in design.yaml) are untouched.

## Review 2 checks (not footprints, kept here for the product decision)

### CJ2305 body diode (M1, M2), forward insertion into a dead board, ~4.1 A per FET for a few ms
JSCJ CJ2305 datasheet (rev F, Apr 2015), https://datasheet.lcsc.com/datasheet/pdf/0d2be3a8b74744bc825762ee6b25df9d.pdf:
p.1 IS (continuous source-drain diode current) = -0.8 A (Ta 25 C); p.2 IS = -1.4 A (TC 25 C), ISM (pulse diode forward current) = -10 A with
note a "Pulse Width <= 300 us, Duty Cycle <= 2 %"; VSD <= 1.2 V at 3.3 A. No SOA or transient thermal impedance curve.
Verdict: NOT covered by the datasheet (FAIL as a datasheet check). 4.1 A is below the 10 A ISM, but a few ms is 10x the rated 300 us pulse width,
and 4.1 A is 3-5x the continuous IS. Rough estimate: about 4-5 W per FET for 3 ms (12-15 mJ), probably survivable, but no datasheet figure supports it.

### F1 breaking capacity: in-stock options (JLCPCB/LCSC search 2026-10-22)
| Part | LCSC | Size | Breaking capacity | Price @10 | Stock |
|---|---|---|---|---|---|
| Walter 2010T3.15A250V (current) | C354904 | TE5 8.2 x 4.0, P 5.08 | 100 A @ 250 VAC | 0.030 | 279k |
| Hongda 40T 3.15A250V, T, SMD | C48392809 | 10.5 x 3.2 x 3.2 | AC 250 V 1000 A (series table), 500 A @ 250 VDC (LCSC) | 0.157 | 2340 |
| SETsafe SCT520T3.15A250V, T, SMD ceramic | C49208729 | D5.1 x 20 | IEC 5 kA @ 250 VAC (0.4-8 A); UL 10 kA @ 250 VAC | 0.134 | 580 |
| Littelfuse 0215003.15MXP (215, T, ceramic) | C142723 | 5.2 x 20 cartridge (needs clips) | 1500 A @ 250 VAC | 0.189 | 3355 |
No in-stock fuse rated >= 1500 A @ 250 VAC fits 9 x 4.5 mm. The best compact option is Hongda 40T (10.5 mm long, about 1.5 mm more than the gap).
Datasheets: SCT520 https://datasheet.lcsc.com/datasheet/pdf/c399a71412fc2cbf24b5ddba60b5c909.pdf ;
40T https://datasheet.lcsc.com/datasheet/pdf/a8dcc59cc397a7d021bb3aabf4272df2.pdf ; 0215 https://datasheet.lcsc.com/datasheet/pdf/bca05598a6d067b5f2483e60cae9d27f.pdf
Standards (not verified against the standard text): IEC 60335-1 and IEC 60730-1 do not give a number for the breaking capacity. They require
fuses to comply with IEC 60127 and to be used within their ratings, so the test lab compares the rated breaking capacity with the prospective
fault current at the fuse. Low-breaking (35 A / 10 x In) miniature fuses are sold for domestic appliances (for example Schurter MSTU, 35 A @ 250 VAC,
https://eepower.com/new-industry-products/schurter-extends-mstu-and-mxt-microfuse-ranges/). On a 16 A MCB circuit the prospective current at the
terminal can reach the kA range. Confirm with the certification lab.

## BT1/BT2 resolved (2026-10-22): one Myoung clip pair per cell, `thermo:Battery_AAA_Myoung_MY-AAA-20-J_19-J`

Sources: Myoung drawing WD-CP-0277 rev A/0 2023-03-09 (MY-AAA-20-J, + clip, LCSC C6937107,
https://datasheet.lcsc.com/datasheet/pdf/79e836848861264c18b59c7e848332da.pdf) and WD-CP-0276 rev A/0 2023-03-09 (MY-AAA-19-J,
- clip with spring, LCSC C6937104, https://datasheet.lcsc.com/datasheet/pdf/b5953874350f465dec3e0fbc2666dcb4.pdf), sheet 1 of each.

| Item | Value | Source |
|---|---|---|
| Land pattern | 2 lands 9 MIN (along the cell) x 5 MIN, 28 mm between the inner edges | "PCB Layout Diagram, Top View, MY-AAA-19-J(-) & MY-AAA-20-J(+)", both drawings |
| Clip body | 10.00 +-0.5 along the cell, 11.00 +-0.8 across, 10.46 +-0.5 high (19-J: 10.00 +-0.5 high at the spring) | front / side views |
| Base | 5.0 wide strip with a d2.0 hole (no peg), 4.0 wide arm roots | bottom views |
| Material | spring steel 0.25 mm, Ni plated, "3A" | title block |

Footprint: pad 1 (+, 20-J) at (-18.75, 0), pad 2 (-, 19-J) at (18.75, 0), both 9.5 x 5.5 rect (above the 9 x 5 minimum, inner edges 28.0 apart as drawn).
Fab: clip bodies 10 x 11 at x +-13.5..23.5, cell 44.5 x 10.5. Courtyard x +-24.25, y +-6.15 (body + 0.8 tolerance + 0.25). Symbol Device:Battery_Cell pin 1 = +, pin 2 = - (`hwflow lib show`), the same as the pads.

Placement (design.yaml): BT1 (52.5, 19.5) rot 0, BT2 (52.5, 6.4) rot 180. Courtyards x 28.25-76.75: 8.25 mm from the 230 V zone (x <= 20), 4.7 mm from the notch_r keep-out (r 4.3), 14.75 mm from the antenna keep-out. BT1.1 (VBAT_RAW) is at (33.75, 19.5), 7 mm above the guard block, so the R4 path is shorter than with the old upper-row BT1.1. `hwflow floorplan`: PASS with risks (the risks are the same as before).
Cost: 2 x 0.2479 = 0.496 USD per board, against 2 x 1.21 = 2.42 (-1.924 USD). The real 2-cell BH-AAA-B5AA001 would have been one part at 1.28-1.54 USD.
The old `BH-AAA-B5AA001` and `BH-AAA-B5AA001_2xAAA` files are no longer used.
