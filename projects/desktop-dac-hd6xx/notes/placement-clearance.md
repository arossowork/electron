# Final placement rounding must preserve clearance

2026-10-24. This compiler correction is separate from both circuit capture and requirement-evidence semantics.

The unchanged engine reproduced `test_net_class_clearance_keeps_pads_of_other_nets_apart`: final placement left RC.1/RH.2 short of their 2.1 mm margin. Instrumentation localized the problem to `_finish`: the legal pre-snap placement had zero shortfall; rounding movable positions to the final 0.05 mm grid introduced a **0.0137354 mm** shortfall. The following legalizer does not repair that pad-clearance term.

The fix tries each snap and accepts it only if overlap, bounds, keep-out and isolation costs do not increase. A rejected snap restores both coordinates and incremental caches. Fixed parts, margins, seeds and optimization iterations are unchanged. A deterministic regression covers both a fixed and a movable anchor, preserving a valid initial 2.101 mm pad gap and checking that the returned cost matches a fresh calculation. Independent review checked the rollback logic.

The original target failed on the unchanged engine and passed after the fix. Three focused cases passed in 1.43 s. The placer/block/floorplan/corridor subset passed **46 tests, 1 deselected, in 160.26 s**. Those timings were collected under concurrent work and are not a speed comparison. No route or DRC performance improvement is claimed.

Final combined fast suite after all three reviewed compiler fixes: **293 passed, 2 skipped, 14 deselected in 94.37 s** (`/tmp/hwflow-final-with-schematic.log`). This is a regression result, not a routing-performance benchmark.
