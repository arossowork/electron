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
