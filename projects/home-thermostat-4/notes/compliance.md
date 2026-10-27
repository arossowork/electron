# Compliance checklist (R28): EU CE route, 10 prototypes

Scope (R28, conflicts.md "Assumptions"): the 10 prototypes are for internal or friendly-site trials. They are **not placed on the market and carry no CE mark**. No notified body and no lab tests are needed for them. The design follows the CE route so that it does not have to change later. **No certificate or test report exists for this product.** Every item below is a design measure, an item from a part's datasheet, or a later test.

Status key: **design** = handled in the design, with the evidence named. **open** = not verified (a number from a search summary, an unread standard text, or a lab question). **later test** = needs a prototype or an accredited lab.

Directive frame: the product has a radio, so RED 2014/53/EU applies. RED Art. 3.1(a) brings in the LVD safety objectives with no lower voltage limit, and Art. 3.1(b) covers EMC. RoHS 2011/65/EU applies as well. The harmonised standards are the ones R28 names.

## 1. LVD / safety (RED Art. 3.1a): EN 60730-1 + EN 60730-2-9

| Item | Requirement | How the design addresses it | Evidence (file, number) | Status / what remains |
|---|---|---|---|---|
| Reinforced isolation 230 V to SELV, creepage and clearance (EN 60730-1 / IEC 60664-1, 250 V, PD2, material group III) | R10 | Mains zone at x <= 20 mm on face B, SELV at x >= 26 mm. 6.0 mm creepage and 6.0 mm clearance are enforced by DRC rules. Mains copper is kept off the inner layers. The frame screw notch counts as accessible metal, so 230 V copper stays >= 6 mm from it. | build/REPORT.md: "isolation mains-SELV (R10): 6.00 mm in plan (limit 6.0 mm); DRC clearance 0 violations; creepage 6.0 mm by DRC: 0 violations". Basis: conflicts.md C3 (reinforced = 5.0 mm creepage; 3.0 mm OVC II / 5.5 mm OVC III clearance), so the 6.0 mm target has margin against both. | design. **Open:** the table values come from conflicts.md C3 and were not checked against the standard text. The build verdict is FAIL (AC_L split into 2 copper islands, REPORT.md), so the isolation must be rechecked after that net is repaired. |
| Isolation through the board | R10, R27 | 1.6 mm FR4 is the solid insulation where face-B 230 V copper lies under face-A SELV parts. No mains copper is on the inner layers (design.yaml drc_rules `mains_outer_only`). | architecture.md 6.3 (>= 0.4 mm needed for reinforced); design.yaml board thickness 1.6 | design; confirmed by the hipot (next row). |
| Dielectric strength (hipot) | R27 | 3.75 kVrms for 60 s between the shorted 230 V terminals and the shorted SELV nets, no breakdown | requirements.yaml R27 | **later test:** no prototypes yet, no hipot record. Run it on all 10 units and file one record per unit. |
| Relay coil-to-contact insulation | R11 | Panasonic ADW1103HLW: 5000 Vrms coil-contact, 12 kV surge, coil and contact pins at opposite ends | notes/parts-230v.md (vendor page); notes/footprints.md (pin pattern from Panasonic catalog ASCTB8E) | **open:** the >= 6 mm coil-contact creepage is a search summary and the word "reinforced" was not read (notes/parts.md 7 item 2). Check against the Panasonic datasheet. |
| Protection against electric shock with the plate off (IEC 61032 test finger, IP2X) | R26, R12 | Cells and SELV face the user. The 230 V parts face the box. The 230 V solder joints on face A are covered by the MP1 insulating sheet (26 x 46 mm, >= 0.4 mm, UL 94 V-0) under a screw-fixed battery cover. | notes/insulating-sheet.md; conflicts.md D2, D5 | **later test:** test-finger inspection on a prototype. **Open:** the 0.4 mm distance through insulation for single-layer reinforced insulation and the GK-17 dielectric/RTI data are not checked against EN 60730-1. Confirm with the lab. |
| 230 V terminal | R12 | J1 KF128-5.08-3P: 250 V / 24 A IEC, OVC III, 4 kV impulse, 2.5 mm², PA66 V-0 | notes/footprints.md (Kefa drawing rev A) | **open:** J1 is an open screw terminal. IP2X is argued by location: J1 faces the box (conflicts.md D8). The lab must accept this argument. |
| Overcurrent protection and fuse breaking capacity | R13, D3 | F1 Walter 2010T3.15A250V (T 3.15 A) in L-in; 230 V traces >= 1.0 mm | notes/parts-230v.md: 100 A (UL) / 130 A (VDE) at 250 VAC, I2t 98 A²s, UL/VDE/CCC marks | **open certification risk (D3):** EN 60730-1 asks for IEC 60127 fuses used within their ratings, and the lab compares the breaking capacity with the prospective fault current, which can reach kA on a 16 A MCB circuit (notes/footprints.md "F1 breaking capacity"). Ask the lab. Alternatives: SETsafe SCT520 (5 kA, C49208729) or Hongda 40T (1000 A, C48392809). Both need a new footprint. |
| Pollution degree, overvoltage category, environment | R29 | Indoor wall box, 0-45 °C, PD2, OVC II; parts rated >= 0-50 °C | requirements.yaml R29; trace R29 (datasheet) | design. |
| Heat and fire: glow wire, terminal temperature rise, abnormal operation (welded contact, stalled firmware), software class (EN 60730-1 Annex H) | (none) | The terminal and the fuse body are PA66 V-0. The latching relays have no holding current, so a hung MCU cannot overheat a coil. | notes/footprints.md, notes/parts-230v.md | **later test (lab):** not mapped to a requirement. The lab defines the test plan, including the software class of the control. |
| EN 60730-2-9 (temperature sensing controls): sensing accuracy and sensor faults | R17, R19, R33 | Ratiometric ADS1115 with a ±0.067 °C worst case. Open and short probe faults are detected. Probe tips are insulated, and the probe cable is 300/500 V rated or sleeved. | build/REPORT.md SPICE 109/109 (R17, R19 pass); architecture.md 2.4 | design (simulation). **Later test:** declared operating values and their drift, on the prototype. |
| Instructions and markings | R34, D10 | Battery-cover label: switch off the heating breaker before changing the batteries. J1 L / R1 / R2 printed on the face B silkscreen. Installer note. | SUMMARY.md; conflicts.md D10 | design. **Later:** the printed label and the full EN 60730-1 marking set (rating, type of action, Annex documentation) are made for production. |

