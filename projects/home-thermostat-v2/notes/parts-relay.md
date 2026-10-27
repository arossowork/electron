# Parts: relay, mains and connector block group (stage 3)

Facts: `notes/datasheets/panasonic-dw.md` (Panasonic DW catalog 2024.3, pages 1-7). Stock/price could not be read live for most parts (the `hwflow parts` index has no relay, MOSFET or terminal category entries; LCSC product pages are partly blocked). Entries marked "verify at order" need an LCSC/JLCPCB check before the order (R29: stock >= 15).

## Relays K1, K2 (R6, R7, R9, R12)

| Item | Value |
|---|---|
| Role | 2 x latching relay, 1 Form A NO, 3 V 2-coil, mains contact COM/NO to J1 |
| MPN (chosen) | Panasonic **ADW1203TW** (IEC60335-1 type: 5 A 250 VAC, min 100e3 ops, p.4) |
| JLCPCB/LCSC | ADW1203TW: no JLCPCB/LCSC listing found. ADW1203W (standard 8 A, 50e3 ops at 8 A, reflow type): JLCPCB **C7070982**, stock and price not shown on the page (verify at order). LCSC ADW1103T (1-coil sibling) C7213019 was out of stock at 18.48 USD (1), 7.37 USD (200+) |
| Price qty 10 | not confirmed. Planning value 7-10 USD each (LCSC sibling 7.1-18.5 USD); architecture assumed 3.0 USD. Cost risk about +8 to +14 USD per unit for two relays |
| Datasheet | https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8811/mech_eng_dw.pdf ; https://industry.panasonic.com/ap/en/products/control/relay/power/number/adw1203tw |
| Symbol | project `thermostat_relay:ADW1203` (KiCad has only `Relay:ADW11`, the 1-coil, which has no pin 7). File `lib/symbols/thermostat_relay.kicad_sym` |
| Footprint | project `thermostat_relay:Relay_Panasonic_ADW12_2Coil_Latching`, file `lib/footprints/thermostat_relay.pretty/Relay_Panasonic_ADW12_2Coil_Latching.kicad_mod` |
| Pins = pads | 1 = coil A (-), 7 = coil common (+), 6 = coil B (-), 3 = COM (mains), 5 = NO (mains). Pads: 1 (0,0), 7 (0,3.75), 6 (0,7.5), 3 (17.5,0), 5 (21,7.5), drill 1.2 mm, pads 2.2 / 2.0 / 2.2 / 2.6 / 2.6. Source: catalog p.6 bottom view (KiCad ADW11 footprint has the same geometry; pin 7 at 3.75 mm is read from a small drawing: confirm with the STEP/CAD file) |
| Coil | 22.5 ohm +/-10 %, 133.3 mA, 400 mW per coil, set/reset <= 80 % = 2.4 V, max allowable 110 % = 3.3 V, recommended +/-5 % (2.85-3.15 V) |
| Timing | set/reset max 15 ms at rated voltage; about 5.5 ms max at 80 % voltage (p.5). Pulse 30 ms (R7) is 2x |
| Contacts | 8 A 250 VAC resistive (2000 VA), AgSnO2, min load 100 mA 5 V; 5 A 100e3 ops for the TW type; mechanical 1e6 |
| Isolation | coil-contact 5000 Vrms, surge 12 kV, open contact 1000 Vrms (p.4). 6 mm creepage/clearance and "reinforced" are NOT in the catalog (only in a trade article) |
| Size | body 24 x 10 x 18.8 mm, 8 g. Height 18.8 mm + 3.2 pin: fits 38 mm (R12) |
| Alternatives | (1) ADW1203HTW, inrush 16 A type, same footprint and height, 100 A inrush, TV-8, 50e3 ops at 8 A: use if the 4-head inrush or an inductive load is a concern; no cos phi rating either. (2) Hongfa HF115F-L / HF3F-L (JLCPCB: HF3F-L/12-1HL1T C190456, 437 pcs, 1.41 USD at 1 seen; 3 V coil version not verified) and Omron G5RL-K: 29 x 12.7 mm, 16 A, rejected by architecture for area (C3); they publish inductive ratings and reinforced isolation, so they are the fallback if Panasonic cannot confirm R6 inductive and R9 reinforced in writing. Not compared in detail (coil power and pulse time of the Hongfa 3 V latching types not read) |

