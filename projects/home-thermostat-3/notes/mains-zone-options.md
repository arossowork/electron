# J1 / 230 V zone options (layout round 2 blocked, 2026-10-22)

Board mm, origin top left, y down, face B = box side. Positions from `build/auto/a1/placement.json` and the footprints in
`lib/footprints/thermo.pretty` (pad positions rotated by hand: KiCad rot 180 maps (x, y) -> (-x, -y), rot 270 maps (x, y) -> (-y, x)).

## Fixed geometry

| Item | Value |
|---|---|
| notch_l | centre (2.25, 23), r 4. 230 V copper edge >= 10.0 from the centre (R10, 6 mm). Courtyard keep-out r 4.3 |
| K1 (12.5, 5.6, 180) | courtyard x 0.25-24.75, y 0.35-10.85. Pad 13 (2.0, 9.35), pad 14 (5.5, 1.85), coil A1 (23.0, 9.35), A2 (23.0, 1.85), coil pads d 2.0 (SELV) |
| K2 (12.5, 40.4, 180) | courtyard y 35.15-45.65. Pad 13 (2.0, 44.15), pad 14 (5.5, 36.65), coil A2 (23.0, 36.65), A1 (23.0, 44.15) |
| F1 (14.0, 13.25, 0) | pads (14.0, 13.25) and (8.92, 13.26), d 2.0. Courtyard x 6.96-15.96, y 11.0-15.5 |
| Mains region | AC pad copper at x <= 20. A 3.0 mm J1 pad needs its centre >= 11.5 from the notch centre and >= 8.5 from each coil pad centre |

## Why the present J1 cannot fit

The WJ2EDGRC (C8377) is a right-angle header. Its mating face points at the left edge. The plug spans x -0.9..7.9 and y 15.9..31.1, and the wire allowance spans x -10.9..-0.9. So the plug sits on top of the frame screw boss (notch x 0-6.25, y 19-27). This is a mechanical collision, so it holds even if R10 were relaxed. The box wall is only (96 - 88) / 2 = 4 mm beyond the board end, so the 10 mm wire bend has no room either. The other orientations also fail:
- Mating face +-y: the courtyard needs 31.6 mm along y, and only 24.3 mm lies between the relay courtyards.
- Above or below the notch: the 17.6 mm courtyard does not fit the 7.8 mm bands.
- Mating face +x: the plug and wires reach x 20-52 over the SELV face B (C1, C2, the guard).
- Rotating the relays: they become 2 x 24.5 = 49 mm along y, more than 46.

## Options

### A. Fixed rising-clamp screw terminal, 5.08 mm, 3 poles, horizontal entry (recommended)
Part: KF128-5.08-3P (Kaifeng, LCSC C474953, about 0.30 USD; search listing, drawing not read). Body 15.24 x about 7.6-8.1 x about 10-11 mm high (typical class values, check the drawing).
Placement: rot 90/270 with the pins at x 17.0, y 18.30 / 23.38 / 28.46 and the wire entry toward -x. Courtyard about x 12.8-21.2, y 15.55-31.25.
- Clear of F1 (y 15.5), K2 (35.15) and the notch r 4.3 (needs x > 6.55).
- Notch: 14.75 - 1.5 - 4 = 9.25 mm, need 6.0.
- Top pin to K1.A1: 10.77 - 2.5 = 8.27 mm. Bottom pin to K2.A2: 10.15 - 2.5 = 7.65 mm. Need 6.0.
- Pad edge at x 18.5, within the x <= 20 limit.

Pin order, top to bottom: L_IN (next to F1.1), ROOM1, ROOM2. This order routes planar on B.Cu:
- L_IN goes to F1.1.
- ROOM1 runs up the right side of J1 (track centre x 19.5) and passes F1.1 in the corridor x 15.7-16.6. That corridor is bounded by the K1.A1 6 mm keep-out.
- AC_L runs from F1.2 down the gap between the notch and J1 (centre x >= 12.75 at y 23) to K2.13.
- ROOM2 goes to K2.14.

