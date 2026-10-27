# Home thermostat: design summary

## What the board does

A two-zone heating controller that fits behind the plate of a standard Italian 503 wall box (BTicino 503E class).

- **Power:** runs on two AAA cells, with no mains supply for the electronics.
- **Outputs:** two latching relays switch the 230 V lines of two thermal valve heads or zone valves, one per room, up to 2 A each.
- **Temperature:** reads two room probes (10 k NTC, cables up to 2 m) to within ±0.5 °C between 5 and 35 °C.
- **Schedule:** runs from the Wi-Fi chip's clock and works with no network at all.
- **Wi-Fi:** every hour the device sends its temperatures over MQTT and collects new settings, so a change from the phone applies within 60 minutes.
- **Low battery:** before the cells run out, the device switches both outputs OFF and sends an alarm.
- **Local interface:** one button and one LED, no screen.

Electronics: ESP32-C3-MINI-1 Wi-Fi module with a 32 kHz crystal, a TPS610994 voltage booster with a 4.7 mF reserve capacitor, a CS1238 24-bit temperature converter, and 2 x DRV8837 drivers for 2 x TE PE014F03 latching relays.

## Status

Sign-off build, 2026-10-20 (`hwflow build --mc 100`): **PASS**.

- **Simulation:** 163 of 163 measures pass at 0 °C and 50 °C, before and after layout.
- **Monte Carlo (100 runs):** 100 % yield on every testbench except the reversed-battery one, at 99 %. The one failed run aborted numerically (see risk 9).
- **Layout:** KiCad DRC 0 errors, with the 230 V spacing rules active (4.0 mm clearance, 6.0 mm creepage). 0 unrouted nets.
- **Fabrication files:** generated in `build/fab`.
- **Requirements:** 21 of 21 verified, none failing.
- **Final review:** the one must-level finding (relays under the front board) is closed by your round 6 decision.

## Requirements

| ID | Priority | Requirement | Status | How it was checked |
|---|---|---|---|---|
| R1 | must | Fits a 503 box: two 62 x 40 mm boards, 28.6 mm deep of the 30 mm available | verified | layout |
| R2 | must | Battery life: about 16 months typical on alkaline AAA, at least 11 months worst case, about 2.2 years on lithium | verified | simulation and analysis |
| R3 | must | 3.3 V supply holds through Wi-Fi bursts, down to the low-battery cutoff and at 0 °C | verified | simulation |
| R4 | must | Two temperatures within ±0.5 °C (worst -0.41 °C), after a one-time calibration of each unit | verified | simulation |
| R5 | must | Probe cables up to 2 m: cable error 0.0015 °C, mains hum reduced by at least 24 dB | verified | simulation |
| R6 | must | Relay contacts 250 VAC, 5 A resistive | verified, with a gap | datasheet; motor surge rating risk accepted (round 5) |
| R7 | must | Latching relays draw no current while holding | verified | simulation |
| R8 | must | 230 V insulation | verified | KiCad DRC rules, 3D stack check, 0.4 mm insulating sheet |
| R9 | must | Relay switching does not disturb the Wi-Fi supply (droop under 30 mV) | verified | simulation |
| R10 | must | Wi-Fi and MQTT through a pre-certified module | verified | datasheet |
| R11 | must | Remote changes apply within 60 minutes | verified | analysis |
| R12 | must | Works offline, with clock drift of 2 minutes per month or less | verified | datasheet |
| R13 | must | Operates from 0 to 50 °C | verified | simulation |
| R14 | must | ESD protection on the touchable inputs | verified | datasheet |
| R15 | must | Screw terminals for 230 V and the probes | verified | datasheet |
| R16 | must | Low battery: both outputs OFF, then an alarm | verified | simulation |
| R17 | should | Battery voltage reported (error 21 mV or less) | verified | simulation |
| R18 | should | One button and one LED | verified | analysis |
| R19 | should | Parts cost of 28 USD or less per unit at 10 units | verified: 27.94 USD | BOM |
| R20 | should | Designed with CE marking in mind | verified | analysis; a safety review is still needed |
| R21 | could | Over-the-air firmware updates | verified | datasheet |

## Cost and size

- **Parts:** 27.94 USD per unit at 10 units, both boards. The two relays are 18 USD of that.
- **Not in that figure:** the insulating sheet (about 0.10 USD), the enclosure, the cells, and PCB fabrication and assembly.
- **Boards:** two 62 x 40 mm, 4-layer boards, fabricated together as one 130 x 40 mm panel and cut apart. The front board carries the Wi-Fi module, the battery holder, the converter and the logic. The rear board carries the relays and the 230 V and probe terminals.
- **Depth:** 28.6 mm in total, inside the 30 mm available behind the plate.

