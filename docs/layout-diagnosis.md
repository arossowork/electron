# Layout diagnosis

A failing layout build ends with a diagnosis (`src/hwflow/diagnose.py`) that answers one question: can more layout attempts fix this, or does the loop go back to an earlier stage? It is deterministic and uses only the boards of the build's attempts and the builds before it.

## What it looks at

1. **Attribution.** Every DRC error and unrouted item goes on the parts it touches: the part of a named pad, or else the parts with a pad within 1.5 mm.
2. **Persistence.** A part is persistent when failures land on it in at least 75 % of the build's attempts (independent placer seeds). Failures that move from part to part come from the seed. Failures that stay on one part do not.
3. **Physics at the persistent parts.**
   - Escape: some pads sit between foreign pads in a row, such as the middle pins of a SOT-563. A track can leave such a pad only up to 2 × (gap to the neighbour's edge − clearance). A net class wider than that needs a neck at every such pad. Two or more such pads on one part, or one pad that the board's minimum track cannot leave, is a package or part choice.
   - Board fill: courtyard area over a board face, above `floorplan.FILL_MAX`.
4. **History.** `build/layout_history.json` records every layout build with a structure key. The key covers the parts, footprints and pin nets, net widths and currents, and the board size, layers, track and clearance. It does not cover placement anchors, weights, or router and DRC settings. A failing build whose persistent parts overlap those of the previous failing build of the same structure means placement and routing have been tried.

## Verdict

| Finding | Back to |
|---|---|
| Board fill over the limit | stage 2 (architecture): board size, layers, part count, a product decision |
| Escape-limited fine-pitch part, persistent | stage 3 (parts): a package with the pitch named, or stage 4 (circuit): narrower nets |
| Persistent parts, no physical limit, first time | stage 7 (layout): one placement change around them is worth one build |
| Same parts again in the next build of the same structure | stage 2 (architecture) |
| Failures move between parts | stage 7 (layout): more attempts can help |

Any verdict other than `layout` is `BLOCKED`. hwflow then refuses the next layout build of the same structure. The report says `blocked` and the verdict says `layout blocked, go back to <stage>`, until the parts, nets or board change. `hwflow build --retry-layout` runs it anyway and is recorded in the history. Agents do not use it on their own.

## Calibration

**home-thermostat-3** (88 × 46 mm, 4 layers), 8 attempts of one `--auto` build:
- U2 (TPS61299, SOT-563, 0.5 mm pitch) fails in 7 of 8 attempts and M2 in 6 of 8.
- U2 pads 2 (SW_BOOST, 0.6 mm class) and 5 (+3V3, 0.4 mm class) can be left by a track of 0.26 mm at most.
- Verdict: back to parts. It needs a package with pitch ≥ 0.67 mm, or narrower SW_BOOST and +3V3.
- The run had spent more than a day of layout cycles on this part.

**Baseline thermostat:** one attempt, failing only on its 2 accepted keep-out errors. The verdict is `next: stage 7`, not blocked.

**Crowding:** a courtyard share around each failing part was tried and dropped. The baseline parts, which routed clean, scored as high (1.4–1.56) as those of home-thermostat-3.

## Part escape, before layout (`partescape.py`)

`hwflow check` runs the escape proofs of every part from its library footprint, pin nets and net widths (`feasibility.py` registers them; each finding is an error with code `escape`). They need no placement, so they give the same answer before and after it. Each one is a proof, not a heuristic: the copper a part needs (a lower bound) against the room its own pads leave (an upper bound).

- Free room is computed in configuration space: the centre line of a track of width w must start inside its pad and keep w/2 + clearance from every foreign pad. Same-net pads are no obstacle.
- Every approximation gives more room, never less. Foreign pads are drawn no larger than their copper (rounded corners, inscribed polygons). The start pad is drawn no smaller (its bounding box). The clearance is the smallest in force at the part, including custom DRC rules that lower it. Neighbouring parts, tracks and the board edge are left out.

| Check | Fires when | Back to |
|---|---|---|
| `pad_escape` | Two or more pads (`ESCAPE_PINS`) on one part have nets wider than the board track, sit between foreign pads in a row, and no track of the net's width leaves them, either out of the part or to a via. Each needs a neck. Pads at the board track width are left out. | parts: the pitch named, or circuit: narrower nets |
| `part_escape`, walled pads | Not even a track of the board minimum width (`min_track_mm`, else 0.15 mm) leaves a pad, as with BGA inner balls or a thermal pad with no same-net ring pin. Each such net then needs a via in its pocket. Via centres keep via_dia/2 + clearance from foreign pads and are never inside an SMD pad, unless `thermal_vias` declares that pad. Vias of different nets are at least via_dia + clearance apart, so a region V holds at most area(V grown by half that) / area(disc) of them. The check fires when the nets exceed that count. | parts |
| `part_escape`, perimeter | Every net that leaves the part crosses the convex hull of its pads, and each crossing takes at least w_min + clearance of the hull. The check fires when the nets that must cross on the pad layer need more than the hull perimeter. Nets with a through-hole pad, and as many as the via bound inside the hull allows, are left out. The sum is over the whole perimeter, not per side, because a net may leave around a corner. | parts |

**Calibration** (pre-layout `hwflow check`, and the same call on the routed boards):
- home-thermostat-3 fires `pad_escape` on U2 (SOT-563, 0.5 mm pitch). SW_BOOST is 0.6 mm and +3V3 is 0.4 mm, and at most 0.36 mm leaves pads 2 and 5. That figure uses the 0.15 mm clearance of the design's `fine_pitch` DRC rule at U2. The layout diagnosis used the board's 0.2 mm, which gives 0.26 mm. The fix is a package with pitch ≥ 0.62 mm, or narrower nets. The check fires on the design and on all 8 attempt boards. Nothing else fires. U4 and U5 (WSON-8, 0.5 mm) carry +3V3 0.4 mm only on row-end pads 1 and 8, and their thermal pads leave at the open ends of the package. U1 (ESP32-C3-MINI-1) has room under the module.
- The baseline thermostat (gate-abc) gives 0 findings before layout and 0 on its routed board. The examples, the other thermostat projects, and the floorplan variants also give none, except the variants copied from home-thermostat-3, which carry the same U2.
- Cost: under 0.1 s per design.
