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

## Cut capacity (`src/hwflow/cutcap.py`, check `cut`)

**What it proves.** A cut is a curve across the board: a straight chord from edge to edge, or the boundary of a part's courtyard (or a cluster's) grown by 0.5, 1 or 2 mm. Every net with pads on both sides must cross it on some copper layer. If the copper the crossings need is more than the free length of the cut, no placement of the other parts and no router can route the board.

**The bound.**
- Demand, a lower bound: the sum over crossing nets of (w + c). w is the DRC minimum track width, 0.15 mm, because a neck of up to 3 mm may sit anywhere on a net. c is the smallest clearance two tracks may keep. Clearance rules that name only footprints are ignored, because they apply to pads. Ground nets with a pour are left out, and so are nets with a pad on the cut, which cross through that pad. Each net is counted once.
- Capacity, an upper bound: on each copper layer, the cut inside the copper area (board inset by the 0.3 mm edge clearance), minus pads on that layer, holes, and rule areas that forbid both tracks and vias. Each free interval adds its length + c. An interval shorter than w adds nothing.
- Why a crossing costs w + c: the centre line of the crossing net meets the cut at a point. A disk of diameter w around that point is that net's copper, and the cut runs through it for at least w. Copper of two different nets is at least c apart along the cut.
- Isolation: a second test counts only the nets outside a `board.isolation` group. It also blocks the group's `clearance_mm` band around the group's pads.

**Which cuts.** Before placement, the cuts come from fixed geometry only:
- an x/y grid every 2 mm;
- lines at the extents of the keep-outs and of large fixed parts (courtyard area ≥ 20 mm²);
- the line through the nearest points of each pair of those, and of each one and each board edge;
- the grown courtyards of the fixed parts.

The demand counts only nets whose pads are all on fixed parts or on parts that a floorplan region keeps within one face. After placement, the same chords are tested, plus the grown courtyard of every part and of every cluster, all at that placement. A failing ring around one part goes back to `parts`. A failing chord or cluster goes back to `architecture`.

**Interface.** `cutcap.findings(design, libs, parts=None, board=None)` returns a list of findings. Each finding has `check`, `back_to`, `refs`, `nets`, `need_mm`, `have_mm` and `msg`, plus `cut` and `per_layer`. `cutcap.margin(...)` lists the tightest cuts, failing or not, for calibration. One call takes about 1 s on these boards.

**Calibration.** Nothing fired on any data set. The table gives the largest demand/capacity ratio found. A cut fires above 1.0.

| data | largest need / have | tightest cut |
|---|---|---|
| baseline thermostat (130 × 40, 4 layers), pre-placement and routed board | 0.048 (4.2 / 88.2 mm, 14 nets) | chord x = 115.7 |
| home-thermostat-3, pre-placement | 0.006 | NOTCH_L to BT1 |
| home-thermostat-3, attempts 1–8 and the kept board | 0.035–0.047 (e.g. 8.05 / 170.8 mm, 23 nets) | chord BT1 to U1; on attempt 5 x = 22 (mains band); on attempt 6, around K2 |
| the same boards, rings around U2 / U4 / U5 / M2 | ≤ 0.023 / 0.029 / 0.026 / 0.013 | |
| 74 × 46, 2 layers (B's floorplan, 20 000-move placement) | 0.105 (3.85 / 36.6 mm, 11 nets) | U4 courtyard + 2 mm; chords 0.090 (y = 38, mains band) |
| sensor_preamp (5000-move placement) | 0.025 | U2 courtyard + 0.5 mm |

The bound is too weak to explain the home-thermostat-3 failures. U2, U4 and U5 fail on local geometry: escape between pads, and necks that conflict with neighbours. One crossing per net per cut does not see that. The 74 × 46 two-layer board (95 unrouted) is at about 10 % of a single cut's capacity. Its failure is total wire length against area, which no single cut sees either. The check stays as a cheap guard for gross cases, such as a part walled in by keep-outs or a narrow neck of board, and it gives zero false positives by construction.
