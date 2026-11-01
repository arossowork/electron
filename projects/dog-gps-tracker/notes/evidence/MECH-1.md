# MECH-1 evidence (analysis)

Requirement (must): Device mass with cells 65 g or less

Acceptance: sum of BOM masses + enclosure estimate <= 65 g (2x AA Li-FeS2 = 29 g, buzzer about 2 g)

## Result

Result: pass

Evidence (estimate, 2x AA Li-FeS2 per the requirement): architecture.md section 10 mass budget: cells 29.0 g, PCB 4.5 g, patch 3.6 g, chip antenna 1.2 g, SiP + GNSS + buzzer + reservoir + other parts 2.8 g, contacts and screws 2.6 g, enclosure 21.1 g = 64.8 g; after section 16 changes (PCB 0.8 mm -0.9, 12 mm patch -1.3, polymer -0.4, supercap +2.0) 64.2 g; with 2 x GZ215F at 0.7 g (derived) and added LDO/FET/resistors 63.7 g (parts-power.md line 153, state.md). Limit 65 g, margin 1.3 g.
Caveats: supercap and patch masses are derived, not published (weigh a sample); enclosure 21.1 g is a lead estimate. On alkaline cells the total is 81.2 g (46.0 g cells, conflicts.md C10), which the requirement does not cover.
