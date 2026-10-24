# Keep the complete generated schematic visible

The 235-component circuit revealed that `sch.build_schematic` placed additional columns beyond the hardcoded A3 page. KiCad's SVG export clipped most of the circuit, even though the off-page connections still participated in ERC. Rendering the exported schematic caught this separate presentation defect after ERC passed.

The compiler now retains A3 for circuits that fit and selects a custom sheet from the generated content extents for larger circuits. It includes component reference/value fields, estimated net-label extents, group titles and power flags, with margins. This changes the page, not the electrical circuit or existing component positions. The current DAC exports on a 1671 × 297 mm sheet; all 235 component references appear in the exported SVG. The full drawing was visually inspected. It is a zoomable review artifact; a future hierarchy/grouping improvement would make large designs more convenient to print and follow.

Three regressions cover a small A3 design, a 160-component wide design, and a tall column of 50 ground flags. They failed on the old page behavior where appropriate and passed after the change. The focused schematic/core run passed 45 tests with 3 deselected in 6.85 s; a subsequent property-extent check passed all 3 new cases in 1.06 s. An independent review found no material defect in the scoped change. Arbitrary symbol-body graphics beyond pin extents remain an existing limitation; no such clipping was found in the DAC export.

Final combined fast suite after all three reviewed compiler fixes: **293 passed, 2 skipped, 14 deselected in 94.37 s** (`/tmp/hwflow-final-with-schematic.log`). This is a regression result, not a routing-performance benchmark.
