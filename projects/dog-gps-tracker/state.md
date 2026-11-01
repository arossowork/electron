stage: 8-signoff
status: waiting-for-answers
# dog-gps-tracker
- PM rounds 1-7 in inbox/answers.md (LTE-M eDRX on request <=125 s; 2xAA Li-FeS2 + alkaline to -20 C; IP67; buzzer 80 dB typ; eSIM 1NCE (delegated); <=65 g; BOM <=55 USD; CE/RED deferred)
- Arch: nRF9151 + LC76G, TPS612994 boost 4.0 V + 2x GZ215F back face, 3.0 V AON LDO + GNSS LDO, 70x35 4L 0.8 mm
- Stage 3 done: floorplan PASS with risks (SiP escape hot spot 5.0x, 22 single-exit pads); parts review findings in the circuit brief
- Project libs dog/dog_misc (hwflow lib show/pads blind to project libs: tool gap)
open:
- nRF9151 land pattern vs Nordic PS (not fetchable; 2 open-hw drawings + Makerdiary pins agree)
- SI2301CDS IGSS, TPS3838K33 trip, 1NCE Italy eDRX: unverified
- cost ~52-53 USD vs 55; mass 63.7 g vs 65 (supercap mass derived)
- Stage 4-5 done: 10/10 sim measures pass, check/ERC 0 errors, fw 0 errors; review fix batch applied (16 findings)
- Stage 5 delta review skipped (budget over): R21/R22, U10/U11 ESD, RF fixed placements, screw_tr moved to (49,3.5) -> sign-off review
- Floorplan PASS with risks (3/52 nets hard; RF corridors under margin); stage 6 optimize skipped (weights are lead defaults)
- hwflow changes: fw nrf91 family (+test), floorplan quick placement seeds copper_len legs (asicflow-like)
- Stage 7 layout + 9 firmware running in parallel
- Stage 7: ~12 layout builds. Fixed: tools install, fine-pitch rules 0.127/0.15, nRF9151 pad rotation (pads 62-80), U2 layout block, LTE match/decap fixed placements, copper_keepout (hwflow) for LTE clearance + In1 GND plane, side_only back fix (hwflow)

- PM round 8: 75x35 board -> layout 0 DRC errors
- Stage 8: sign-off review 3 musts fixed (RF GND taps via board.pour_taps, U10/U11 front, fab); MC 100 100 %; fab in build/fab; trace 23/26
- hwflow changes: copper_keepout regions, side_only mirrored back, repair stitch vias honour via rule areas, board.pour_taps (tests added)
open:
- ENV-2 IP67 (must), CERT-2 battery door: enclosure not in scope -> question to PM (enclosure project or accept PCB-only sign-off)
- FL1/U8 inner GND ~10 mm to a via; C4/C5/J1 rows to drop from cpl.csv (hand solder)
- Stage 9 closed (178 fw checks): PM to confirm lead defaults C16 low-batt 14 days, C4 1 FOTA/year
