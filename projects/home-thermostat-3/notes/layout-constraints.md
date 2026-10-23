# Layout constraints for the layout stage (U2 MAX17227A boost)

Source: MAX17227A data sheet 19-100743 Rev 1, PCB Layout Guidelines (p.19-20) and Pin Description; delta review 2026-10-22.
design.yaml carries what hwflow can enforce (`decouples: U2` on C3, C4, C5 and R40; `group: power`); the rest is
listed here and is checked by hand in the post-layout review.

## 1. Output capacitors C4, C5 (2 x 47 uF 1206) and input C3

- C4, C5 directly next to U2 pins 1-2 (OUT): the data sheet calls the output-capacitor placement the most important
  item. Top-plate trace to pins 1-2 and bottom-plate return to pin 4 (GND) as short as possible, on the top layer, no via
  in the return path between the capacitor ground pads and pin 4.
- C3 (IN, 22 uF) next to pins 7-8, its ground to pin 4 / EP on the top layer.
- LX (SW_BOOST, pin 3) to L1 pad 2: minimum copper area. +3V3 routed away from L1 and the LX node.

## 2. RSEL: R40 191 k on pin 6 (BOOST_RSEL)

- R40 within 1-2 mm of U2 pad 6; the BOOST_RSEL trace as short as possible (data sheet: < 2 pF on the pin; it is read
  once at start-up, 240-1320 us, tRSEL).
- No copper plane under R40 or under the pad-6 trace on the adjacent inner layer (In1.Cu): cut a keep-out in the GND/power
  pour under R40 and the trace (a 0.2 mm trace over 0.2 mm of FR-4 to a plane is about 0.1 pF/mm: the cut keeps margin).
- Route BOOST_RSEL away from LX (pin 3, SW_BOOST) and from L1 (no parallel run, no crossing under L1).
- R40 ground pad to pin 4 / EP on the top layer.

## 3. EP and grounds

- Pads 4 (GND) and 5 (AGND) tie to the EP (pad 9) on top copper under the part (data sheet: GND and AGND to EP at one
  point, short traces).
- 2 vias in the EP to the inner GND plane (0.3 mm drill fits the 0.8 x 1.2 mm EP with 0.25 mm annular ring; tent or plug
  them so solder does not wick away from the EP).
- The C4/C5 ground return goes to pin 4 on the top layer, short (see 1); the inner-plane vias are for the rest of
  the circuit and for heat, not for the switching loop.

## 4. Footprint EP check

KiCad Package_DFN_QFN:TDFN-8-1EP_2x2mm_P0.5mm_EP0.8x1.2mm: EP 0.8 x 1.2 mm, 4 paste windows 0.32 x 0.48 mm, pads
0.775 x 0.25 mm at 0.5 mm pitch (`hwflow lib pads`). The Maxim land pattern 90-0065 and outline 21-0168 could not be
downloaded (analog.com and pdfserv.maximintegrated.com redirect without a file, 2026-10-22), so the EP size is checked
only against the KiCad description (which cites 21-0168). Open: compare with 90-0065 before ordering the stencil.

## 5. C22 (third 47 uF, PM D1, 2026-10-22)

- C22 sits above C5 at (52.0, 33.9) rot 0: pad 1 (+3V3) at x 50.52, under C5 pad 1, and pad 2 (GND) at x 53.48. Join pad 1 to the C4/C5 +3V3 pads with a short wide top-layer trace (about 2.4 mm), and take the GND pad straight to the plane with 2 vias.
- C22 pad 1 is 0.95 mm from the C2 THT pad (VBAT_P, (48, 34)). Keep the +3V3 trace on the right of C2.
- The `decap-distance` screen (limit 4 mm) may flag C22 at about 6 mm of copper from U2 pin 1-2. C22 is bulk for the LPM -> HPM step, not the HF loop (C4 holds that), so a warning there is expected.

## 6. Relay pads near the board edge (K1, K2, real ADW1 footprint)

- With the real ADW1103HLW pattern (notes/footprints.md), the contact pads sit 0.60-0.75 mm from the board edges: K1 pad 14 is 0.60 mm from y = 0, K2 pad 13 is 0.60 mm from y = 46, and K1/K2 pad 13 are 0.75 mm from x = 0.
- Rule: copper to board edge >= 0.5 mm for these pads. hwflow writes `min_copper_edge_clearance` 0.3 mm into the KiCad project (= max(board.clearance_mm, 0.3); design.yaml sets no `clearance_mm`, default 0.2, and no `copper_edge_mm`). The KiCad DRC therefore accepts 0.3 mm, and the 0.60 mm pads pass both the DRC and the 0.5 mm rule. The 0.5 mm rule holds at the current placements. Do not move K1/K2 closer to the edges. The GND pour is clipped at 0.3 mm from the edge, so the pour is no issue.
- These are 230 V pads. The board edge faces the plastic 503 box, and the creepage to the screw boss is covered by notch_l (R10, 6 mm). Order the board with routed edges and no V-score on these edges (the V-cut copper clearance was not checked).
- BT1/BT2 clips (2026-10-22): the BT2 clip metal (upper row) is 0.9 mm from the top edge (0.5 mm at the +0.8 mm width tolerance). Its pad copper starts at y 3.65.
