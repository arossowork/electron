# Review early: cutting the stage-5 rework loop

Stage 5 (review, rework, re-review) took 1 to 1.5 h of each timed thermostat run. The reviewer itself takes 10 to
13 min a round. The time goes into the serial loop around it: a fix, then 5 or more build and sim loops, then product
questions that only the review raised, then parts rework, circuit rework, and a re-review that finds new musts. Five
changes move those findings earlier. All of them are generic: nothing names the thermostat.

| # | Change | Where |
|---|---|---|
| 1 | Product questions before review: a decisions pass at the end of stages 2 and 3, and a deterministic acceptance diff against the approved requirements | SKILL.md, hw-architect, hw-parts; `hwflow approve --requirements`, check code `acceptance` (hwharness) |
| 2 | Mechanical and safety at architecture: a stage-2 checklist, `mechanical:` / `accessible:` regions that feed the isolation checks, and a fuse check for mains inputs | SKILL.md, hw-architect; `core/floorplan.py`; codes `mains-fuse`, `mechanical` |
| 3 | A parts-mode reviewer that runs in parallel with circuit capture, launched in the same message as hw-circuit, with its findings sent to hw-circuit before its build loop ends | SKILL.md, hw-reviewer |
| 4 | Cheap checks: ERC `footprint_link_issues` is an error (and `footprint-link` catches it statically), the model's source MPN against the BOM MPN, the cost recomputed against the limit and cost.md, and the files a requirement names | `verify/reviewchecks.py`, `pipeline/stages.py` |
| 5 | hw-circuit gets the reviewer's checklist up front and fixes review findings in one batch with one build and sim; a third review only if a must remains | hw-circuit, SKILL.md |

## Checks (`hwflow check`)

| code | severity | fires when |
|---|---|---|
| `acceptance` | error on a must, warn otherwise | a requirement's priority, acceptance, verification method or text numbers changed after `hwflow approve <proj> --requirements`, or a requirement was added or removed, and no product decision records it. Each of these records a decision and gives a warning instead: an inbox question with `"change": "<id>"` or `"reqs"`, a new `PM round N, qM` citation that `answers.md` answers, `change: <id>` in conflicts.md, or a derived limit whose numbers alone changed, re-allocated in a `notes/` file that its source names |
| `mains-fuse` | error | mains nets (a `kind: mains` region, an isolation group with `role: mains` or "mains" in its name) carry no fuse, PTC or breaker, or no line input (`role: mains_input`, else the mains nets on a connector) reaches the rest of the circuit only through one |
| `mechanical` | warn | a design with mains nets has no fixing region, no cover, wall, enclosure or film region, or has regions named like fixings or covers that do not say whether they are touchable |
| `footprint-link` | error | the `lib.pretty` folder that the KiCad table gets lacks a footprint that hwflow finds in another folder with the same nickname. The ERC `footprint_link_issues` is also promoted to an error at the sch stage |
| `model-mpn` | error / warn | `spice.mpn` is not the BOM part (error). Without `spice.mpn`, the model's comments name a near-identical part number (warn) |
| `cost-limit` | error on a must, warn otherwise | the BOM (`price_usd`) plus `cost.extra` is above a cost requirement's limit, or cost.md itself states a total above it. With hwharness, it is a warning while the cost question is open |
| `cost-doc` | error / warn | cost.md states a total that design.yaml does not reproduce. It is an error when the document claims a must limit is met. A table row that prices a part differently from `price_usd` is a warning |
| `req-file` | error on a must, warn otherwise | a file that a requirement's text, acceptance or verification names does not exist |

The design fields are in docs/design-yaml.md (floorplan `mechanical`/`accessible`, `cost`, `spice.mpn`,
`nets.<n>.role: mains_input`, `overcurrent`, isolation `role`/`inputs`, the approval section).

## Replay on the two timed runs

The project state at each review is the last build before that review, restored from the build cache (CAS). The
stage-1 requirements baseline was rebuilt from the session log (the stage-1 Write, then the edits that applied the
round-1 answers). The checks were run on copies in `evals/runs/replay/`, which is gitignored.

| review | findings (must) | caught by the new checks | which |
|---|---|---|---|
| thermo-e2e-1 review 1 | 15 (4) | 5 (4 musts) | footprint-link (U1), mains-fuse (no fuse), acceptance (R12, and R8 one review early), mechanical: no fixing (must #3) and no cover (should #9), as warnings |
| thermo-e2e-1 review 2 (delta) | 10 (2) | 1 (1 must) | acceptance R8 (TX cap without the PM). The other must (fuse at 95 % of In) was already caught by `fuse-derating` |
| thermo-e2e-2 review 1 | 15 (7) | 5 (4 musts) | footprint-link (U1), acceptance (R10, R16), cost-doc (cost.md 22.44 against a design that does not reproduce it, U1 priced 1.09 against 2.74), req-file (energy_budget.md, ntc_error.md), mechanical: no cover (must #5, as a warning) |
| thermo-e2e-2 review 2 (delta) | 9 (3) | 2 (2 musts) | model-mpn (the D7 leakage came from the Diodes 1N5819HW, a warning), mechanical (cover holes and rivets without a touchability statement, a warning). With the convention applied (`mechanical: rivet`, `mechanical: wall`), padiso gives 3 errors: K2.13 0.75 mm and F1.1/F1.2 1.42 mm from the cover wall, which needs 5 mm |
| total | 49 (16) | 13 (11 musts: 7 as check errors, 4 as warnings) | |

The checks also flag a few things that no review recorded. In thermo-e2e-1, R4 and R6 had acceptances that were
rewritten with new conditions, next to a numbers re-allocation in notes/realloc.md. In thermo-e2e-2, cost.md 22.44 is
above the R43 margin of 22.00, a should.

Not caught (process or judgement): the strap pin for flashing, C4's leakage against the sleep budget (the parts-mode
review targets this), the lithium pass-through at exactly the abs-max, the insulation thickness of the cover sheet (a
checklist item), and the model realism points.

Existing designs: no new check error. The new warnings are `mechanical` on home-thermostat-3, -4 and -4-blocks (no
fixing or cover regions). `cost-limit` info on desktop-dac-hd6xx, whose limit is in EUR. `acceptance` info everywhere
(there is no requirements baseline yet). The conductive needs of the existing designs do not change.
