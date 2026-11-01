# COST-1 evidence (analysis)

Requirement (should): BOM cost at 1000 units 55 USD or less (excluding enclosure, cells and SIM)

Acceptance: hwflow cost <= 55 USD at qty 1000

## Result

Result: pass

Evidence: `uv run hwflow check` reports a BOM cost of $47.34 per board with 51 passive refs unpriced (C*, R*, L*). Estimate for the unpriced refs: notes give $0.002 (R 0402), $0.005 (C 0402), $0.01 (22.6k 1 %) (parts-ui.md lines 7-9, parts-power.md line 12). Assumption: $0.01 each for about 41 plain C/R = $0.41, and $0.10 each for the 10 RF-match parts L2-L5, C19-C23 (LQW15 / GJM1555 class, price assumed, not in the notes) = $1.0. Total about $1.4, so the BOM is about $48.8. A stricter bound of $0.10 for all 51 gives $52.4. Both are at or below the $55 limit. Cross-check: parts.md unit cost table totals $51.9-53.4, including 3 USD for PCB and passives, margin 1.6-3.1. design.yaml has no cost.extra entry for price (the only `extra` is the depth stack). Scope excludes enclosure, cells and SIM. Open price risks: the GZ215F supercap (10.33 each, Digikey @1000) and the nRF9151 at 13.75-15.4 (nrf9151.md).