Mains pad geometry and the 6 mm rule: the relay's own pin spacing already meets it. Coil pad 1 to contact pad 3 is 17.5 mm centre to centre (15.3 mm pad edge to edge at pad sizes 2.2 / 2.6), pad 6 to pad 5 is 21.0 mm: no slot is needed under the relay. The barrier line (6 mm keep-out between SELV and mains copper, no SELV copper on any layer within 6 mm of pads 3 and 5 and their traces) runs between x = 3 and x = 11 along the relay long axis. Pad 3 to pad 5 (open contact, same mains pole) 8.3 mm centre to centre. Two relays side by side at 13 mm pitch (10 mm body + 3 mm): pad edge to pad edge between K1 and K2 contact pads about 3.3 mm (R10: 3.0 clearance, 2.5 creepage: pass, tight). Mains pads: 5 A needs traces 2.5 mm wide at 1 oz (architecture).

Coil pulse energy per switch: rail side 3.3 V x 137 mA x 30 ms = 13.6 mJ (architecture); coil only, at 3.0 V rated: 0.4 W x 30 ms = 12 mJ. A 20 ms pulse (still 3.6x the 5.5 ms switching time at 80 % voltage) would be 9 mJ, but R7 asks for >= 30 ms. Two pulses 27 mJ (R8: 50 mJ reserve).

Coil window check (architecture: 1.0 ohm series, rail 3.00-3.37 V): worst corners with coil 20.25-24.75 ohm and 0.04 ohm NMOS: min 3.00 x 20.25 / 21.29 = 2.85 V (-5 %), max 3.37 x 24.75 / 25.79 = 3.23 V (+7.8 %). Both inside R7 (+/-10 %: 2.7-3.3 V) and the 110 % limit (3.3 V), 18 % above the 2.4 V set voltage. Slightly outside Panasonic's "+/-5 %" use recommendation on the high side. A 1.5 ohm series resistor gives 2.79-3.17 V and 12.4 mJ (less droop, R7 sim to re-run); keep 1.0 ohm unless stage 4 shows a problem.

## Drive circuit (4 x NMOS, 4 x diode, per relay 1 series resistor)

| Ref | Part | LCSC | Role | Key parameters |
|---|---|---|---|---|
| Q1-Q4 | Alpha & Omega **AO3400A** SOT-23 (JLCPCB C20917, on the JLCPCB library; stock and basic/extended status not shown on the page, verify) | C20917 | low-side switch per coil, source to GND, drain to coil pin 1 or 6 | Vds 30 V, Id 5.7 A, Vgs(th) max 1.45 V, Rds(on) 48 mohm at 2.5 V (about 40 mohm at 3.3 V), Ciss 630 pF, Idss <= 1 uA at 30 V (nA at 3.3 V). SPICE: no vendor model fetched; fit Level-1 VTO 1.0 V, KP x W/L about 11 A/V2 (gives 40 mohm at Vgs 3.3 V), source `fit`; replace with the AOS model if stage 4 needs it |
| D1-D4 (flyback, ref names per design) | Diodes Inc **1N4148W-7-F** SOD-123 (JLCPCB C83528) | C83528 | one per coil, anode at the NMOS drain, cathode at coil common pin 7 | 100 V, 300 mA, Vf 1.25 V at 150 mA, trr 4 ns, leakage 1 uA at 75 V (nA at 3.3 V). Coil current 137 mA < 300 mA. Alternative: SS14 Schottky (leakage worse in sleep) or BAT54 |
| R_ser x 2 | 1.0 ohm 1 %, 0603 or 0805 (generic, JLCPCB basic) | pick at design | one per relay, in the coil common (pin 7) path (only one coil pulses at a time) | 19 mW |
| R_gate x 4 | 100 ohm 0603 | basic | gate series (ringing, ESP32 pin) | |
| R_pd x 4 | 100 k 0603 | basic | gate to GND: no pulse during boot/reset | see risk on GPIO9 below |

