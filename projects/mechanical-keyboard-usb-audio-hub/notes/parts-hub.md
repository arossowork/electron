# Parts: HUB / USB DATA group (stage 3)

## Iteration 2 summary (2026-10-22): final table (price in USD at 100 pcs unless noted; stock from the iteration 1 LCSC read, not re-read)
| role | mpn | lcsc | price | stock | datasheet | lib_id | fp_id |
|---|---|---|---|---|---|---|---|
| hub1, hub2 | GL3523-OTY30 | C390630 | 1.54 (260) | 12008 | notes/datasheets/gl3523.md (pins p.14-16, package p.42) | kb_hub:GL3523 (new) | Package_DFN_QFN:QFN-76-1EP_9x9mm_P0.4mm_EP5.81x6.31mm (library; rotated 90 vs Genesys numbering, same cyclic order) |
| hub xtal 25 MHz | X322525MOB4SI | C9006 | 0.067 (150) | 61510 | LCSC | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm |
| host UFP + CC + SS mux | HD3SS3220IRNHR | C701817 | 1.09 | 556 | notes/datasheets/hd3ss3220.md (p.3-5, 45-46) | kb_hub:HD3SS3220 (new) | kb_hub:WQFN-30-1EP_2.5x4.5mm_P0.4mm_EP1.2x3.2mm (new) |
| front C orientation mux | HD3SS3212RKSR | C129320 | 1.03 | 336 | notes/datasheets/hd3ss3212.md (p.4, 20-21) | kb_hub:HD3SS3212 (new) | kb_hub:VQFN-20-1EP_2.5x4.5mm_P0.5mm_EP1.0x3.0mm (new) |
| USB-C 24P mid-mount (x4) | HC-TYPE-C-24P-MM-1.4-5A-12 | C5307768 | 0.6624 | 738 | notes/datasheets/connectors-hctl-kailh.md (iteration 3: drawing still not retrievable; no readable alternative: JAE DX07S024WJ1R350 out of stock, SHOU HAN QCHT is for 1.0/1.2 mm boards) | Connector:USB_C_Receptacle | kb_hub:USB_C_Receptacle_HCTL_HC-TYPE-C-24P-MM-1.4 (PROVISIONAL JAE geometry, no board cut-out modelled; release blocker: compare with the HCTL PDF opened in a browser) |
| USB-A 3.0 right angle (x2) | HC-ST-003-01-Z | C2845331 | 0.1078 (200) | 2490 | connectors note (signal pads = KiCad Molex 48393-001 = EasyEDA footprint of the part; height class 6.5-7.55 mm from the Wurth 692122030100 drawing, HCTL height unverified) | Connector:USB3_A | kb_hub:USB3_A_HCTL_HC-ST-003-01-Z_Horizontal (pads verified against Molex fp; shield holes from EasyEDA; body depth estimated) |
| SS ESD, recommended (x8) | TPD4E02B04DQAR (0.25 pF) | C106794 | 0.19 (500) | 0 at LCSC: JLC global sourcing (PM decision) | LCSC page | Power_Protection:TPD4E02B04DQA | Package_SON:USON-10_2.5x1.0mm_P0.5mm |
| SS ESD, in-stock fallback | TPD4E05U06QDQARQ1 | C81353 | 0.4717 | 2315 | TI | Power_Protection:TPD4E05U06DQA | Package_SON:USON-10_2.5x1.0mm_P0.5mm |
| D+/D-/SBU ESD (x5) | PUSB3FR4Z-ES (Cio 0.45 pF typ, USB 2 use only) | C41429303 | 0.0437 | 15510 | LCSC page | Power_Protection:TPD4E05U06DQA | Package_SON:USON-10_2.5x1.0mm_P0.5mm (DFN2510-10L, pad map to verify) |
| SS AC caps, RTERM | 100 nF 0402 (C1525), 20 k 0402 | JLC basic | ~0.003 | basic | GL3523 p.15 | Device:C / Device:R | Capacitor_SMD:C_0402_1005Metric / Resistor_SMD:R_0402_1005Metric |

