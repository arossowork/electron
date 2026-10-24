# Placement and routing validation

2026-10-24. The final retained PCB has **three missing signal connections and zero other KiCad DRC errors**. All three former ground islands are connected. The bounded follow-up demonstrated no connectivity improvement; it removed two dangling track remnants. This is a partial diagnostic board, not a passed CLI layout stage or a fabrication-ready design. The remaining signals are DAC_L_P, I2S_BCLK, and I2S_LRCLK. No warm-reuse benchmark or manufacturing export has been claimed.

The stable final board is [`build/layout-repair-followup/desktop_dac_hd6xx.kicad_pcb`](../build/layout-repair-followup/desktop_dac_hd6xx.kicad_pcb), with a [native top-side SVG](../build/layout-repair-followup/pcb.svg). The exact final PCB, project/rules, schematic, full DRC, preview, and harness are retained in [`layout-final-candidate.zip`](../verification/layout-final-candidate.zip). The PCB SHA-256 is `e56fd70be2e367c5bba1c3511977f57f885c4f1ccd2f49ce16cf5d330bef66c7`; it contains **1,666 track segments and 238 vias**. The native preview was rendered and visually inspected; crowded silkscreen remains visible and matches the DRC warnings.

## Final bounded checkpoint follow-up

After the repair planner's endpoint-neighbor expansion fix passed focused tests and independent review, the follow-up reconstructed the accepted three-open checkpoint. Source pins, all 238 placements, fixed anchors, outline, layers, and via geometry were rechecked. Initial full DRC reproduced three missing connections and no other errors. The exact engine module hashes and harness hash are in the [follow-up results](../verification/layout-repair-followup-results.json).

The follow-up used the same **300-second repair / 450-second outer cap** and completed normally in **318.370 seconds wall time**. Round 1 changed three errors to three and was rejected. Round 2 expanded to 12 nets but stopped at the 300.0-second repair deadline; its incomplete copper was rejected. The retained board therefore has **no demonstrated connectivity improvement** from neighbor expansion. The independent final full-DRC acceptance check passed relative to the initial checkpoint. The source and input hashes remain unchanged, all 703 assigned pin nets match, all 238 placements remain exactly equal, and hard placement/fixed-rule violations remain zero.

The actual generated second-round input removed copper on DAC_L_P, I2S_BCLK, I2S_LRCLK, 3V3_A, 3V3_D, AUDIO_READY, BOUT_L, DAC_L_N, DAC_R_N, I2S_DATA, I2S_DATA_BUF, and I2S_LRCLK_BUF. VPOS was outside the capped set. This set was measured from the actual generated input versus the checkpoint, not inferred from a later planner call. [Expansion evidence](../verification/layout-repair-followup-expansion.json) and a [compressed input snapshot](../verification/layout-repair-followup-expanded-input.zip) preserve that observation. The interrupted round did not flush its buffered router log, so only completed-call regrades appear in the [router summary](../verification/layout-repair-followup-router-results.json).

Pruning removed two dangling track remnants without adding errors. Final native DRC reports three missing connections and 476 warnings: 199 silk overlaps, 199 silk-over-copper items, 40 Datasheet-field differences, and 38 explicit NC-net naming differences. The [final static inspection](../verification/layout-repair-followup-inspection.json) still reports nine decoupling-distance warnings and the three open signals; its coverage limits are described below. No current-width violation was reported among 13 declared-current nets. Copper-region containment and return-path/SI remain unconfigured and unverified. No CLI layout acceptance, route-cache publication, warm-reuse benchmark, or functional sign-off was run. All routing containers have exited; no diagnostic job remains.

## Current source and hard constraints

Current source SHA-256 is `8d99069b290cd461d95c22192f326537330d9ada447b19f2e4fa2150d384b96f`. The 238 components, 703 assigned pins, 133 electrical nets, 140 × 80 mm outline, four copper layers, and 1.6 mm thickness are unchanged. Fixed connector anchors remain J100 (42,48,0°), J200 (58,76,90°), J201 (123,76,90°), and J300 (124,15,180°). All 238 footprint placements were compared before and after repair and are identical. Both hard placement and fixed-rule checks return zero violations. Source and preserved input hashes are unchanged after the run.