Boot safety: the 100 k pull-downs hold the gates low while the pins are high impedance. ESP32-C3 GPIO9 (and GPIO2 for the strap) have internal pull-ups of about 45 k at reset: with a 100 k pull-down the gate would sit near 2.3 V, above Vgs(th), and the coil would fire at boot. Architecture section 7 proposes GPIO8 or GPIO21 for K2: stage 4 must pick pins without a reset pull-up (or use 10 k pull-downs, +0.33 mA only while a pin is held high: no sleep penalty). Firmware interlock: never drive A and B of the same relay together. No pulses while the MCU is in reset.

## Connectors

| Ref | MPN | LCSC | Stock / price | Role and key parameters | Footprint |
|---|---|---|---|---|---|
| J1 | DORABO/DIBO **DB128V-5.0-4P-GN-S** | C424682 | 15,115 pcs, about 0.19 USD (search result, verify) | 4-pole screw terminal, 5.0 mm pitch, 15 A 300 V class (search listing; datasheet to read at order), wire 0.5-1.5 mm2, poles COM1 NO1 COM2 NO2 | generic 4 x 1 5.0 mm THT; project footprint to be drawn in stage 4 from the DIBO datasheet (no KiCad symbol/fp checked) |
| J1 alt 1 | Cixi Kefa KF2EDGRM-5.0-4P (pluggable header) | C441281 | not read | 15 A, 5.0 mm | |
| J1 alt 2 | DIBO DB128L-5.08-4P-GN-S (5.08 pitch, side entry) | C2827883 | 22,095 pcs, 0.195 USD | | |
| J2 | Xinlaiya **XY350V-3.5-4P** | C557656 | 4,100 pcs; 0.3015 USD at 5, 0.2369 at 50 | 4-pole screw terminal, 3.5 mm pitch, 7 A 300 V, 18-24 AWG (1 mm2), height 8.6 mm, M2 screw | THT 4 x 1 3.5 mm |
| J2 alt | DIBO DB2EK-3.5-4P-GN-S (250 V, 10 A) | C395832 | not read | | |

J1 note on R10: at 5.0 mm pole pitch with a 2.4 mm pad the edge gap between NO1 and COM2 is 2.6 mm, below the 3.0 mm clearance of R10. Use an oval pad at most 2.0 mm wide across the pitch (gap 3.0 mm) or add a routed slot between pole 2 and pole 3 (slot gives creepage, the clearance still needs the 3.0 mm gap; the terminal housing wall adds a physical barrier). Terminals are wave-soldered THT parts: JLCPCB standard PCBA or hand-solder (R29).
J1 height not read from a datasheet (typical 10 mm, 4P 20 mm long x 10 mm); J1 area in the architecture is 10 x 20 mm. J2 4P 3.5 mm: 14 x 7.5 mm approx (architecture 14 x 8).

## Barrier, snubber, varistor

- No PCB slot is required for the relay (17.5 mm between coil and contact pads). A routed slot is only a fallback where the J1-to-cell distance or a trace route cannot reach 6.0 mm.
- Snubber: the architecture's choice (no RC snubber) holds: an RC across the contact leaks about 7 mA at 230 V (0.1 uF) and keeps thermoelectric heads warm. R6 asks 2 A inductive at cos phi 0.6, but the Panasonic catalog gives only cos phi 1.0 ratings (8 A, 50e3; 5 A, 100e3 for TW). The listed loads are resistive heaters (valve heads, 4 x 1 A inrush for seconds, 1.2 A steady) or a boiler enable contact: use the 5 A resistive rating and put the inductive interpretation to the requirement owner. If the load is a motorised zone valve or contactor coil: add a DNP footprint for a 275 VAC varistor across each load (not across the contact), e.g. 7 mm disc (area about 8 x 5 mm per output in the mains zone, to check against C6), leakage tens of uA.

