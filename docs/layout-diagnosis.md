# Layout diagnosis

A failing layout build ends with a diagnosis (`src/hwflow/diagnose.py`) that answers one question: can more layout attempts fix this, or does the loop go back to an earlier stage? It is deterministic and uses only the boards of the build's attempts and the builds before it.

## What it looks at

1. **Attribution.** Every DRC error and unrouted item goes on the parts it touches: the part of a named pad, or else the parts with a pad within 1.5 mm.
2. **Persistence.** A part is persistent when failures land on it in at least 75 % of the build's attempts (independent placer seeds). Failures that move from part to part come from the seed. Failures that stay on one part do not.
3. **Physics at the persistent parts.**
   - Escape: some pads sit between foreign pads in a row, such as the middle pins of a SOT-563. A track can leave such a pad only up to 2 × (gap to the neighbour's edge − clearance). A net class wider than that needs a neck at every such pad. Two or more such pads on one part, or one pad that the board's minimum track cannot leave, is a package or part choice.
   - Board fill: courtyard area over a board face, above `floorplan.FILL_MAX`.
4. **History.** `build/layout_history.json` records every layout build with a structure key. The key covers the parts, footprints and pin nets, net widths, currents and `layers`, and the board size, cut regions, layers, track and clearance. It does not cover placement anchors, weights, or router and DRC settings. A failing build whose persistent parts overlap those of the previous failing build of the same structure means placement and routing have been tried.

## Proofs before and after layout

`feasibility.py` registers the proof modules (`partescape`, `barrier`, `cutcap`, `corridor`). `hwflow check` runs them without a placement (errors `escape`, `barrier`, `cut`, `corridor`), and the layout diagnosis runs them again at the kept placement and board. A finding that depends on the placement of movable parts (`fixed: false`) counts there only when its parts are persistent. With the library available, the proofs replace the diagnosis's own pad escape estimate (they use the clearance actually in force, such as a fine-pitch DRC rule).

## Routing corridors of constrained nets (placer)

The placer (`placer.py`, `_cor_eval`) keeps a channel open for the constrained nets: members of a `board.isolation` group (or of a `drc_rules` isolation pattern), nets with `layers`, and nets of a floorplan region with `nets` (2 to 16 pads). For each edge of the minimum spanning tree of the net's pads, it measures the free width of the straight channel and of the two L-shaped channels and keeps the best one. The free width is twice the smallest distance from the centre line to foreign copper, minus the spacing that copper needs. Foreign copper means pads that block every layer the net may use (a through-hole pad blocks all layers, an SMD pad only its own layer), conductive keep-outs and rule areas without tracks. The spacing is `between_mm` between two members of a group, the group distance between a member and another net, and the larger net class clearance otherwise. These are router values, without the placer margins. The channel starts at each pad's edge. A channel narrower than the track adds `weights.corridor` (default 30) × the shortfall in mm to the cost (`placement: {weights: {corridor: ...}}`). The cost is incremental: a net is recomputed only when one of its pads moves, or when a part enters or leaves the spatial-hash cells around it. On home-thermostat-3 this costs about 10 % of the annealing speed.

The straight and L-shaped channels are only a proxy. A line marked `narrow (a neck may be needed)` does not prove that the router will fail, since it may go around. The layout stage prints the narrowest channel of each constrained net and writes it to `status.json` (`layout.corridors`) and `REPORT.md`.

When every pad of a constrained net is on a fixed part, `corridor_blocks` asks the corridor proof of `hwflow check` (`corridor.py`) about the copper of the fixed parts alone. The two checks are one model: the same spacings (clearance, isolation, between_mm, conductive keep-outs), the net's layers, and the neck allowance. A track may neck down to the board minimum for at most 3 mm per stretch (`routing.NECK_MAX_MM`), as the router does, so a corridor that only a neck passes is no block. If the pads stay apart even with necks, no placement can open the channel. The check then adds a placement violation `corridor <net> blocked between fixed parts:` followed by the proof's message, and the layout stage writes the unrouted board and stops with `not routed: ...` instead of starting the router. Moving the fixed parts, or changing the net's `layers`, width or spacing, is a design decision.

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

## Corridor proof (`src/hwflow/corridor.py`, check `corridor`)

**Why a module of its own.** `barrier` models the rules that apply to every net. Some nets carry rules of their own that shrink their room further. `corridor.Model` subclasses `barrier.Model` and adds those rules, so the room model and its upper-bound argument are reused, not copied. It reports only nets that `barrier` does not already cut apart, so the same net is never reported twice. `barrier` itself is unchanged, apart from two hooks: `net_copper` (the layers a net may use) and `neck_len` (the neck length bound).

**Which nets.** Members of a `board.isolation` group with `between_mm`, nets of a floorplan region that declares `nets`, and nets with `layers`.

**The added rules.** Each is a hard rule of the flow: the router enforces it and the post-layout check fails on it.
- `between_mm`: a member keeps this distance from the pads of the group's other members. It is lowered, like every barrier clearance, by any clearance rule that could override it (a `memberOfFootprint` rule, or a rule this module cannot read).
- Conductive isolation: the nets of a region with `isolation_mm` stay outside every `conductive: true` keep-out or cut grown by that distance, on every layer (`floorplan.conductive_needs`). The keep-out is drawn as its inscribed polygon, so the grown area is never too large.
- `nets.<name>.layers`: the net's tracks lie only on those layers. Its pads connect through the union of those layers. A via counts only for a net with two or more layers, wherever its disc fits on those layers. The other layers are left out of the via test, which gives more via sites, never fewer.

The region outline itself is no barrier. Copper outside the region is only a `region-nets` warning.

**The test.** Can the net's pads connect at its track width, with a neck down to the minimum track for up to `NECK_MAX_MM` per stretch? The flow accepts such a neck, even on a current net, where it is a `track-current` warning. The neck length bound is tighter than barrier's, and still a lower bound. It is the distance between where the narrow component touches one wide side and where it touches the other, not the distance between the two sides anywhere on the board. The finding gives:
- `need_mm`: the track width.
- `have_mm`: the widest track that connects with necks (bisection, 0.01 mm).
- `plain_mm`: the widest track that connects without necks.
- `at` and `layer`: the narrowest place of that corridor.
- `blockers` and `refs`: the pads, keep-outs, conductive keep-outs and cuts that bound it there, each with its spacing rule.

The verdict is back to `architecture` when all blockers are fixed, else to `layout`. After placement, `fixed` says whether the finding holds with fixed copper alone. `corridor.margin(...)` lists every constrained net's widest corridor, failing or not.

**Neck needed, as data for the router.** `corridor.neck_needed(...)` returns the constrained nets that connect at their width only with necks: net -> `{need_mm, plain_mm}`. `pcb.krt_route` computes it on the board it routes. Such a net gets a pass of its own, ahead of the other nets of its isolation side. It skips the full-width pass, which cannot route it, and routes at the board track width (when narrower than the net), then at `routing.neck_floor` (0.15 mm, or `min_track_mm` when larger). `routing.widen` then widens it back to its width wherever the clearances allow. The pass is accepted when no narrow stretch is longer than `NECK_MAX_MM`. The same neck-down runs for any net a pass leaves open, signal or power, not only for nets wider than the board track. The net's `layers` and its isolation keep-outs (between_mm included) apply to every neck pass. The layout notes give each necked net's narrow length, total length and longest neck, and say when a neck width left it open.

**Calibration.**
- **home-thermostat-3, before placement** (0.6 s; 1.0 s with `margin`): it fires on AC_ROOM1. The net is 1.0 mm wide, F.Cu only, and runs from K1.14 to J1.2.
  - There are three ways across the row at y ≈ 13.
  - Left of F1.2, the 6 mm band around `notch_l` and K1.13 (`between_mm`) close the way.
  - The slot between F1.2 and F1.1 is 3.08 mm. With 1.5 mm to each side, 0.08 mm is left, below the 0.15 mm minimum.
  - The corridor right of F1.1 is the widest. Its narrowest place is at (16.46, 12.23). It lies between F1.1 (AC_L_IN, 1.5 mm `between_mm`) and K1.A1 (K1_COIL_A, 6 mm isolation). It fits a 0.34 mm track. With 3 mm necks the bound is 0.90 mm. Both are less than 1.0 mm.
  - So the net that layout round 4 left unrouted after a 50-minute build could not be routed by any router. Back to `architecture`. The options are F1 or K1 moved by at least 0.66 mm, B.Cu allowed for AC_ROOM1, or a narrower net.
- The other AC nets pass. Their widest track without necks is AC_L 1.79 mm (need 1.4), AC_L_IN 5.9 mm (need 1.4) and AC_ROOM2 6.0 mm (need 1.0).
- examples/sensor_preamp has no constrained nets.
