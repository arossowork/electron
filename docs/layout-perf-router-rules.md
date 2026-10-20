# Router and DRC rules (L1)

`run_krt` (src/hwflow/pcb.py) now writes `<name>.kicad_pro` and `<name>.kicad_dru` (`write_project`) before the first pass and copies them next to every pass input (`<stem>.kicad_pro`, `.kicad_dru`). KiCadRoutingTools reads them from the input board's siblings, so each pass sees the rules `kicad-cli` DRC uses afterwards.

## What the router honours (read from the sibling files)

- Net-class clearance and track width, per net (`.kicad_pro` net classes, `netclass_patterns`). Cross-class clearance is the KiCad pairwise maximum. The global `--clearance 0.15` stays as the default-class value.
- Board floors from `.kicad_pro` `rules`: min clearance, track, via, drill, hole-to-hole, annular ring, and `min_copper_edge_clearance` (0.3 mm). The router routes at least that far from Edge.Cuts, slots included.
- `.kicad_dru` rules it can parse: layer-scoped clearance and track-width rules, track rules, and `disallow` keep-outs on a net filter.

Measured on the 2026-10-19 baseline (rebuilt): clearance 19 -> 0, copper_edge_clearance 8 -> 0, creepage 7 -> 0. Remaining: 2 `items_not_allowed` (see below).

## What the router does not honour (left to L2's repair loop)

| Rule | Why | What L2 must do |
|---|---|---|
| `creepage` constraints (`board.isolation` `creepage_mm`, `drc_rules` creepage) | creepage is a surface path distance; the router only has copper-to-copper clearance. The baseline happens to end with 0 creepage errors once clearance is honoured. | read `creepage` errors from DRC, rip up the nets named, re-route with a larger per-class clearance or move the part |
| Rule keep-out areas on a footprint/zone not expressed as a net-filtered `disallow` (e.g. `KO_STACK` "front board over rear J3 (3D 4 mm)") | the router does not read zone keep-outs of the board file | 2 `items_not_allowed` remain on the baseline; rip up the track/via in the keep-out and re-route around it (or fix in placement) |
| Pad-to-pad distances under a class/isolation rule | fixed by placement, not routing | placer or the agent must move the part |
| Zone fill results (GND islands, thermal starvation) | zones are filled by KiCad after routing | stitching vias or island removal (L2) |
| Anything the router reports as routed but KiCad connects differently (unconnected after refill) | different connectivity model | repair loop on `unconnected` |

## Notes

- Not a router limit but worth knowing: with a very bad placement (1 seed, 3000 iterations) the router can still leave a track 0.025 mm from a foreign pad of a Default-class net. The test therefore checks only the violations of the large-clearance rule.
- The .kicad_pro written by `write_project` is regenerated, so a stale one from an earlier build is never used.
