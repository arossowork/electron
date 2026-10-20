# Router and DRC rules (L1)

`run_krt` (src/hwflow/pcb.py) now writes `<name>.kicad_pro` and `<name>.kicad_dru` (`write_project`) before the first pass and copies them next to every pass input (`<stem>.kicad_pro`, `.kicad_dru`). KiCadRoutingTools reads them from the input board's siblings, so each pass sees the rules `kicad-cli` DRC uses afterwards.

## What the router honours (read from the sibling files)

- Net-class clearance and track width, per net (`.kicad_pro` net classes, `netclass_patterns`). Cross-class clearance is the KiCad pairwise maximum. The global `--clearance 0.15` stays as the default-class value.
- Board floors from `.kicad_pro` `rules`: min clearance, track, via, drill, hole-to-hole, annular ring, and `min_copper_edge_clearance` (0.3 mm). The router routes at least that far from Edge.Cuts, slots included.
- `.kicad_dru` rules it can parse (checked against `py_router/kicad_dru.py`, `design_rules.py`, `net_keepouts.py`): layer-scoped clearance rules (`(layer ..)` or `onLayer` terms only), track-to-track clearance rules scoped to a net class, and `disallow track/via` rules on a named rule area (`A.intersectsArea('name')`, net filter allowed). A clearance rule with a `NetName` condition is skipped ("non-layer scope"); a `creepage` rule has no consumer.
- Rule areas (`(zone ... (keepout (tracks not_allowed) (vias not_allowed)))`, board level and inside footprints, any layers) are read by the router from the board file itself (`extract_keepouts`, `add_rule_area_keepout_obstacles`), so `KO_*` areas need no `disallow` rule. hwflow writes none: a duplicate rule would make KiCad report the same item twice. Checked on the thermostat: all 8 areas are read with their flags; 0 tracks or vias in them after routing.
- Creepage (L11): `isolation_router_rules` (src/hwflow/pcb.py) turns each creepage requirement between a net group and every other net into `A.Type == 'track' && B.Type == 'track' && A.NetClass == 'iso_<name>' && B.NetClass != 'iso_<name>'` with `(constraint clearance (min <creepage>mm))`, plus a net class `iso_<name>` (membership only, no size fields) assigned to the group nets in `.kicad_pro`. Sources: `board.isolation` entries with `creepage_mm`, and `board.drc_rules` creepage rules shaped `A.NetName == 'P' && B.NetName != 'P'` (glob expanded to the net names). The real `creepage` rule stays in the file for KiCad DRC. The router applies it raise-only, member track against non-member track (same-group spacing is unchanged).

Measured on the 2026-10-19 baseline (rebuilt): clearance 19 -> 0, copper_edge_clearance 8 -> 0, creepage 7 -> 0. Remaining: 2 `items_not_allowed` (footprint keep-outs, a placement matter, see below).

## What the router does not honour (left to L2's repair loop)

| Rule | Why | What L2 must do |
|---|---|---|
| Creepage to pads and vias of the other side | the router's pair rule covers track against track only; pads and vias use the net-class clearance map, which cannot be pair-specific (a class value also applies inside the group) | read `creepage` errors from DRC, rip up the nets named, re-route or move the part |
| Creepage rules with another condition shape (`NetClass`, `hasNetclass`, `||` lists, `onLayer`) | `isolation_router_rules` converts only `A.NetName == 'P' && B.NetName != 'P'` and `board.isolation` | same as above; `board.isolation` can say any group |
| Creepage through a slot or along a surface | the router has copper-to-copper distance only; the router rule is a straight track-track gap (a slot that lengthens the path is not credited, so KiCad DRC may report the 6 mm track gap where creepage itself is met) | none needed for the baseline |
| Footprint keep-outs (`footprints not_allowed`, e.g. `KO_STACK`, `KO_ANT` for BT1/U2) | the router places no footprints | placement (L14 checks them) |
| Pad-to-pad distances under a class/isolation rule | fixed by placement, not routing | placer or the agent must move the part |
| Zone fill results (GND islands, thermal starvation) | zones are filled by KiCad after routing | stitching vias or island removal (L2) |
| Anything the router reports as routed but KiCad connects differently (unconnected after refill) | different connectivity model | repair loop on `unconnected` |

## Notes

- Not a router limit but worth knowing: with a very bad placement (1 seed, 3000 iterations) the router can still leave a track 0.025 mm from a foreign pad of a Default-class net. The test therefore checks only the violations of the large-clearance rule.
- The .kicad_pro written by `write_project` is regenerated, so a stale one from an earlier build is never used.
