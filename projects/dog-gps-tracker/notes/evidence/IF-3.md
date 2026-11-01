# IF-3 evidence (datasheet)

Requirement (must): GNSS (GPS + Galileo at least) with A-GNSS support.

Acceptance: receiver datasheet: GPS L1 + Galileo E1, A-GNSS, TTFF with assistance <= 30 s.

## Result

Result: pass

Evidence: notes/datasheets/lc76g.md (Quectel LC76G hardware design / spec):
- line 2: GPS, GLONASS, Galileo E1, BDS, QZSS (GPS L1 + Galileo E1 met).
- line 11: cold-start TTFF 28 s without assistance, 15 s with EASY (self-generated assistance), 5 s with EPO (downloaded assistance, PM round 7: assistance data over the network). With assistance 5-15 s <= 30 s.
The firmware host tests (`hwflow fw test`, 10 IF-3 checks, 0 failed) cover the EPO download and the fix sequence.
