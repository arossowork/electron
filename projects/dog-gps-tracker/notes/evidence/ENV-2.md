# ENV-2 evidence (inspection)

Requirement (must): Water and mud proof (dog swims)

Acceptance: enclosure IP67 (1 m, 30 min); battery door with gasket; no exposed connector; buzzer port sealed by a membrane

## Result

Result: open

Evidence: this project designs the PCB only. The enclosure (IP67, 1 m 30 min) is not designed here. architecture.md section 11 (line 146) states that the IP67 gasket is in the housing door and shell joint, not on the board; no verifiable drawing or test exists. The board has no exposed connector (J1 is wire pads inside the sealed shell; TP1-TP7 are inside the compartment, design.yaml line 86). The buzzer BZ1 (PKLCS1212E4001, top port 12 x 12 mm) needs a membrane over its sound port (not in the notes).

The enclosure design must provide: IP67 shell and door gasket, a battery door with gasket, a membrane-sealed buzzer port, no exposed connector, and an IP67 test report.