Compared with the electrically validated source `556b89df…63c1` in commit `3c002e1`, the only source changes are explicit padding of the DAC region left edge from 38 to 37.6 mm, padding of the power region top from 2 to 0.7 mm, and two scoped 0.15 mm internal pad-clearance rules for U204 and U336. The [parsed input-equivalence proof](../verification/circuit-revision-equivalence.json) requires every other board, floorplan, component, electrical, model, and fixed-anchor input to match exactly. General clearance remains 0.20 mm. Power-to-DAC and power-to-analog separation remain 8 mm against the 5 mm placement rule; the module reserve and outline remain intact.

Corrected source and schematic checks have zero errors (five source warnings, four ERC warnings). Independent KiCad netlist export matches **703/703 assigned source pins** across all 238 components and 133 nets; see [connectivity results](../verification/connectivity-results.json). Explicit unconnected pin names are excluded from that assigned-pin comparison.

## Corrected real-footprint floorplan

The current source passed the corrected floorplan check in **58.9 seconds engine time / 59.687 seconds wall time**. It reports 238 real components plus the module placeholder, 61% front fill, power/DAC/analog region fill of 45%/42%/41%, and no back-side parts. The 29.6 mm depth stack fits the provisional 32 mm allowance. Quick placement uses seed 2 and 20,000 moves, with **zero placement violations and zero fixed-rule violations**. Estimated routing demand is 7% of capacity, peak 0.7×, and zero overflow cells.

The native floorplan SVG was rendered and visually inspected. These results establish packing feasibility, not routed copper, signal integrity, or final enclosure fit. [Current floorplan evidence](../verification/layout-floorplan.json) points to the exact command, source hash, and [corrected log](../verification/layout-corrected-floorplan.log). The earlier 87.9-second result is retained separately as [historical evidence](../verification/layout-floorplan-historical.json).

## First selective repair and static inspection (checkpoint history)

The [reproducible repair harness](../verification/layout_repair_candidate.py) reconstructed the lowest-unconnected cold candidate using engine APIs, source pin maps, original automatic placements, and current project/rule generation. It verified board outline, layers, via sizes, all assigned pin nets, and all fixed anchors before routing. Thermal vias generated by the source were excluded from imported copper to prevent duplication. Initial full DRC reproduced exactly six missing connections and zero other errors.

The 300-second repair budget was enforced: the run completed in **323.391 seconds wall time**, below the 450-second outer bound. Round 1 added three ground stitching vias and reduced six errors to three in 104.0 seconds. Round 2 made no improvement and was rejected; round 3 stopped at the repair deadline. Full DRC after pruning accepted the retained copper. The final candidate contains **1,668 track segments and 238 vias**, has SHA-256 `b88addf35024448a6d3737c9ca71d15fe2e02e1d31011dbe29f6f61bef720462`, and keeps all 238 placements identical. The full-DRC acceptance guard would restore the initial copper if errors or missing connections increased. See [measured results](../verification/layout-repair-results.json), [process/command](../verification/layout-repair-process.json), and [router regrade summaries](../verification/layout-repair-router-results.json).

Native full DRC still reports 478 warnings: 199 silk overlaps, 199 silk-over-copper items, two dangling tracks, 40 Datasheet-field differences, and 38 explicit NC-net naming differences between the generated schematic and PCB. The metadata/NC warnings are distinct from the exact assigned-pin parity check. The native preview is partial and the crowded silkscreen still needs cleanup.

The existing engine [post-layout checks](../../../src/hwflow/postlayout.py) report the same three open signals and nine decoupling paths above the default 4 mm limit. Locations and all 41 measured paths are retained in the [first inspection](../verification/layout-repair-inspection.json) and unchanged [final inspection](../verification/layout-repair-followup-inspection.json):

| Capacitor → IC | Rail | Copper path | Capacitor location (mm) |
|---|---|---:|---|
| C100 → U100 | 3V3_D | 5.87 mm | 70.95, 63.80 |
| C101 → U100 | 3V3_C | 6.02 mm | 72.95, 49.20 |
| C106 → U101 | 3V3_D | 20.12 mm | 58.8475, 47.2836 |
| C245 → U203 | VPOS | 4.27 mm | 102.85, 60.15 |
| C246 → U204 | 3V3_D | 4.47 mm | 102.35, 57.40 |
| C247 → U205 | 3V3_D | 5.41 mm | 98.80, 57.55 |
| C322 → U311 | VPOS | 6.86 mm | 75.25, 20.35 |
| C215 → U202 | VNEG | 5.68 mm | 90.0709, 72.0649 |
| C348 → U336 | 3V3_D | 4.02 mm | 90.70, 24.90 |