## Issues for the architecture

1. R6 inductive rating not in the datasheet; R6 and R9 evidence ("reinforced", 6 mm) not in the 2024.3 catalog: get a Panasonic statement (or the IEC60335-1 / EN 60730 insulation report) or switch to a Hongfa/Omron relay with those ratings (area cost, C3/C6).
2. Cost: relay price is probably 7-10 USD each, not 3 USD: BOM +8 to +14 USD, still below the 25 USD limit if the rest is as estimated (about 16.7 -> about 25-31 USD: may break R28, recompute). The TW type is not stocked at JLCPCB: hand-solder.
3. Area (C6): footprint area per relay: courtyard 24.5 x 10.5 = 257 mm2 (body 24 x 10 = 240 mm2, pad bounding box 23.2 x 9.7); two relays at 13 mm pitch occupy 23.5 x 24.5 = 576 mm2 (rotated 90 degrees in the floorplan: 24 mm along y, 23 mm along x). The architecture's relay coil halves (320 mm2) and barrier (216) are consistent. No change to the C6 balance (+298 with parts under the cell) from the relay.
4. Boot pulses on the pins with reset pull-ups (above).
5. The set/reset coil assignment (pin 1 vs pin 6) must be confirmed on the first article.
6. `uv run hwflow lib show/pads` do not load project libraries (design-less); the symbol and footprint could not be checked by the CLI here: run `hwflow check` once design.yaml exists. Also `lib show Relay:ADW11` crashes (mixed alphanumeric pin numbers in the hwflow sort).

## Cost alternatives (stage 3 follow-up, 2026-10-20)

Source limits: Bash network access and PDF download were blocked in this session; WebFetch could not parse the Omron/Hongfa PDFs. Relay facts below come from distributor/LCSC/JLCPCB listing pages and search extracts. Every entry marked "verify" must be read from the datasheet before the order. I found no JLCPCB-stocked 3 V latching relay with >= 5 A 250 VAC and >= 4 kV coil-contact.

### Relay candidates

