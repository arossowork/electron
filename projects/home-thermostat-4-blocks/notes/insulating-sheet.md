# MP1: R26 insulating sheet (drawing note)

Decision: PM D2, review-2 round, 2026-10-22 (conflicts.md); prototype material PM D5 (below). design.yaml ref MP1, footprint `thermo:InsulatingSheet_26x46_FaceA` (outline on Dwgs.User, board coordinates, placed at 0, 0).

## Part

- Material: ITW Formex GK-17, flame-retardant polypropylene, 0.017 in (0.43 mm), UL 94 V-0, continuous use to 115 C (ESPE Manufacturing product page, https://electrical-insulation.espemfg.com/compare/formex-gk-sheets/formex-gk-17-2).
- Supply: 24 x 48 in sheets cut from roll, white (GK-17) or black (GK-17BK). 9-24 sheets: 10.64 USD per sheet + 20 USD small-order charge (ESPE web page, 2026-10-22). Credit-card payments add 5 %.
- Pieces per sheet: 610 / 26 = 23 x 1219 / 46 = 26 = 598. Material per board: 0.018 USD (BOM uses 0.02).
- One-off lot: 9 x 10.64 + 20 = 115.76 USD. This is not in the R30 central estimate.

## Prototypes (PM decision D5, review-2 round, 2026-10-22)

- The 10 prototype sheets are cut by hand from insulating sheet already in stock, to the drawing below. No Formex lot is bought for the prototypes.
- Acceptance for the stock material: thickness >= 0.4 mm (measure with a micrometer, 3 points per sheet), flammability UL 94 V-0 (from the material's data sheet or UL iQ / yellow card file). A material without a V-0 document is not used.
- Record per lot (fill in when cut): material name and maker, thickness measured (mm), UL 94 rating and file number or data-sheet reference, dielectric strength if stated, who cut it and the date. Material used: ________ (to be recorded at assembly).
- Production: the ITW Formex GK-17 order (9-sheet lot, 115.76 USD one-off, or a die-cut supplier quote) stays on the open list.
- BOM: MP1 stays in design.yaml as the production part (Formex GK-17, 0.02 USD material share). R30 is unchanged at 38.79 USD (PM D6).

## Drawing (board coordinates, mm, origin top left, y down, face A = front)

- Outline: rectangle x 0-26, y 0-46, all four sides flush with the board outline (left, top, bottom edges) and the x = 26 line.
- Cut-out: circle r 4.5 centred on the left screw boss (2.25, 23); it meets the left edge at y 19.1 and 26.9. That leaves 0.5 mm around the r 4.0 notch so the screw boss passes.
- Covers on face A: the solder joints of K1, K2 (THT, bodies on face B), J1 and F1, which are the 230 V copper on face A. The relay coil pads (copper x 22.0-24.0) are also under the sheet. They are SELV, and the sheet does them no harm.
- Retention: the battery cover clamps the sheet against face A. No adhesive is assumed. If the cover does not reach x 0-26, the sheet needs an adhesive transfer tape. That is not priced here and not checked for the insulation rating.
- Tolerance: +-0.3 mm die-cut, corners r 0.5.
- Marking (on the sheet, optional): "230 V" warning symbol IEC 60417-6042.

## Open checks

- IEC 60730-1 reinforced solid insulation: 0.4 mm minimum distance through insulation for a single layer (to be confirmed against the standard text). GK-17 is 0.43 mm, which leaves 0.03 mm of margin. Confirm the material and the thickness with the lab, together with the F1 breaking-capacity item (D3).
- The sheet adds 0.43 mm on face A under the battery cover. The cells sit in the SELV region (x >= 28.25), so the sheet does not raise the cells and the 30 mm depth stack (R2) is unchanged.