### Changes in iteration 2
1. Library: kb_hub symbols GL3523, HD3SS3220, HD3SS3212 and footprints WQFN-30, VQFN-20, USB3 A, USB-C 24P; checked with `hwflow check` on a scratch project (symbol and footprint load, pins match pads, package pad count and pitch accepted); the scratch project was deleted. Files: lib/symbols/kb_hub.kicad_sym and lib/footprints/kb_hub.pretty/. The `hwflow lib show/pads` CLI does not load project libraries (it uses the KiCad share only), so use `hwflow check` to inspect them.
2. GL3523 package: QFN76 9 x 9, pitch 0.4, EP 5.75 x 5.75 nominal (5.10-6.40) per Genesys p.42: the library footprint with EP 5.81 x 6.31 is inside the range; no project footprint.
3. Host Rp level: HD3SS3220 in GPIO mode (ADDR NC, PORT to GND) gives OUT1 (pin 25) and OUT2 (pin 26) open-drain, 200 k pull-ups to 3.3 V: OUT1 H/OUT2 H = default 900 mA, OUT1 L/OUT2 H = 1.5 A, OUT1 L/OUT2 L = 3 A (datasheet p.4). Alternative: I2C 0x67 or 0x47, register 0x08 CURRENT_MODE_DETECT bits 5:4. Both need 2 MCU pins (GPIO27, GPIO28 in the keyboard note) or can go straight to the power group ILIM logic.
4. SS ESD: the only part found with Cio <= 0.3 pF is TPD4E02B04DQAR (0.25 pF, symbol exists): 0 stock at LCSC, buy through JLC global sourcing. PUSB3FR4Z-ES is 0.45 pF (LCSC page), so it stays USB 2 only.
5. Connector heights vs the 11.1 mm stack (R24: tallest front part 7.5 mm + 1.6 board + 2.0 back): USB-C mid-mount shell is 3.3 mm tall (sister drawings), sunk in a cut-out: well inside. USB-A 3.0 right angle: class value 6.6-7.2 mm above the board (8.2-8.8 mm overall), vendor drawing not retrievable, so unverified: if it measures more than 7.5 mm the stack breaks. Alternative if it does not fit: a low-profile USB-A 3.0 right-angle (e.g. the SUYIN 2.5 mm-protrusion class receptacles) or a 2.0 USB-A for the one port that does not need SS (not acceptable for R7/R8 at SS speed).

Prices are LCSC USD at the quoted tier, stock read on 2026-10-22 from the LCSC product page. `hwflow parts` returned errors or nothing for these categories (404 / empty), so every lookup is from LCSC pages. No layout block of the library covers this group (blocks: esp32c3_mini_decoupling, rev_guard_2pfet, tps61299_boost).

## Chosen parts

| ref role | mpn | LCSC | price (qty) | qty | KiCad symbol | footprint | key specs |
|---|---|---|---|---|---|---|---|
| hub1, hub2 | GL3523-OTY30 | C390630 | 1.54 (260), 1.89 (30) | 2 | none, project symbol needed (Interface_USB:TUSB8041 is a different pinout) | Package_DFN_QFN:QFN-76-1EP_9x9mm_P0.4mm_EP5.81x6.31mm (check EP against LCSC datasheet; alt EP3.8x3.8) | 4-port USB 3.1 Gen 1, stock 12008, 5 V in, built-in 3.3 V LDO and 1.2 V DC-DC (needs L + FB divider), 25 MHz xtal, no flash needed |
| hub crystals | X322525MOB4SI | C9006 | 0.067 (150) | 2 | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | 25 MHz, 12 pF CL, +-20 ppm, ESR 50, stock 61510. Load caps 2 x 18 pF (CL 12 pF, 3 pF stray) |
| host upstream mux + CC | HD3SS3220IRNHR | C701817 | 1.09 (100) | 1 | none, project symbol | none for WQFN-30 2.5x4.5, project footprint | Type-C port controller (UFP mode) with 10 Gb/s 2:1 SS mux, stock 556. Gives orientation for the hub's single SS port and the Rp current level |
| front C SS orientation mux | HD3SS3212RKSR | C129320 | 1.03 (100) | 2 | none, project symbol | TQFN-20 2.5x4.5 P0.5, none in library (Package_DFN_QFN:DHVQFN-20 EP differs), project footprint | 2 diff channels 2:1, 8 GHz BW, insertion loss -1.6 dB at 5 GHz, Ron 5 ohm, VCC 3.3 V, SEL from orientation, stock 336 |
| USB-C receptacles (front C1, C2, back host, back PD in) | HC-TYPE-C-24P-MM-1.4-5A-12 | C5307768 | 0.6624 (100) | 4 | Connector:USB_C_Receptacle (24 pins + S1) | none for this part (nearest: Connector_USB:USB_C_Receptacle_GCT_USB4125...); project footprint from the vendor drawing | 24P mid-mount SMT, 1.4 mm sink, 5 A, stock 738. PD-in uses the same part, SS pins left open |
| USB-A 3.0 receptacles (front A1, A2) | HC-ST-003-01-Z | C2845331 | 0.1078 (200) | 2 | Connector:USB3_A | none matching (USB_A_* in library are USB 2); project footprint | 9P right-angle through-hole, stock 2490. Height not read from the LCSC page, check the datasheet for the 11.1 mm stack |
| SS ESD arrays (1 per SS pair set) | TPD4E05U06QDQARQ1 | C81353 | 0.4717 (100) | 8 | Power_Protection:TPD4E05U06DQA (D1+,D1-,D2+,D2- pins 1,2,4,5; GND 3, 8) | Package_SON:USON-10_2.5x1.0mm_P0.5mm (symbol default); Package_DFN_QFN:Diodes_UDFN-10_1.0x2.5mm_P0.5mm has the same 10 pad layout | 4 ch, 0.5 pF class (TI figure, check datasheet CIO), IEC 61000-4-2 +-8 kV contact (TI part +-8/+-15), clamp 14 V at 2.5 A, stock 2315 |
| D+/D-/SBU/CC array (5 connectors) | PUSB3FR4Z-ES | C41429303 | 0.0437 (100) | 5 | Power_Protection:TPD4E05U06DQA (pinout equal) | DFN2510-10 (project footprint) | 4 ch, 3.3 V standoff, 5 V clamp 8 A, stock 15510, capacitance not on the LCSC page (USB 2 only, fine). CC lines need 20 V tolerance: do not use this array on CC (the power group owns the CC TVS) |
| SS AC caps | 100 nF 0402 X7R 16 V (JLC basic, e.g. CL05B104KO5NNNC) | C1525 | ~0.003 | 16 | Device:C | Capacitor_SMD:C_0402_1005Metric | 75-200 nF, one per TX line at the transmitter (hub1 10, hub2 4, spare 2) |
| RTERM | 20 k 1 % 0402 | JLC basic | ~0.002 | 2 | Device:R | R_0402 | datasheet p.15: 20 k between RTERM and GND |