Weak spot: ROOM1 passes F1.1 (L_IN) at 0.2-1.1 mm. Functional L-to-room insulation sees 230 V across an open contact. Allowing F.Cu for the AC nets inside x <= 20 removes this. Face A is under the MP1 sheet there (R26). This is a design.yaml layer change and needs no product decision.

R2: about 11 mm, under 15.8. R30: -0.03 USD (no plug).
R12 risk: IP2X is not stated. notes/parts.md rates the open KF301 class as not finger-safe. J1 faces the box and cannot be reached with the plate off (R26), so the argument is location, not the part. Variant A2 is a Weidmuller LSF 180 deg push-in terminal (top entry, IP20 class). It is not at LCSC, costs about 2 USD (BOM about 22.7, R30 about 38.76 against 38.79), and its height is unverified.

### B. Vertical pluggable header WJ2EDGVC-5.08-3P (LCSC C8430) + the same plug (C71371)
The wires leave perpendicular into the box, so no wire allowance is needed on the board. The footprint is about 17.1 x 8, in the same spot and with the same pin order as A.
- Fit: yes.
- R2: the mated height is about 19-21 mm (header plus 17.6 mm plug, estimate). The stack becomes about 33-35 mm, more than 30. FAIL unless the PM relaxes R2.
- R30: about +0.

### C. Flying leads plus in-box lever connectors
Three 1.5 mm2 H07V-K leads, 15 cm, colour-coded (brown L, black R1, grey R2), soldered in d 1.8 holes at x 17. The kit includes 3 Wago 221 connectors (IP2X, 450 V).
- Fit: trivial, 3 pads.
- R12: met by the Wago connectors.
- R30: +0.8 to +1.1 USD, so about 37.9-38.2 against 38.79.
- Risks: strain relief and anchorage (IEC 60730 cord anchorage) and hand soldering.
- Installer: wires the leads like any 503 device, with no screwdriver.

### D. Two connectors / wider mains zone / relocation (rejected, numbers)
- A 2-pole right-angle plug needs 12.6 mm along y, against the 7.8 mm band.
- Pushing the right-angle J1 far enough right that plug and wires clear the notch puts the pins at x about 36. The mains zone would grow by 18 mm, which leaves 62 - 18 - 6 = 38 mm of SELV for the 48.5 mm cell row. Does not fit.
- Top and bottom edges: occupied by K1 and K2.

## J1 pin order

Free to change. R12 asks only for "3 poles: L in, room 1 out, room 2 out". Architecture section 7 lists 1 = L, 2 = L1, 3 = L2, but no requirement asks for a label or a fixed order. With a new order, update architecture section 7 and put L / R1 / R2 in the face B silkscreen next to the pins, plus SUMMARY.md for the installer.

## Decision (PM 2026-10-22, conflicts.md D8-D10)
Option A was chosen. The drawing that was then read (Kefa KF128-5.08 rev A) gives a body of 15.24 (+0.60 nubs) x 10.7 x 14.1 mm, larger than the 7.6-8.1 x 10-11 mm assumed above. The pins moved 0.60 mm down, to y 18.90 / 23.98 / 29.06, so that the nubs at the pin-1 end clear F1. Fit numbers (pad edges, d 3.0): notch circle 9.28 mm, K1.A1 8.78 mm, K2.A2 7.18 mm, pad copper x <= 18.5. Courtyard x 6.80-22.65, y 15.51-31.85. It clears the F1 courtyard (15.50) by 0.01 mm and the notch keep-out (x 6.55) by 0.25 mm. AC nets may use F.Cu at x <= 20 (D9).

## Installer note (D10), for SUMMARY.md and the installer sheet
- J1 is a screw terminal on the back of the board (box side), with the wire entries facing the left screw post. Strip 6-7 mm. Use solid or stranded wire of 0.5-2.5 mm2 and tighten the M2.5 screws to at most 0.4 N.m (Kefa drawing).
- The terminals, top to bottom as printed next to them, are L (incoming live from the heating breaker), R1 (room 1 zone valve or pump live), R2 (room 2 live). Neutrals are joined in the box, not on the board.
- Switch off the heating breaker before wiring. With the plate fitted, the terminal is not reachable (IP2X by location, R12).
