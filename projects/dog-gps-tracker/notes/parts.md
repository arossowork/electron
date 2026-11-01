# Parts (stage 3, merged)

Per-group tables with mpn, lcsc, price, footprint, side, limits, models, alternatives and drawings:
- Power: notes/parts-power.md (TPS612994 WCSP-6 boost at 4.0 V, 2 x CAP-XX GZ215F 2 x 90 mF supercaps (back face, hand solder; was GW209F), DMP2035U rev FET, TPS7A0230 GNSS LDO, TPL5010 WDT, TPS3839G33 supervisor, TPD1E10B06 TVS, CJ2305 VBAT divider block)
- RF / modem / GNSS / SIM: notes/parts-rf.md (nRF9151-LACA-R7, LC76G-PA, NN02-220 + 7-element match, Taoglas GP.1575.12.4.A.02 patch, SAFFB1G56 SAW, Truphone MFF2 eSIM)
- UI / sensors / debug: notes/parts-ui.md (PKLCS1212E4001 buzzer two-GPIO antiphase, Würth 0603 LED, TS-1187A button + ESD9B5.0ST5G, LIS2DW12, 7 test pads, battery wire pads)
- Project libraries: lib/ (dog: nRF9151 symbol + LGA-113 from hlord2000/nordic-lib-kicad, cross-checked vs JLTelectronics Mesh3310; dog_misc: LC76G, NN02-220, MFF2, LIS2DW12 symbol, GP.1575 patch, SAW). Corrections from the library pass (eSIM height 1.27 mm, LC76G 28 pads, LC76G replaces RF_GPS:Quectel_L76) supersede the group tables.

## Library blocks
- tps61299_boost: not taken, input limit 0.96-1.44 A pulls cold alkaline (2.8 V, 4 ohm) below 0 V; TPS612994 (100 mA limit) used instead, same topology.
- max17227a_boost: not taken, 2 A peak limit, no average input limit, 2.96 USD vs ~1.3.
- pfet_vbat_divider: taken, 2x 200k, 10 nF, plus N-FET gate level shifter.

## Unit cost (COST-1 <= 55 USD @1000, PM round 7 q1; scope: without enclosure, cells, SIM)
| group | USD |
|---|---|
| power, was 9.1 with GW209F (6.0 assumed) | 24.5 (2 x GZ215F 20.66 + 2nd hand solder +0.4, AON LDO +0.30, open-drain supervisor +0.07, SI2301CDS -0.04, balancing R +0.02: +15.41) |
| RF/modem/GNSS (excl. eSIM) | ~22.7-24.3 |
| UI/sensors | ~1.7 |
| PCB 4L 0.8 mm + passives misc (estimate) | ~3 |
| **total** | **~51.9-53.4 USD against 55 USD (margin 1.6-3.1)**; eSIM 1NCE EUR 14.50 per SIM is outside the scope; cost.extra lines: PCB/assembly/consigned-part handling as above in the "PCB + passives misc" line (not itemised) |

## Fix pass 2026-11-01 (changed rows)
| ref | part | footprint | price | limits |
|---|---|---|---|---|
| C_STORE1/2 | CAP-XX GZ215F x2 | dog_misc:CAPXX_GZ215F (back, 2.5 mm) | 10.33 each | abs 5.5 / rec 5.0 V, -40..70 C |
| U_LDO_AON | TPS7A0230PDBVR (C3747031) | SOT-23-5 | 0.30 | VDD_AON v_max 3.06 V |
| U_SUP | TPS3838K33DBVR (C135468), open drain | SOT-23-5 | ~0.30 | abs 7.0 (verify) |
| Q_REV | SI2301CDS-T1-GE3 (IGSS 100 nA to verify) | SOT-23 | ~0.10 | Vds 20 V |
| Q shifter | BSS138LT1G (C82045) | SOT-23 | 0.042 | Vth <= 1.5 V |
| U_ESIM operator | 1NCE MFF2 | dog_misc:MFF2_DFN8_5x6_P1.27 | EUR 14.50 (outside COST-1) | - |
Hold-up (4.0 V, 80 mA input limit, 0.458 A deficit): 235 ms cold new, 159 ms end of life. Mass 63.7 g <= 65. PWR-5: 10.4 uA (23 C typ), 20.4-21.8 uA (50 C design corner): re-allocate to <= 22 uA (notes/sleep-floor-reallocation.md).

## Open (stage-3)
- GZ215F + / - corner assignment and BAL tab from a drawing image: confirm on the first sample; mass derived.
- SI2301CDS IGSS 100 nA and TPS3838K33 trip min/max: Vishay/TI PDFs not read this pass.
- 1NCE Italy LTE-M/eDRX/PSM row; 1NCE MFF2 pin-out vs our footprint.
- TPS612994 only WCSP-6 0.4 mm pitch, no LCSC; consign. 0.9 V efficiency curve at 3.0 -> 4.0 V (image).
- LC76G full MPN, SAW F7F vs F0A, nRF9151 VDD decoupling values, ENABLE/VDD_GPIO power-up sequencing detail (parts-power.md G2).
- Design.yaml supercap floorplan item must become two sites ~42 x 15 mm (lead).
