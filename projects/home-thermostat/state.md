stage: 1-requirements
status: waiting-for-answers
iteration: 2
open issues:
- Round 1 answered (inbox/answers.md): battery only (1 AA), 60 min latency, 230 V thermal heads or zone valves at 2 A or less, probes on cables < 2 m. Applied to R2, R3, R5, R6, R11, R15, R16.
- Conflicts resolved: C1 (1 AA, 60 min wake), C3 (boost with 0.6 A input limit plus a 4.7 mF rail buffer), C5 (reinforced barrier), C6 (cables < 2 m, 300/500 V cable). C4 resolved by architecture (two boards). The depth stack of 31.2 mm against 30 mm is for the architect (relay of 10.5 mm or less, or AA on the rear board).
- Open, product choice: C2 (safe state when the battery dies), C7 (metal plate).
- Round 2 questions in inbox/questions.json: q1 safe state, q2 cost and volume, q3 front plate, q4 optimization priorities.
- Defaults that were not asked: R4 accuracy +/-0.5 degC (5-35 degC), R13 0-50 degC operation.
