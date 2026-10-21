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

## Barrier and channel proofs

`src/hwflow/barrier.py`, `findings(design, libs, parts=None, board=None)`. Both checks are proofs of impossibility, per net.

**Room model.** For every net and copper layer, the region its copper may occupy is the board inset by the 0.3 mm edge clearance, minus panel cutouts, minus the rule areas that forbid tracks (footprint keep-outs such as the antenna, `KO_*` and notch parts), minus the `.kicad_dru` `disallow track/via` rules that name the net (`A.intersectsArea(...) && A.NetClass/NetName == ...`), minus the pads of other nets grown by the clearance of the pair, minus non-plated holes. The clearance of a pair includes the `board.isolation` clearance between a member and a non-member. Each subtracted item is a lower bound of what the rules forbid:
- the smallest clearance any rule gives the pair (a `memberOfFootprint` rule lowers it at that part, an unread clearance rule lowers it everywhere);
- straight clearance only, not creepage, because a slot can meet creepage;
- pads at their copper outline or less;
- before placement, fixed parts only;
- other nets' tracks and pours never;
- rules this module cannot read are left out.

The room is therefore an upper bound. A through via fits where its disc fits on every layer. Through-hole pads join all their layers.

- **barrier**: the net's pads lie in separate components of that room at the minimum track (0.15 mm), on every layer and through every via site. Back to `architecture`, or to `parts` when the part's own keep-out cuts its pad off. The message names the cause, found by leaving out each kind of obstacle in turn.
- **channel**: the pads connect at the minimum track but not at the class or budget width. A track may still neck to the minimum for `routing.NECK_MAX_MM` per continuous stretch, anywhere, as `routing.widen` accepts. A narrow stretch joins two wide areas only when they are at most 3 mm apart, which is a lower bound of its length. The largest width that connects is found by bisection. Back to `circuit` (a narrower net), or move the geometry.

After placement, `fixed` says whether a finding holds with fixed copper alone. `fixed: false` depends on the placement of movable parts.

**Limits.**
- A via site counts as wide on every layer. Under the neck rule, a chain of vias can carry a narrow track across any area where vias fit. So for nets wider than `via_dia_mm`, a channel fires only where no via fits on all layers, for example for mains nets that `mains_outer_only` keeps off vias.
- Floorplan regions enter only before placement, as the area where their pads must lie. Region outlines are no track barrier, because DRC has no such rule.

**Calibration.** The check was run on the data sets below. Self-check: every routed track and via on the baseline (530 segments) and on ht3 attempt 1 (671 segments) lies inside the room the model allows. KiCad DRC confirmed that `mains_outer_only` forbids `AC_*` vias and inner tracks (the composite net class matches).

| Data | Before placement | After placement |
|---|---|---|
| Baseline thermostat (130 × 40, 4 layers) | 43 nets checked, none fires (12 s) | none fires (16 s) |
| home-thermostat-3, attempts 1–8 | 10 nets on fixed parts, none fires | none fires on any attempt (6–10 s each) |
| B variants v74, v74_4L, v88, v88b, vst100, vst158 | none fires (floorplan pads as regions) | v88 with `v88_place.json` (a placement that does not fit this variant): barrier on K1_COIL_A/B (K1 coil pads at x = −1, off the board) and on K2_COIL_A (K2.A1 within 6 mm of K1.14 AC_ROOM1, `fixed: false`). Both are true. |

On the baseline, the two accepted keep-out errors are the courtyards of BT1 and U2 in the `KO_STACK` areas. They are no barrier, because no pad lies in the areas and their nets connect.

On ht3, the unrouted K1_COIL_A/B and SENS_EN are no barrier and no channel. Without vias and without necks, a track of 0.35 mm (K1_COIL_A/B) and 0.31 mm (SENS_EN) still connects all their pads in every attempt, against a class width of 0.25 mm. The tightest spot is the pad escape between the 0.5 mm-pitch pads of U4 and U3.