Count of SS ESD arrays: each C port has 2 SS pair sets (A and B side) = 2 arrays per C, x3 C ports (C1, C2, host) = 6, plus 1 per USB-A x2 = 8. The PD-in C port needs no SS or D+/D- array.

## Group cost (100 qty)
GL3523 2 x 1.60 = 3.20; xtals 0.13; HD3SS3220 1.09; HD3SS3212 2 x 1.03 = 2.07; USB-C 4 x 0.66 = 2.65; USB-A 2 x 0.12 = 0.24; SS ESD 8 x 0.47 = 3.77; D+/D- ESD 5 x 0.044 = 0.22; caps, resistors, load caps ~0.15; 1.2 V DC-DC L + caps for two hubs ~0.30. **Total ~ USD 13.8** (no flash, no EEPROM).

## Bus-mode current (R19, conflict C12), GL3523 datasheet p.39 (5 V input, silicon level)
- Hub1, 4 SS ports active: 821 mW config / 825 mW R/W = 164 / 165 mA. Hub1 with only the hub2 link active: 442 mW = 88 mA.
- Hub2 (1 SS user port active + uplink, 2 HS/FS ports): base 28 mA + SS term + USB 2 term = 100 mA config (88 + 12), 115 mA R/W (89 + 27). Hub2 with no user SS device (A2 empty), 2 USB 2 ports: 40-55 mA.
- Worst case both hubs, every user port holding an SS device: 165 + 115 = **280 mA** against the 272 mA architecture estimate (172 + 100). Quiet case (no user device on A1/A2/C1/C2): hub1 88 + hub2 40 = **128 mA**.
- Hence the hub choice does not close C12: datasheet worst case is 8 mA above the estimate (R/W) or 8 mA below (config, 264). The conflict budget (549 mA typ) moves to 541-557 mA, still over the 500 mA limit by 41-57 mA. The 3.3 V LDO and the 1.2 V DC-DC are already included in these figures. Using external 1.2 V/3.3 V rails would save only ~4 %.
- Levers that do reduce it: (a) in bus mode count only the connected SS devices (hub1 drops 25 mA per idle SS port), (b) disable the hub2 SS PHY of DS4 and the unused SS ports in firmware (needs SPI flash; the flash adds ~5 mA), (c) a single 7-port hub: none found with lower power. USB5807 class is ~1.2 W (architecture estimate) and not checked here, TUSB8041 4-port is 2 x worse in cost. No datasheet figure under 280 mA was found for the group.
- The overcurrent, non-removable and per-port enables use the hub pins listed in notes/datasheets/gl3523.md.