## 2. RED radio, EMC, RF exposure, cybersecurity

| Item | Requirement | How the design addresses it | Evidence | Status / what remains |
|---|---|---|---|---|
| EN 300 328 (2.4 GHz wideband) | R14, R16, R15 | Pre-certified ESP32-C3-MINI-1 module, with its PCB antenna used as certified. Antenna at the board edge with no copper under it. >= 15 mm to the relays and 230 V copper; >= 11 mm to the cells. | notes/parts.md 4 (R16): EU-type examination certificate E1177-210536, NB 1177; design.yaml floorplan antenna regions | **open:** the certificate number comes from a search summary. The PDF was not opened, and coverage of the -H4X variant is not confirmed (notes/parts.md 7 item 10). **Later test:** reduced EN 300 328 tests in the final housing, and a range test for the 11 mm battery distance (R15). |
| EN 301 489-1 / -17 (EMC for radio equipment) | R18 | NTC lines have a CESD3V3D5 TVS plus 1 kΩ in series (EN 301 489-1 asks ±4 kV contact / ±8 kV air; the design targets ±8 kV contact). Coil drive is in brake mode. The relays switch resistive valve heads, so no snubber is needed. Varistor footprint left unfitted (DNP). | requirements.yaml R18; notes/parts.md 4 (clamp current 9.1 mA against the ADS1115 10 mA limit, 9 % margin, limit from recall) | design for R18. **Open:** the ADS1115 input-current limit is from recall. **Later test:** ESD, radiated and conducted emissions, burst/surge on the 230 V port, and immunity. None of these is simulated. |
| EN 62311 (RF exposure) | (none) | Low-power module, wall-mounted, 60 min duty cycle | (none) | **later test/assessment:** not mapped to a requirement. Use the module's exposure assessment if the certificate covers it. |
| EN 18031-1 (RED Art. 3.3 d/e/f, cybersecurity, applies from 1 Aug 2025) | R14 (partly) | WPA2/WPA3 and TLS are in the radio's capability (R14). | requirements.yaml R14 | **later (firmware):** no requirement covers secure boot, flash encryption, credential storage, update mechanism or default passwords. Assess the firmware before market. |

## 3. RoHS 2011/65/EU (+ 2015/863)

| Item | Requirement | Status |
|---|---|---|
| Component RoHS declarations (design.yaml BOM: LCSC parts, Panasonic relays from Mouser/Digi-Key, Formex GK-17, NTC probes) | (none) | **open, later:** declarations not collected. Lead-free assembly at JLCPCB is assumed but not recorded. The NTC probes come from a marketplace with no documents (notes/parts.md item 18). Collect the declarations before market. |

## 4. Summary of open items for the lab or the prototype

1. Hipot 3.75 kVrms / 60 s on every unit (R27).
2. F1 breaking capacity against the prospective fault current (D3).
3. MP1 sheet: distance-through-insulation rule (0.4 mm against GK-17 at 0.43 mm) and the material data. Record the prototype sheet material (D5).
4. J1 IP2X argued by location (R12). Test finger on a prototype with the plate off (R26).
5. Relay coil-contact creepage >= 6 mm and "reinforced" wording (R11).
6. Module RED certificate number and variant coverage (R16). Antenna range test (R15).
7. EMC (EN 301 489), EN 62311, EN 18031-1, RoHS declarations, and the EN 60730-1 abnormal-operation and software-class tests. These are later tests and are not mapped to requirements.
8. Isolation recheck after the AC_L copper-island fix (build/REPORT.md verdict FAIL).
