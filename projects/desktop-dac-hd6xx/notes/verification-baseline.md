# Explicit requirement evidence: measured compiler correction

2026-10-24. Engine baseline `ca10fa8e0b9d67745787734b979e784f4422a3ec`, pinned container `hwflow-audio:ca10fa8e`, Python 3.11.15. This is a verification-semantics comparison, not a routing-speed result.

## Reproduction and correction

On the unchanged engine, an inspection/test evidence string such as `NOT CHECKED: awaiting prototype` counted as verified. A missing evidence file emitted a separate diagnostic but still counted as covered. A passing simulation tagged to a bench-test requirement also completed it. Failing measures were included in the report's count labelled “verified”.

The regression file `tests/test_trace.py` ran before the implementation change: **25 failed, 5 passed in 0.43 s**. These failures reproduced the false-positive cases rather than a missing import or tool failure. After the change, the same 30 cases plus the five existing minimum-width tests gave **35 passed in 0.69 s**.

Manual results now require an explicit `status`, a nonempty project-local artifact and the current semantic design hash. Missing, planned, invalid or stale evidence remains unverified with a reason. Simulation and routed-board checks must match the requested verification method; passing simulation alone cannot complete a bench test. Failing linked measurements still fail the requirement. The documentation explains that a manual record is an author's attestation, not independent validation of a written result, and that design hashing does not cover every external model/tool dependency.

The DAC project's product evidence remains empty: none of these compiler tests proves the physical product requirements.

## Broader regression run

`python -m pytest -q -m 'not slow'`: **286 passed, 1 failed, 2 skipped, 14 deselected**, 104.14 s. The failure was `tests/test_placer.py::test_net_class_clearance_keeps_pads_of_other_nets_apart`, reporting pad-pair `RC.1/RH.2` 0.01 mm short of its 2.1 mm placement margin.

The identical placement test then ran with `PYTHONPATH=/app/src`, selecting the unchanged image engine rather than the modified worktree: **the same failure reproduced in 2.03 s**. The evidence change does not touch placement. This pre-existing failure was isolated and corrected separately in the placer; see `placement-clearance.md`. Neither the clearance nor the test acceptance was relaxed. The slow routing tier was not run for these scoped changes.

Local run logs: `/tmp/hwflow-trace-red.log`, `/tmp/hwflow-trace-green.log`, `/tmp/hwflow-trace-suite.log`, `/tmp/hwflow-placer-baseline-failure.log`. The regression tests and this note retain the portable reproduction; temporary logs are not required to use the branch.

## Independent review and requirement target freshness

Review found that the existing build-cache hash excludes requirement text, where a numerical target may be written. A new regression first failed when a passing manual record remained current after its target text changed. Manual evidence now uses a separate SHA256-based semantic hash including the whole requirements document except evidence itself. Build caching keeps its existing hash. The focused trace/minimum-width/core run after this correction passed **81 tests in 141.62 s**. Logs: `/tmp/hwflow-trace-text-red.log` and `/tmp/hwflow-trace-review-green.log`.

## Final combined compiler regression

After the independent reviews and both separate compiler fixes, the standard fast tier completed: **290 passed, 2 skipped, 14 deselected in 391.83 s**. Log: `/tmp/hwflow-final-fast.log`. This run overlapped circuit/geometry work and initially stale simulation processes; its time is not a performance comparison. The slow routing tier was not run because these patches change evidence handling and final snap validity rather than routing.

Final combined fast suite after all three reviewed compiler fixes: **293 passed, 2 skipped, 14 deselected in 94.37 s** (`/tmp/hwflow-final-with-schematic.log`). This is a regression result, not a routing-performance benchmark.
