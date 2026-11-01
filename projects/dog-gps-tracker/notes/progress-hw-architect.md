
## Stage-5 floorplan rework (copper_len budgets), 3 runs used
- Done: fixed placements FL1 (18.6,17.7), R12 (53.2,11.25), L2/C19/C21/L5 column x 54.75 at y 9.8/7.7/5.6/3.5 rot 90, D3 (27.4,27.5), U1 (45.5,13.5 r90), U8 (25.5,21.0 r180; item gnss_rx at moved from 17.5 so BZ1 fits above), AE1/AE2 = item at. screw_tr + hole_tr moved (53,4) -> (49,3.5).
- Result: gnss_saw_to_rx, lte_pin_to_pi, lte chain pass; only d3_tvs_lead fails (7.59 mm) because SW1 is quick-placed (item at not held in the full run).
- Next: add `placement: {x: 30, y: 31, rot: 0, fixed: true}` to SW1 (= item button at), rerun floorplan.