## Alternatives
| role | alternative | reason not chosen |
|---|---|---|
| hub | GL3510-OSY52, C7501408, QFN-64, $0.89 at 260: LCSC lists 2 downstream ports only | too few ports. Not enough for hub2 |
| hub | GL3523-OSY30 (QFN64) / -ONY30 (QFN48) | fewer SS ports on the QFN48 (no DS4); QFN64 has no PGANG shared LED pins but the same 4 DS. Use only if board area is tight. LCSC stock not checked |
| hub, Type-C | GL3523-OTYS3 (QFN76) native Type-C | would replace the upstream HD3SS3220 ($1.09). Not found on LCSC in this pass, datasheet S3 chapter p.17-25 not read. Worth a check if stock exists |
| host mux | HD3SS3212 + a TUSB320 (Interface_USB:TUSB320 symbol exists) | same cost, two chips; HD3SS3220 does both |
| front C mux | HD3SS3220 per port | CC logic collides with the PD source controller |
| SS ESD | TPD4E02B04DQAR, C106794, 0.25 pF, $0.19 (500), 8.8 V clamp at 5 A TLP, +-12 kV | **0 stock** at LCSC at read time; symbol exists (Power_Protection:TPD4E02B04DQA). Best part if it comes back in stock |
| SS ESD | PUSB3FR4Z-ES (C41429303, $0.044, 15510) | capacitance not on the LCSC page, clone of Nexperia PUSB3FC4 (0.29 pF) by name; use if the datasheet shows <= 0.5 pF |
| USB-C | HC-TYPE-CF-24P-RAT-Z, C2894903, 1718 in stock, $0.34 | right-angle SMT (taller body ~3.3 mm), not mid-mount; fits the PD-in port if the plate notch allows |
| USB-C | C168695 "TYPE-C 24POS SMD recessed", 1320, $0.85 | 7.95 mm long, dearer |
| USB-A | HC-ST-003-03-Z, C2845335 vertical flag | wrong orientation for the front edge |

## Missing KiCad symbols and footprints (project library needed)
Symbols: GL3523 (QFN76), HD3SS3220, HD3SS3212, USB-C 24P with hidden mating pins is OK with Connector:USB_C_Receptacle. USB-A 3.0 symbol exists (Connector:USB3_A). Footprints: HC-TYPE-C-24P-MM-1.4-5A-12 (24P mid-mount), HC-ST-003-01-Z (USB 3 right angle, 9P), TQFN-20 2.5x4.5 (HD3SS3212), WQFN-30 2.5x4.5 (HD3SS3220), DFN2510-10 if PUSB3FR4Z is used. QFN-76 9x9 exists with two EP sizes, confirm against the GL3523 package drawing (p.42-46 of the datasheet).

## SPICE
No hub model. Passive/ESD parameters for the circuit stage: SS AC cap 100 nF (75-200 nF allowed); ESD C_IO 0.5 pF typ (TPD4E05U06) per line, clamp 14 V at 2.5 A 8/20 us (C81353 LCSC page); mux Ron 5 ohm, -1.6 dB at 5 GHz (HD3SS3212); Ron/CIO of HD3SS3220 not read.

## Interface notes for other groups
- Orientation signal for HD3SS3212: the front-C PD source controller must output CC orientation (or the MCU reads CC and drives SEL). The power/PD group has to give a net `C1_ORIENT`, `C2_ORIENT` at 3.3 V.
- Host port: HD3SS3220 in UFP mode owns CC1/CC2 Rd on the host connector. R21 CC-Rp detection (eFuse ILIM 0.9/1.5/3.0 A) can read its current-mode status (datasheet to be confirmed) instead of a second CC network. The power group must not add its own Rd on the same CC pins.
- Hub PWREN/OVCUR pins (active low) go to the USB-A and C port limit switches of the power group (R6 over-current).

## Risks
1. Hub vendor layout guide (max SS trace length) is not public; the 165 mm upstream run is unchecked against Genesys. Get the "USB3.1 Hub Design Guide" from Genesys. The architecture estimate (3.7 dB total) is within a typical 20 dB channel.
2. C12 not solved by the hub choice (280 mA worst, 128 mA quiet).
3. GL3523 is single source (Genesys), the only 4-port 5 Gb/s hub found in stock at LCSC (12008). A second source needs a different footprint and pinout.
4. HD3SS3220 (556) and HD3SS3212 (336) are low stock for a volume build; the 5 pieces cost ~$5.
5. The best SS ESD array (TPD4E02B04) shows 0 stock; the chosen TPD4E05U06 sits at the 0.5 pF limit.
6. USB-A THT right-angle part height and the 11.1 mm depth: not verified (height not on the LCSC page). Mid-mount USB-C 1.4 mm sink needs a board cut-out edge verified from the drawing.
7. Several parts need project symbols/footprints (list above).
8. 1.2 V DC-DC component values (inductor, FB resistors) are not in the datasheet: get them from the design guide or a reference schematic before the circuit stage.