No current-width violation was reported across 13 declared-current nets. Component-region checks pass, but copper-region containment is **not checked** because the regions do not declare net lists. Return-path and signal-integrity screening is **not checked** because no net/component edge rates are declared; the separate two-layer return-path check does not apply to this four-layer board. No new SPICE or IR-drop simulation was run in this inspection. These explicit gaps and the decoupling warnings prevent functional layout sign-off even if a later copper DRC becomes clean.

## Historical cold routing run

The first actual run used `layout --auto --attempts 2 --workers 2 --brief`, a 600-second internal budget, and a 750-second outer timeout in the pinned offline `hwflow-audio:ca10fa8e` image. It ended after **750.401 seconds**, exit 124, with only placement attempt `a1` reached. The engine had not propagated its deadline into the initial router calls; it produced partial candidates but no final selected route or layout verdict. The deadline propagation is now corrected and tested. Source `556b89df…63c1` remained unchanged throughout that cold run.

All 23 generated PCB candidates and project settings are retained in [`layout-cold-candidates.zip`](../verification/layout-cold-candidates.zip). The diagnostic best completed candidate, `desktop_dac_hd6xx_krt2n0.kicad_pcb`, contained 238 footprints, 1,679 segments, and 242 vias. Its SHA-256 is `de6d75a13b382899a3d9f2174a472d69d375cd0b34f45289457206c84eff81b8`. This was a diagnostic selection by missing-connection count, not an engine acceptance decision. Its unfilled-board count of 174 must not be confused with the six missing connections after zone refill.

Independent native DRC found **ten fixed package-clearance errors plus six missing connections**. U336 had six and U204 four adjacent pad-pair gaps of 0.15 mm under the 0.20 mm rule. The missing signal nets were DAC_L_P, I2S_BCLK, and I2S_LRCLK; three other items were ground-zone islands. Historical DRC warnings totalled 605, including 199 library-context warnings from the standalone candidate check. [Cold results](../verification/layout-cold-results.json) and [native DRC](../verification/layout-cold-drc.json) preserve the exact counts and errors. File-write milestones are artifact timestamps, not measured stage durations. The timed-out container exited; no routing job was left running.

## Historical fixed-region discrepancy and pad-rule trial

The initial floorplan reported zero violations in 87.9 seconds, but actual placement reported J100 outside the DAC region by 8.2 mm² and J300 outside power by 13.8 mm². Native geometry confirms J100 courtyard x_min=37.69 mm against the old x_min=38; its body, pads, and drills fit. J300 courtyard y_min=0.8 mm against the old y_min=2; body y_min=1.3, while pads and drills fit. Both footprints fit the board outline. [Measured bounds](../verification/layout-fixed-bounds.json) distinguish courtyard, body, copper, and drill geometry.

The [floorplan reporting implementation](../../../src/hwflow/floorplan.py) now includes the fixed-part violations previously omitted by its quick check. The explicit region padding described above accommodates the real courtyard strokes while preserving all fixed anchors, module reserve, outline, and 8 mm separation. The corrected 58.9-second run supersedes the historical false-clean result.

Before canonical rules were added, a scratch-derived engine project retained the candidate PCB byte-for-byte and allowed 0.15 mm only between two pads within U204, or two pads within U336. KiCad removed exactly the ten intrinsic clearance errors while leaving six missing connections and 605 warnings. [JLCPCB's published capabilities](https://jlcpcb.com/capabilities/pcb-capabilities/) list 0.15 mm clearance for SMD pads on different nets; this is a capability reference, not a supplier selection or manufacturing release. The [official KiCad custom-rule documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#custom_design_rules) describes the pad-type and footprint-membership conditions used.

Seven resolver scope checks show the two intended internal pad pairs resolve to 0.15 mm, while cross-footprint pads, other pads, pad-to-track, pad-to-via, and track-to-track pairs retain 0.20 mm. The pinned router's legacy copper-clearance channels do not bind the footprint condition, so routed copper remains conservative at 0.20 mm; final KiCad DRC applies the pad-only exception. This does not claim every internal router pad checker implements that exception. [Rule-trial evidence](../verification/layout-clearance-rule-results.json) records the exact rules, DRC, resolver checks, and unchanged input-board comparison.
