# IF-1 evidence (datasheet)

Requirement (must): LTE-M with NB-IoT fallback cellular link, PSM and eDRX.

Acceptance: module datasheet lists Cat-M1 and Cat-NB1/NB2, PSM, eDRX, and bands B3, B8, B20 (EU) at least.

## Result

Result: pass

Evidence: notes/datasheets/nrf9151.md line 4 (nRF9151 product specification): Cat-M1 bands incl. B3, B8, B20; NB1/NB2 incl. B3, B8, B20; PSM, eDRX, DRX. Line 3: eDRX 81.92 s and PSM currents, the modes the firmware uses (architecture.md, PWR-2 budget). The firmware host tests (`hwflow fw test`, 3 IF-1 checks, 0 failed) exercise the attach, PSM and eDRX sequence on the simulated board.