| Candidate | LCSC / stock / price | Coil | Contacts | Insulation | Size, KiCad | Verdict |
|---|---|---|---|---|---|---|
| Panasonic ADW1203TW (baseline) | none at LCSC/JLCPCB; sibling ADW1103T C7213019 out of stock, 18.48 (1) / 7.37 (200+) USD; plan 7-10 USD | 2 coil, 22.5 ohm, 133 mA, 400 mW | 5 A 250 VAC cos phi 1, 100e3 ops; no cos phi 0.6 | 5 kV coil-contact; 6 mm / "reinforced" not in catalog | 24 x 10 x 18.8 mm, courtyard 257 mm2; project symbol/footprint | fails R6 inductive and R9 wording, cost high |
| Omron G5RL-U1A-E DC3 (single coil) | not at LCSC (none found); novapart 3.05 EUR (about 3.5 USD), 50 pcs, lead time 190 days | single coil, 3 V, 15 ohm = 200 mA, 600 mW (novapart listing); needs an H-bridge (4 FET per relay); 3 V +10 % = 3.3 V max, rail reaches 3.37 V: add about 0.5 ohm series | 16 A 250 VAC SPST-NO, 24 VDC | 10 kV impulse, 8 mm coil-contact creepage (Omron feature list, search extract; verify page) | 29 x 12.7 x 15.7 mm; no KiCad symbol or footprint (G2RL/G5LE are different pinouts) | best on R6/R9; cheap; not JLCPCB-stocked, hand-solder |
| Omron G5RL-K1A-E (2-coil) | not at LCSC; 5 V version 3.0-4.55 USD at Newark/Arrow (qty 1 to 50); Electrokit 85 SEK | 2 coil, 5 V = 33.3 ohm, 150 mA (Electrokit); a 3 V coil version is not confirmed in any listing | 16 A 250 VAC | as G5RL-U | 29 x 12.7 x 15.7 mm; no KiCad parts | keeps the 4-NMOS drive if a 3 V part exists: verify with Omron |
| Hongfa HF115F-L | HF115F-LS-12-HSL2F C190453, 229-777 pcs, 4.26 (1) -> 2.33 (4000) USD; JLCPCB C190454 (HF115F-S/12-HSF), 161 pcs, 3.29 (1) -> 1.94 USD; 12 V only, no 3 V part found | 12 V coil; 5 V single coil about 62.5 ohm / 400 mW (C3) | 16-20 A 250 VAC, 5 kV coil-contact, reinforced (conflicts.md C3 cites VDE 0700/0631) | 5 kV | 29 x 12.7 x 15.7 mm; no KiCad parts | only JLCPCB-stocked power-latching family, but no 3 V coil: not usable at 3.3 V without another rail |
| Hongfa HF3F-L | C190594 (5 V, 62.5 ohm, 800 mW), C190456 (12 V, 1.41 USD at 1) | single coil | 10 A 277 VAC | only 2 kV coil-contact (RS listing) | 19 x 15.2 mm | rejected: fails R9 (>= 4 kV) |
| Zhejiang Fanhar FH42L-1AT-L2-DC3V/16A | 4project.co.il listing, no LCSC | 2 coil, 3 V | 16 A 250 VAC | not read | 10 x 24 mm | no datasheet access: not recommended |
| Hongfa HFD2/003-S-L2 | C910882 (JLCPCB) | 3 V | signal relay 2 A | - | - | contact too small: rejected |

HF32F, HF46F, Song Chuan 832AL, Ningbo Forward FRL, Golden GH and HKE: no 3 V latching listings found, not evaluated.

### SGM58031 price check (C700177)

Re-checked on the LCSC page 2026-10-20: SGMICRO SGM58031XMS10G/TR, tiers 6.3749 (1), 5.3875 (10), 4.7869 (30), 4.2818 (100) USD, stock 3764. The 5.39 USD is real: a 16-bit PGA ADC with 4 inputs is ADS1115 class, and the ADS1115IDGSR (C37593) is 5.12 USD at Mouser, out of stock at LCSC. The 0.5-1 USD expectation was wrong. No cheaper in-stock pin-compatible 16-bit part found. A 12-bit part (ADS1015/TLA2024 class, about 1-2 USD, not priced here) would need an R5 accuracy check by the circuit stage: PM/circuit decision, not made here.

### Unit cost at qty 10 (USD)

Non-relay BOM from the four parts notes (qty-10 prices): MCU group 4.38 (module 3.353, crystal 0.595, LED 0.268, button, caps, resistors, ESD) + power group 2.86 (TPS61099 0.95, L1 0.17, 2 x 22 uF 0.03, bulk cap 1.0, battery clips 0.456, divider and reverse protection about 0.26) + NTC group 5.73 (ADC 5.39 + passives 0.34) + relay drive 0.41 (4 AO3400A 0.295, 4 diodes, resistors) + connectors 0.49 (J1 0.19, J2 0.30) = 13.87, rounded 13.9 (+/- 0.5: some LCSC numbers are "verify", the reverse-protection part is unpriced).

JLCPCB per-batch costs. Read from the JLCPCB assembly page: setup from 8 USD, labor 0.0016 USD per joint. Assumed, not read from a quote page: PCB 10 pcs 2-layer up to 100 x 100 mm about 5 USD, stencil 8 USD (may be 1.5-8). Shipping excluded as in R28.

