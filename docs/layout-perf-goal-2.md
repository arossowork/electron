Second goal for `layout-perf`: close the gaps between hwflow's layout and the commercial AI layout tools (Allegro X AI, Quilter, Flux auto-layout). Today hwflow makes up for a weak first placement with retries (placer seeds, the repair loop), and its router honours creepage and keep-outs only after the fact. The aim: a better first placement, rules met while placing and routing, and the post-layout checks feeding back into the loop. Prove it with numbers on a fixed benchmark set.

## Order

1. Finish the current goal first: commit L3 (route cache) and L5 (parallel `--auto`, early stop), then pass the regression gate in `docs/layout-perf-goal.md`.
2. Then do L8 to L14 below.
3. Run the home-thermostat-3 UI run once, after L8 to L14, so it measures everything together. If L8 to L14 take longer than a day, ask the user whether to run the UI run first.

The same rules as the first goal apply: the session is the orchestrator; Sonnet subagents write the engine code; the orchestrator reviews each diff, runs the tests and commits; one commit per item, naming the item; subagents run in parallel only when their files are disjoint; never touch `main` or the other worktrees.

## L8 first: the benchmark (every later item is measured on it)

Write `scripts/layout_bench.py`. It runs `build --until layout --auto` on a fixed set: every design in `examples/`, the eval fixtures that reach layout, a scratchpad copy of the 2026-10-19 thermostat baseline, and one synthetic board of about 150 parts, 2 layers, with a mix of passives, SOICs and QFNs. It runs each design 3 times with fixed seeds.

For each design it records:
- placement HPWL;
- unrouted nets and DRC errors before repair;
- repair rounds;
- DRC errors after repair, by type;
- the number of `--auto` attempts used;
- vias and total track length;
- post-layout warnings by code;
- wall time per phase (placer, router, repair, DRC).

It writes `build/bench/<date>.json` and prints a table. Commit the first run as `docs/layout-bench-baseline.json`. Every later item reports before and after numbers from this script in `docs/layout-perf-result.md`. The headline numbers are the DRC-clean rate before repair and the mean attempts per design.

## Work items

1. **L9: routability in the placer.**
   - Add a congestion term to `Placer.cost`, based on RUDY (rectangular uniform wire density). Each net's wire length is spread over its bounding box on a grid of about 1 mm cells. A cell's capacity comes from the layer count, the track pitch of the default class and the keep-outs. The penalty is the overflow above capacity, squared.
   - Make the cost incremental: a move recomputes only the nets and pairs of the parts that moved. Use a spatial grid for the overlap pairs instead of the O(n²) loop.
   - Report iterations per second before and after. Use the speed-up for more iterations or for a short refine pass on the best seed.
   - Target: the DRC-clean rate before repair goes up, and mean attempts go down, on the benchmark. The total `--auto` wall time on the thermostat must not get worse.
   - Test: a fixture where the wirelength-only optimum blocks a channel, and the new cost leaves it open.
2. **L10: keep-outs and isolation as placement constraints.**
   - The placer reads rule areas from the board (`footprints not_allowed`, the `KO_*` zones) and from `board.isolation`. A courtyard inside a forbidden area, or a pad closer than creepage plus margin to a pad of another isolation group, is a hard penalty weighted like overlap.
   - Test: on the thermostat baseline copy, the placer never puts a footprint in `KO_STACK`.
   - Do not move the fixed BT1 in the baseline. Its keep-out conflict is the user's decision, see L14.
3. **L11: creepage in the router.**
   - Creepage is at least the clearance, so give the router a cross-group clearance equal to the creepage value: between the net groups of different `board.isolation` sides, in the `.kicad_dru` that KiCadRoutingTools parses (or as a net-class pair if it reads only those).
   - Express the router-ignored keep-out areas as net-filtered `disallow` rules where possible.
   - Update `docs/layout-perf-router-rules.md` (which rules are honoured while routing, which are left to repair).
   - Measurement: creepage and `items_not_allowed` errors before repair on the benchmark, before and after.
4. **L12: post-layout checks feed the placer.**
   - Add placer cost terms for what `postlayout.py` warns about after the fact:
     - decap loop length (extend the `decouple` term to use the ground return pad, not only the supply pad);
     - separation between aggressor nets (`class: clock` or `switching`) and `sensitive` nets;
     - thermal separation between parts that dissipate (from the derating table) and temperature-sensing parts.
   - Add a distance between an antenna or RF module and the relays and mains copper, if `design.yaml` can already express it.
   - In `--auto`, rank attempts that have equal DRC results by the number of post-layout warnings.
   - Test: one fixture per term.
   - Measurement: post-layout warnings by code on the benchmark, before and after.
   - If a term needs a new `design.yaml` field, write the proposed field names in a design note and ask the user before implementing it. Field names are the user's decision, as in backlog 52/53/59.
5. **L13: scale.**
   - Run the 150-part synthetic board through `--auto`. Report the wall time per phase and where it grows faster than linearly.
   - Fix the worst cost if it is in hwflow: the placer, the repair planning, the board writing, the extraction.
   - If it is in KiCadRoutingTools or `kicad-cli`, record it in `docs/layout-perf-result.md` with the profile and do not patch the tools.
   - Target: the 150-part board finishes `--auto` in under 10 min with 0 unrouted.
6. **L14: DRC independent of `fp-lib-table`.**
   - Find out why KiCad 10.0.7 reports `items_not_allowed` for BT1 and U2 without `fp-lib-table` and not with it. Make hwflow's DRC report keep-out violations the same way in both cases: a second check without the table, or hwflow's own check of courtyards against the rule areas.
   - Test: the BT1-over-`KO_STACK` case is reported.
   - This changes the baseline's verdict. Implement it behind a flag, report what it finds on the benchmark, and ask the user before it becomes the default.

## Stop and ask

- Any change that alters a product result: layers, board size, BOM cost, parts on the bottom side (bottom-side placement raises assembly cost; if added, it is opt-in and off by default), or the keep-out semantics of L14.
- New `design.yaml` fields (field names first).
- New installs, or paid external routers (Quilter, DeepPCB, Flux). Do not add these. If the benchmark shows hwflow's router failing on the synthetic board, write down where it fails, and the user decides.
- A merge into `main` or `backlog-impl`.

## Done when

- L8 to L14 are committed, each with a test and a benchmark before/after row in `docs/layout-perf-result.md`, or rejected there with the measured reason.
- The regression gate passes again on the thermostat baseline: PASS, 21/21 requirements, SPICE 163/163, 0 DRC errors, 0 unrouted, and identical boards across two runs.
- Targets, not promises:
  - the DRC-clean rate before repair is at least twice the L8 baseline;
  - mean `--auto` attempts are 2 or fewer;
  - post-layout warnings on the thermostat are down;
  - the 150-part board routes in under 10 min.
