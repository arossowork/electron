# Desktop DAC circuit and compiler verification implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development for independent circuit sections, with root integrating the single design file and a final independent electrical review.

**Goal:** Replace the architecture placeholders with a reviewable USB DAC/headphone circuit, exercise the actual PCB pipeline, and fix reproducible compiler failures encountered along the way.

**Architecture:** A preprogrammed Amanero USB-C receiver drives an ES9039Q2M in hardware mode 8. ESS reference analog filtering feeds a physical volume control, OPA1622 amplification, and normally disconnected output protection; an external 5 V supply powers sequenced DAC rails and bipolar analog rails.

**Tech Stack:** hwflow YAML/Python, KiCad 10.0.7, ngspice 42, KRT 0.23.1, pinned `hwflow-audio:ca10fa8e` container.

**Spec:** `projects/desktop-dac-hd6xx/requirements.yaml`, `architecture.md`, `analog-design.md`, and `compiler-experiments.md`.

## Global constraints

- Board 140 × 80 mm, four layers; initial enclosure allowance 160 × 100 × 40 mm.
- Stereo PCM through 384 kHz; USB-C; HD6XX, nominal 300 ohms.
- External low-voltage supply, no host VBUS backfeed; no purchases.
- Prototype delivered-cost target EUR 150–250; cost is not verified until a complete estimate exists.
- 4 Vrms into 300 ohms is an engineering target. Headphone testing requires independent dummy-load fault/transient review first.
- Pin and pad numbers come from verified KiCad libraries and manufacturer documents; no invented footprints.
- Only mechanical interfaces are fixed. Circuit parts remain movable within named regions.
- PASS, FAILED and NOT CHECKED remain distinct. Fit, simulation, routing, DRC and bench tests establish different facts.
- Keep compiler changes separate from circuit commits; baseline and candidate use identical design inputs and environment.

## Review focus

1. USB and adapter connect/disconnect in any order: no backfeed or audible unsafe startup; test sequencing and default mute.
2. I2S clocks at 384 kHz and DSD/unsupported format: correct ratios or hardware mute; verify receiver firmware contract.
3. Either amplifier output at sustained positive/negative DC: relay remains open or drops out; calculate and simulate threshold/time.
4. Fine-pitch exposed pads and local supplies: correct pad nets, voltage limits and real footprint escape; static check and ERC before routing.
5. Incomplete or stale verification evidence: never reported as successful verification; regression tests for the compiler change.

## Task 1: Capture verified circuit sections

Files: project `fragments/{dac,analog,power}.yaml`, matching `notes/*-circuit.md`, project libraries/models as necessary. These fragments are integration inputs; final `design.yaml` is authoritative.

Interfaces: DAC consumes `3V3_D`, `3V3_C`, `3V3_A`, `DAC_EN`, `PWR_GOOD`; outputs `DAC_L_P/N`, `DAC_R_P/N`, `AUDIO_READY`. Power owns `DAC_EN`/`PWR_GOOD`. Analog consumes those differential signals, `VPOS`, `VNEG`, `AUDIO_READY`; owns `AMP_ENABLE` and relay drive. Shared `GND`. Reference ranges 100/200/300 avoid collisions.

- [x] Verify application circuits, symbols and footprints; record calculations and source URLs.
- [x] Implement each section, including real control/protection circuitry and explicit unsolved limits.
- [x] Run meaningful analog and control simulations where models permit; keep model limitations explicit.
- [x] Integrate sections into `design.yaml`, replace placeholder memberships with real refs, and run `hwflow check` and schematic/ERC.

## Task 2: Correct verification evidence semantics

Files: `src/hwflow/cli.py`, `tests/test_trace.py`, design-format documentation.

Interface: manual evidence is an explicit record with `status: pass|fail|not_checked`, `artifact: <project-relative file>` and `design_hash: <current semantic hash>`. Legacy prose remains readable but unverified. A test/inspection requirement cannot be satisfied by an unrelated simulation alone.

- [x] Reproduce false coverage from text saying NOT CHECKED on unchanged engine.
- [x] Add failing regressions for missing/empty/stale artifacts, explicit failure, current passing evidence, and simulation versus bench verification.
- [x] Implement the smallest change that passes; report why evidence is unverified.
- [x] Run focused tests and relevant broader suite; record unchanged-input baseline/candidate comparison and commit separately.

## Task 3: Review and exercise the board pipeline

Files: project `design.yaml`, `notes/circuit-baseline.md`, `compiler-experiments.md`, `state.md`, `README.md`.

- [x] Independently review electrical connections, supply sequence, protection, footprint mapping and model assumptions; resolve material findings.
- [x] Run real-footprint floorplan and inspect its result. Preserve dimensions and explain any unsatisfied envelope before changing geometry.
- [ ] When circuit gates pass, run a bounded cold layout baseline, then warm reuse and one local-edit comparison on independent copies. Record actual attempts, elapsed time, fixed/movable counts, DRC and unrouted results.
- [x] If a prior gate fails, record the precise failed prerequisite and leave routing NOT CHECKED; never create apparent layout success by skipping electrical issues.
- [x] Commit reviewable results and state remaining fabrication/bench gates without claiming production readiness.

## Execution notes

The user already authorized circuit/PCB implementation and compiler improvements. The floorplan explanation and size discussion are complete; implementation proceeds without another approval round. Work remains in the existing isolated design branch. No publication, purchase or fabrication is part of this plan.

## Checkpoint outcome

The original circuit checkpoint is recorded in `notes/circuit-baseline.md`. The follow-up repaired the TI model compatibility path, passed 14 project measures and 50 tolerance trials, and added source-derived analog-chain, power and protection validation. Final power/protection checks pass within documented assumptions; physical bench qualification remains open. The source now has 238 real components, 133 connected nets and 703 assigned pins, all matching KiCad export. The unchanged board outline has feasible packing and no quick-placement violations. Actual placement and routing produced a retained board; local repair closed three ground islands. A bounded follow-up exercised the corrected neighbor-repair logic but retained three open signals after its 300 s budget. See `notes/layout-validation.md` for the measured result and the nine decoupling-path warnings; a final PCB acceptance verdict remains outstanding.

Compiler fixes have independent review and focused regressions; the final fast suite passed 327 tests (2 skipped, 14 deselected). The original evidence, placement rounding and schematic sizing fixes are followed by explicit PSpice parsing/audit failure handling, reliable YAML query-URL round trips, fixed-part floorplan checks, initial routing deadline/copper retention and neighbor expansion around open connections. No purchase, publication, merge or fabrication occurred.