| Item | Batch | Per unit |
|---|---|---|
| 2-layer PCB 65 x 42, 10 pcs (5 pcs is the minimum, 10 needed) | 5.0 | 0.50 |
| PCBA setup | 8.0 | 0.80 |
| Stencil | 8.0 | 0.80 |
| Extended-part fee: about 18 unique extended SMD parts x 3.0 (module, crystal, boost, inductor, 2 clips, ADC, 2 ESD, LED, button, 0.1 % resistors, 22 uF, 910 ohm, AO3400A, others; relays, terminals, bulk cap are hand-soldered) | 54.0 | 5.40 |
| Solder joints about 150 x 0.0016 | 2.4 | 0.24 |
| Total fixed | 77.4 | 7.74 |

(a) Panasonic DW: 13.9 + 2 x 8.5 (range 7-10, so 14-20) + 7.74 = **38.6 USD** (range 35.6-41.6).
(b) Omron G5RL drop-in 2-coil, if a 3 V version exists: 13.9 + 2 x 3.5 + 7.74 = **28.6 USD**; with the single-coil U type add four FETs for two bridges (+0.3): 28.9 USD.
Both exceed the 25 USD target of R28 (should). Largest levers: the 18 extended-part fees (5.4 per unit), the ADC (5.39); at 100 units the fixed share falls from 7.74 to about 1.2 USD. Neither relay is JLCPCB stocked: hand-solder in the BOM (R29) in both cases.

### Area outcome for (b)

Omron body 29 x 12.7 mm. Two relays side by side at 14.5 mm pitch (12.7 body + 1.8; the 3.0 mm R10 clearance between contact pads is to verify with the footprint): 29 x 29 = 841 mm2 against 576 mm2 for the Panasonic pair (+265 mm2). The 8 mm coil-contact creepage is inside the relay, so no separate 6 mm zone is added (the architecture's 216 mm2 barrier stays). C6 balance after the AA clip fix was +298 mm2 on 2730 mm2: 298 - 265 = **+33 mm2** (1 % of the board), not routable. The 65 x 42 mm board does not carry (b). Demand with (b) is 2697 mm2. For a 30 % margin the board needs about 3500 mm2: **75 x 48 mm = 3600 mm2** (margin 903 mm2, 33 %). It fits the C3 interior estimate of about 96 x 58 mm with 10 mm per side for walls and wiring, but needs R12 changed (outline <= 65 x 42) and a blank plate instead of the 3-module window (67.5 x 45): a product decision. JLCPCB PCB price does not change below 100 x 100 mm. Height 15.7 mm is within 38 mm.

### Recommendation

1. Do not change the relay silently. R6 (cos phi 0.6) and R9 (reinforced) are unproven for the Panasonic part; the Omron G5RL (16 A, 10 kV impulse, 8 mm) is the only candidate with credible evidence, about 10 USD cheaper per unit but needing a 75 x 48 mm board (R12 change). Ask the product person: (A) keep 65 x 42 mm and the DW, get Panasonic's written statement on cos phi 0.6 and reinforced insulation, accept about 38.6 USD; or (B) 75 x 48 mm with G5RL, about 28.6 USD. In both cases relax R28 or cut extended parts.
2. Before B: confirm with Omron or a distributor that a 3 V G5RL-K (2-coil) exists (else G5RL-U1A-E DC3 with H-bridges and 0.5 ohm series resistors), get qty 10 stock, and create a symbol and footprint (none in KiCad).
3. Keep SGM58031 at 5.39 USD.

Risks: relay not stocked at JLCPCB or LCSC in both options (single source, hand-solder, 190 days lead time seen for the G5RL-U); Omron 3 V K version unconfirmed; PCB and stencil prices assumed; Omron figures come from listings, not datasheet text; no KiCad library parts for any candidate.
