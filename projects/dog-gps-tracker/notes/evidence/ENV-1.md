# ENV-1 evidence (datasheet)

Requirement (must): Operates outdoors -20 to +50 C

Acceptance: all parts rated -20..+50 C or wider (datasheet); battery derating at -20 C in the energy analysis

## Result

Result: pass

Evidence: every BOM part with a documented range covers -20..+50 C. Sources are manufacturer datasheets or product pages (web check 2026-11-01) or the project datasheet notes. The temperature column is T_A or T_J as the datasheet states it.

| ref | part | range (C) | source |
|---|---|---|---|
| U1 | nRF9151 | -40..85 | Nordic nRF9151 product page, key features (notes/datasheets/nrf9151.md line 7) |
| U8 | LC76G-PA | -40..85 | Quectel LC76G HW design V1.3 (notes/datasheets/lc76g.md) |
| U9 | LIS2DW12TR | -40..85 | ST DS11811, ordering table and abs max |
| U2 | TPS612994 | -40..125 | TI TPS61299 datasheet, recommended T_J |
| U3, U4 | TPS7A0230 | -40..125 | TI TPS7A02 datasheet, T_J |
| U5 | TPS3838K33 | -40..85 | TI TPS3838 datasheet, T_A |
| U6 | TPL5010 | -40..105 | TI TPL5010 datasheet sec. 7.3 |
| U7 | 1NCE MFF2 eUICC | -40..105 | notes/datasheets/mff2-esim.md |
| M1 | SI2301CDS | -55..150 | Vishay Si2301CDS, T_J and T_stg |
| M2 | CJ2305 | -50..150 | JCET CJ2305 datasheet, T_J and T_stg |
| M3 | BSS138LT1G | -55..150 | onsemi BSS138LT1/D rev 12 |
| D1 | TPD1E10B06 | -40..125 | TI datasheet rev G, T_A |
| U10, U11 | TPD4E05U06 | -40..125 | TI TPDxE05U06 datasheet rev O, T_A |
| D3 | ESD9B5.0ST5G | -55..150 | onsemi ESD9B/D, T_J and T_stg |
| D2 | 150060RS75000 LED | -40..85 | Wurth datasheet |
| SW1 | TS-1187A-B-A-B | -30..85 | XKB listing (LCSC C318884, JLCPCB C318884); parts-ui.md says -40..85, listing is the narrower value |
| BZ1 | PKLCS1212E4001 | -20..70 | Murata spec JGB40-1584B (notes/datasheets/pklcs1212e4001.md); margin 0 at -20 C |
| C_STORE1/2 | GZ215F | -40..70 | CAP-XX datasheet Table 1 (notes/datasheets/gz215f.md) |
| AE2 | GP.1575.12.4.A.02 | -40..85 | Taoglas (notes/datasheets/gnss-patch-saw.md) |
| FL1 | SAFFB1G56AC0F7F | -30..85 | Murata SAFFB1G56AC0F0A datasheet; the F7F suffix page was not found, so the same family is assumed |
| AE1 | NN02-220 | -40..125 | Ignion DS_NN02-220 and user manual |
| L1 | DFE201610E-1R0M=P2 | -40..125 | Murata product page, self-heating included |
| L2-L5 | LQW15AN_80 | -55..125 | Murata DS_LQW15AN_80 |
| C19-C23 (GJM1555C1H...) | C0G RF caps | -55..125 | Murata GJM15 datasheets |
| other C | dielectric not specified in design.yaml | open item | BOM must call X5R or better (-55..85); X7R is -55..125 |
| R | thick-film 0402/0603 | -55..155 typical | design.yaml gives only value and tolerance; BOM must call a -55..155 C thick-film class |

Cells (user's choice, not part of the BOM per COST-1): the E91 alkaline is rated -18..55 C (notes/datasheets/e91.md), 2 C short of -20 C. The acceptance covers this with the battery derating at -20 C in the energy analysis, which is in architecture.md section 16, "Alkaline (PWR-7)" paragraph (line 180), and the E91 note (R_eff 2.0 ohm per cell at 250 mA, 4.0 ohm per pair, boost input held at -20 C) and parts-power.md hold-up at -20 C (235 ms against 100 ms). Li-FeS2 cells are rated lower. This is not a part failure.

Caveats: the SAW suffix and generic C/R classes are assumptions flagged above. The TS-1187A listing is not a manufacturer datasheet.