## Decisions you made

| Round | Decision |
|---|---|
| 1 | Battery only (no neutral in the box); remote changes within 60 min; 230 V thermal heads or zone valves, 2 A or less; probes within 2 m |
| 2 | Both outputs OFF on low battery, plus an alarm; prototype of 10 units; plastic plates only; priorities: cost, then battery life, then size |
| 3 | Low relays plus a screw-fixed battery cover, insulated probe tips and a breaker-off instruction for the prototypes; motor loads under 1.5 A; measure every 10 minutes |
| 4 | Two AAA cells instead of one AA; low-leakage reserve capacitor; up to 28 USD per unit |
| 5 | Keep the hourly Wi-Fi and accept 11-13 months for a worst-case unit; accept the relay surge-rating gap for the prototypes |
| 6 | 0.4 mm insulating sheet between the two boards |

## First-article risk list

Things simulation cannot prove, and the bench test that settles each one on the first boards.

| # | Risk | Bench test on the first boards |
|---|---|---|
| 1 | Battery life rests on typical figures: sleep current, reserve-capacitor leakage, module current at 50 °C | Measure the pack sleep current on 3 boards at 25 and 50 °C (pass: 16.7 µA or less at 2.5 V); measure the C1 leakage on 3 capacitors; measure the energy of one Wi-Fi wake (budget 0.5 J) |
| 2 | Relay surge with real valve heads (no datasheet rating, accepted) | Switch a real valve head or zone valve 1,000 times, then check the contact resistance |
| 3 | Relay footprint read off the datasheet drawing (2.5 vs 2.54 mm grid); TE lists the part as "not currently available" | Check one real relay against a 1:1 print before ordering boards; confirm stock for 20 relays |
| 4 | Converter stability with 4.7 mF on its output (datasheet maximum 100 µF); output up to 3.47 V with fresh lithium cells | Scope VOUT and SW during a Wi-Fi burst; insert the cells 20 times with contact bounce; read the sleep voltage with fresh lithium cells and with a 3.7 V and a 3.8 V supply |
| 5 | Cold, nearly empty alkaline cells (estimated internal resistance) | Measure the pack's internal resistance at 0 °C near the cutoff; check the Wi-Fi burst at 2.56 V |
| 6 | Temperature accuracy needs a calibration per unit (the converter's gain error is ±0.5 %) | Fit a 10.00 k 0.1 % resistor at each probe input, store the gain constant, then check against a reference thermometer at 5, 20 and 35 °C |
| 7 | Battery holder leads (26 AWG) add resistance at the converter input (24 mV margin at 0 °C) | Trim the leads to 30 mm or less; measure the drop during a Wi-Fi burst |
| 8 | Reversed battery right after a Wi-Fi wake (not simulated) | Reverse-swap the pack within 1 s of removal, from sleep and right after a Wi-Fi wake; check that the converter survives |
| 9 | One reversed-battery Monte Carlo run aborted numerically (cause unknown) | Covered by test 8 |
| 10 | Converter wake-up pins could feed the switched-off sensor supply during boot | Measure AVDD_SW in sleep and during boot |
| 11 | 3D fit of the two-board stack: insulating sheet, board spacers, holder overhang (1.95 mm of 2 mm), antenna 14.5 mm from the relay contacts | Assemble one unit in a real 503 box with the plate fitted; check the gaps and the sheet coverage; check Wi-Fi range through the plastic plate |
| 12 | Panel cut: the ground plane runs across the cut between the boards | Inspect the cut edges for exposed copper next to the probe and coil tracks (for production: separate board files) |
| 13 | Insulation: relay internal barrier is basic, battery cover needs a tool, probe tips insulated, breaker-off instruction | Hi-pot test 230 V parts to the low-voltage side; safety review before CE |
| 14 | Unconfirmed part data: SP0402B-ULC-01 polarity, JSMSEMI DRV8837 pinout, missing LCSC numbers for R18 and R20 | Check against the datasheets before ordering; check the first board under a microscope |

## Open items for production (not for the prototypes)

- Cheaper relays (they are 18 of the 27.94 USD).
- Separate board files instead of the cut panel.
- CE safety review: relay insulation and the insulating sheet.
- Firmware rules are in `notes/circuit.md`: converter configuration at every wake, temperature-dependent cutoff, no relay pulse during Wi-Fi, gain calibration.
