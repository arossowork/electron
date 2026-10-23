# Parts list for stage 4 (single source; merged from notes/parts-{hub,power,audio,keyboard}.md, PM round 6)

Bridge = XMOS XU316-1024-QF60B-C24 (PM round 6, ordered, arrives ~2027-01-07). STM32F723VET6 route is dropped (kept below as a rejected alternative). Prices are USD at 100 boards (LCSC tier or distributor quote; [E] = estimate). Library: `libraries: [../../lib]` style project libs kb_audio, kb_hub, kb_power; `hwflow lib show` does not read project libs: use `hwflow check`. Layout blocks: `block list` (esp32c3_mini_decoupling, rev_guard_2pfet, tps61299_boost) covers nothing in this design (rev_guard_2pfet too small for 3.2 A at 20 V), no block is used. Datasheet facts: notes/datasheets/*.md.

## 1. Hub / USB data
| ref prefix, role | qty | mpn | lcsc | $ | symbol | footprint | SPICE | notes |
|---|---|---|---|---|---|---|---|---|
| U hub1, hub2 USB 3 hub | 2 | GL3523-OTY30 | C390630 | 1.54 | kb_hub:GL3523 | Package_DFN_QFN:QFN-76-1EP_9x9mm_P0.4mm_EP5.81x6.31mm | none (digital) | datasheet p14-16, 39, 42; pin order rotated 90 vs Genesys numbering, same cyclic order; 12008 in stock |
| Y hub crystal 25 MHz | 2 | X322525MOB4SI | C9006 | 0.067 | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | none | 2 x 18 pF load caps (CL 12 pF) |
| U host UFP + CC + SS mux | 1 | HD3SS3220IRNHR | C701817 | 1.09 | kb_hub:HD3SS3220 | kb_hub:WQFN-30-1EP_2.5x4.5mm_P0.4mm_EP1.2x3.2mm | none | GPIO mode, OUT1/OUT2 = host Rp level (556 in stock) |
| U front C SS orientation mux | 2 | HD3SS3212RKSR | C129320 | 1.03 | kb_hub:HD3SS3212 | kb_hub:VQFN-20-1EP_2.5x4.5mm_P0.5mm_EP1.0x3.0mm | none | 336 in stock |
| J USB-C 24P mid-mount (front C1, C2, back host, back PD in) | 4 | HC-TYPE-C-24P-MM-1.4-5A-12 | C5307768 | 0.6624 | Connector:USB_C_Receptacle | kb_hub:USB_C_Receptacle_HCTL_HC-TYPE-C-24P-MM-1.4 (PROVISIONAL JAE geometry) | none | release blocker B1 |
| J USB-A 3.0 right angle (front A1, A2) | 2 | HC-ST-003-01-Z | C2845331 | 0.1078 | Connector:USB3_A | kb_hub:USB3_A_HCTL_HC-ST-003-01-Z_Horizontal | none | height unverified (blocker B2) |
| D SS ESD (8) | 8 | TPD4E02B04DQAR (0.25 pF) | C106794, 0 stock: JLC global sourcing | 0.22 [E] (0.19 @500) | Power_Protection:TPD4E02B04DQA | Package_SON:USON-10_2.5x1.0mm_P0.5mm | none | in-stock fallback TPD4E05U06QDQARQ1 C81353, 0.47 |
| D D+/D-/SBU ESD (5) | 5 | PUSB3FR4Z-ES | C41429303 | 0.0437 | Power_Protection:TPD4E05U06DQA | Package_SON:USON-10_2.5x1.0mm_P0.5mm (DFN2510-10, pad map to check) | none | USB 2 only; not on CC |
| C SS AC caps / R termination | 16 / 2 | 100 nF 0402 X7R / 20 k 1 % 0402 | C1525 / basic | 0.003 | Device:C / Device:R | Capacitor_SMD:C_0402_1005Metric / Resistor_SMD:R_0402_1005Metric | none | GL3523 p15 |
| L, C hub 1.2 V DC-DC | 2 sets | inductor + caps per GL3523 | basic | 0.15 each [E] | Device:L, Device:C | project fp (stage 4) | none | in cost line |
Hub group at 100: about $11.7 (11.66: hubs 3.08, xtal 0.13, mux 3.15, connectors 2.87, ESD 1.98, DC-DC/passives 0.45).

## 2. Power / PD
| ref prefix, role | qty | mpn | lcsc | $ | symbol | footprint | SPICE | notes |
|---|---|---|---|---|---|---|---|---|
| U 5V_SYS buck (20 -> 5.1 V, 4.3 A) + 2 C-port bucks | 3 | LM61460AFSQRJRRQ1 | C2876600 | 1.51 | kb_power:LM61460AFS | kb_power:VQFN-HR-14_RJR0014A_4.0x3.5mm | fit: Vref 1.0 V, 400 kHz, Rds 41/21 mohm, HS limit 10.3 A | only 145 in stock (48 boards); L pads read by pixel scaling (blocker B3) |
| U C-port source controller | 2 | IP2736U_EF | C42437970 | 0.5634 | kb_power:IP2736U | kb_power:QFN-24-1EP_4x4mm_P0.5mm_EP2.7x2.7mm_IP2736U | none (behavioural source) | factory PDO list unconfirmed (blocker B4); 3944 in stock |
| U PD sink 20 V | 1 | CH224D | C3975094 | 0.2403 | kb_power:CH224D | Package_DFN_QFN:QFN-20-1EP_3x3mm_P0.4mm_EP1.65x1.65mm | none | VBUS abs max 24 V: 100 ohm + BZT52C22 + 1 uF; no PG pin |
| Q sink FETs back-to-back | 2 | AO4407A | C16072 | 0.2433 | Device:Q_PMOS | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | fit: Rds 12 mohm class | refs must start with M in design.yaml if simulated |
| D TVS VIN20 | 1 | SMBJ22A | C284010 | 0.0314 | Device:D_TVS | Diode_SMD:D_SMB | n/a | |
| D TVS host VBUS | 1 | SMBJ5.0A-TR | C130397 | 0.197 | Device:D_TVS | Diode_SMD:D_SMB | n/a | |
| U host eFuse | 1 | TPS25940ARVCR | C2653873 | 1.5094 | kb_power:TPS25940A | kb_power:WQFN-20_RVC0020A_4.0x3.0mm_P0.5mm_EP2.6x1.6mm | fit: Ron 42 mohm, IMON 52.3 uA/A | R_ILIM 88.7 k = 0.90/0.99/1.07 A (max 7 % over PM 1.0 A, B5) |
| U USB-A port switch + BC1.2 | 2 | TPS2546RTER | C117009 | 0.5212 | kb_power:TPS2546 | Package_DFN_QFN:Texas_RTE_WQFN-16-1EP_3x3mm_P0.5mm_EP1.2x0.8mm | fit: Ron 73 mohm | |
| U C-port bus-mode limit switch | 2 | TPS2553DBVR | C55266 | 0.3296 | kb_power:TPS2553 | Package_TO_SOT_SMD:SOT-23-6 | fit: Ron 85 mohm | OUT abs max 7 V: series P-FET Q_bus mandatory |
| U 3V3_D buck, 0V9_CORE buck (XU316 VDD) | 2 | TPS62933FDRLR | C5219272 | 0.7244 | Regulator_Switching:TPS62933F | Package_TO_SOT_SMD:SOT-583-8 | fit (behavioural) | 3 A rating vs 0.84 A core max; 3V3_D: 1.2 MHz, 2.2 uH; 0V9: RT open = 500 kHz, 4.7 uH, FB 10.0 k / 80.6 k (0.8993 V), EN = 1.8 V rail; sim core load step 0.2 -> 0.8 A at circuit stage |
| U +-5.5 V pre-regulator | 1 | TPS65131RGER | C87663 | 2.28 | Regulator_Switching:TPS65131RGE | Package_DFN_QFN:VQFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm_ThermalVias | fit | |
| L 8.2 uH (5V_SYS, 2 C ports) | 3 | FEXL0660A-8R2M | C5378547 | 1.09 | Device:L | project fp (6.6x6.4, stage 4) | n/a | 550 in stock; fallback FXL0630-8R2-M C167222 0.11 |
| L 2.2 uH (3V3_D) | 1 | FSA0630-2R2MT | C41384288 | 0.0518 | Device:L | project fp (7x6.6, stage 4) | n/a | |
| L 4.7 uH (+-5.5 V x2, 0V9 x1) | 3 | CKCS5040-4.7uH/M | C354606 | 0.0405 | Device:L | project fp (5x5, stage 4) | n/a | Isat 3.5 A vs 0V9 peak 1.0 A |
| C bulk 22 uF 25 V X5R 1206 | ~14 (incl. 3 x 22 uF at 0V9) | CL31A226KAHNNNE | C12891 | 0.0932 | Device:C | Capacitor_SMD:C_1206_3216Metric | n/a | 8 uF effective at 20 V |
| misc FETs, zener, R (Q_bus, PDMODE, ILIM switches, CC TVS) | lot | per notes/parts-power.md 3.2-3.4 | basic | ~1.5 [E] | Device:* | basic | n/a | |
Power group at 100: about $19.8 (includes the restored 0V9 buck 0.72 + 0.04 + 0.28 caps).

## 3. Audio
| ref prefix, role | qty | mpn | lcsc | $ | symbol | footprint | SPICE | notes |
|---|---|---|---|---|---|---|---|---|
| U bridge, USB 2.0 HS UAC2 | 1 | XU316-1024-QF60B-C24 (3.3 V IO, grade 24) | global sourcing (DigiKey EU, ordered, due 2027-01-07) | 6.8 [E from EUR 7.09@10, 5.31@1248] | kb_audio:XU316-1024-QF60B (65 pins, verified p5-7) | kb_audio:QFN-60-5EP_7x7mm_P0.4mm_XU316 (new, from p35) | none; supply loads: 236 mA @0.9 V, 31.5 mA @1.8 V, 5 mA @3.3 V typ | datasheet notes/datasheets/xu316-1024-qf60b.md; QF60A = 1.8 V IO is the wrong variant |
| U bridge boot flash (QSPI) | 1 | W25Q64JVSSIM (QE bit set at factory) | not found at LCSC; IQ variant C179171 (QE = 0, 200 in stock) as fallback | 1.6 [E] (IQ 1.40) | Memory_Flash:W25Q32JVSS | Package_SO:SOIC-8_5.275x5.275mm_P1.27mm (208 mil; symbol default 5.23 is wrong width) | none | CS pull-up 10 k to 3V3; XMOS tools must support it (blocker B7) |
| Y bridge crystal 24 MHz, CL 12 pF | 1 | X322524MOB4SI (YXC) | C70590 | 0.058 | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | none | 22 pF caps on XIN/XOUT, 680 ohm in series with XOUT (XMOS p15); ESR not read (B8) |
| U 1.8 V LDO (VDDIOB18 + USB_VDD18) | 1 | AP2112K-1.8TRG1 | C176944 (6940 in stock) | 0.09 | Regulator_Linear:AP2112K-1.8 | Package_TO_SOT_SMD:SOT-23-5 | none (ideal source) | 600 mA, dropout 0.25 V at 0.6 A; load 32/39 mA, 48/59 mW dissipation; EN to VIN; 1 uF + 100 nF each pin group; alternatives NCP705MT18TCG C892699 0.58 (WDFN-6), BCT2193EJR18-TR C2887790 |
| Y/X audio master clock 22.5792 / 24.576 MHz | 2 | SIT1602BC-23-33E-24.576000E / SiT8008BI-31-33E-22.579200 (C817260) | global / C817260 | 1.2 | Oscillator:ASE-xxxMHz | Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm | none | one enabled at a time (OE), 3/4 mA; separate from the 24 MHz XU316 crystal; MCLK in on X0D35 (pin 41) per XMOS ref |
| U DAC | 1 | ES9039Q2M | global sourcing | 9 [E] | kb_audio:ES9039Q2M (33 pins) | Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.3x3.3mm | behavioural: 0.60 uV A-wtd at 2 Vrms diff, Rdac 390 ohm, 7.5 mApp/pin | datasheet v0.2.3 read |
| U ADC (mic) | 1 | ES9822PRO | global sourcing | 7 [E] | kb_audio:ES9822PRO (pins 24-41 unverified) | Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.8x3.8mm | behavioural: 125 dB [S] | blocker B6 |
| U I/V, diff-SE, mic preamp | 3 | OPA1612AIDR | C94590 | 0.93 | Amplifier_Operational:OPA1612AxD | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | fit lib/spice/audio_fit.lib OPA1612_FIT (en 1.1 nV/rtHz, GBW 40 MHz) | 26127 in stock |
| U headphone amp | 1 | OPA1622IDRCR | global sourcing (0 at TI store) | 2.5 [E] | Amplifier_Audio:OPA1622 | Package_SON:Texas_S-PVSON-N10 | fit OPA1622_FIT (en 2.8 nV/rtHz, GBW 32 MHz) | |
| U LDO 3V3_A and 4.5 V | 2 | LT3045EMSE#TRPBF | C666577 | 5.77 | Regulator_Linear:LT3045xMSE | Package_SO:MSOP-12-1EP_3x4mm_P0.65mm_EP1.65x2.85mm | behavioural | |
| U LDO -4.5 V | 1 | LT3094EMSE#TRPBF | C670494 | 5.48 | Regulator_Linear:LT3094xMSE | same MSOP-12 | behavioural | pin map differs from LT3045 |
| U LDO +4.5 V op-amps, 2V5_BIAS | 1 | ADP7118ACPZN-R7 | C579266 | 1.9 | Regulator_Linear:ADP7118ACPZN | Package_CSP:LFCSP-6-1EP_2x2mm_P0.65mm_EP1x1.6mm | behavioural | |
| J jacks HP + mic (plug-detect switch) | 2 | SJ-3524-SMT-TR | global (DigiKey 32,661) | 1.0 [E] | Connector_Audio:AudioJack3_SwitchT | Connector_Audio:Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal | none | |
| D audio ESD | 8 | TPD1E10B06DPYT | C1847652 | 0.24 | Power_Protection:TPD1E05U06DPY | Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm | none | |
| FB 3V3_A ferrite | 1 | BLM18PG121SN1D | C14709 | 0.015 | Device:FerriteBead | Inductor_SMD:L_0603_1608Metric | none | |
| FB XU316 PLL_AVDD (600 ohm @100 MHz, DCR < 1 ohm) | 1 | BKH1005LM601-T (Taiyo Yuden, per XMOS p26) or BLM15AG601SN1D | LCSC code not checked | 0.02 [E] | Device:FerriteBead | Inductor_SMD:L_0402_1005Metric | none | 1 uF MLCC at the pin |
| C/R XU316 decoupling (8 x 100 nF VDD, 100 nF per VDDIO, 10 uF per rail), audio precision R/C | lot | 0402 100 nF, 0603 10 uF, thin-film 0.1 % | basic | 4.0 [E] | Device:C / Device:R | 0402 / 0603 | none | |
Audio group at 100: about $59.1 (XU316 group incl. flash, crystal, LDO, ferrite: 8.65).

## 4. Keyboard / MCU
| ref prefix, role | qty | mpn | lcsc | $ | symbol | footprint | SPICE | notes |
|---|---|---|---|---|---|---|---|---|
| U MCU | 1 | RP2040 | C2040 | 0.99 | MCU_RaspberryPi:RP2040 | Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm | none | 12.0 typ / 21 max mA at 3.3 V (datasheet Table 637 p623) |
| U QSPI flash | 1 | W25Q16JVSNIQ | C2456211 | 0.80 | Memory_Flash:W25Q128JVS (same pinout) | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm | none | |
| Y MCU crystal 12 MHz, CL 12 pF | 1 | CJ13-120001210B20 | C2875246 | 0.08 | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | none | 18 pF NP0, 1 k in XOUT |
| D matrix diode | 104 | 1N4148W | C81598 | 0.012 | Diode:1N4148W | Diode_SMD:D_SOD-123 | fit (is 2.5e-9 class) | back side |
| SW hot-swap socket, PM consigned | 104 | Kailh CPG151101S11 | C2803348 (consigned) | 0.075 | Switch:SW_Push | kb_keys:SW_MX_Hotswap_Kailh_CPG151101S11_1u_Back | none | pad geometry from community footprints (B9) |
| H stabilizer cut-outs | n | mechanical | - | 0 | Mechanical:MountingHole | kb_keys:STAB_MX_2u_2.25u_2.75u, kb_keys:STAB_MX_6.25u | none | pad-less: check the placer accepts them |
| SW volume encoder with push | 1 | ALPS EC11E153440D | C370992 | 1.22 | Device:RotaryEncoder_Switch | Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm | none | shaft/height unverified (B10); alt EC11L1525G01 C2991196 |
| C/R MCU decoupling and pull-ups | lot | 0402 | basic | 0.4 [E] | Device:C / R | 0402 | none | |
Keyboard group at 100: $4.7 on-board + $7.8 consigned sockets = $12.5.

## 5. Cost per board at 100 units
| item | $ | basis |
|---|---|---|
| hub / USB data | 11.7 | section 1 |
| power / PD | 19.8 | section 2 |
| audio (incl. XU316 bridge group 8.65) | 59.1 | section 3 |
| keyboard (4.7 + consigned sockets 7.8) | 12.5 | section 4 |
| general passives, test points not itemised | 3.0 | [E] |
| parts subtotal | 106.1 | |
| 4-layer PCB 440 x 152 mm (668 cm2) | 11 (range 6-18) | [E] ~$0.017/cm2; conflicts.md C9 had 5.5 for 440 x 155: needs a JLC quote |
| PCBA (standard, two sides, ~2100 joints + order fees) | 9 (range 7-11) | [E] C9 had 6.5; back-side sockets/diodes add a second stencil |
| **total** | **126 (range 119-135)** | |
Against R17 <= $125: central estimate $1 over, range straddles the limit: not yet demonstrated (needs the JLC quote and a global-sourcing fee check: XU316, DAC, ADC, OPA1622, jacks, TPD4E02B04 are non-LCSC, typically +$1-3 per board). R25 should <= $110: missed by about $16 (the PM chose the studio tier). Round 6 bridge delta vs iteration 3 (STM32 9.4 incl. crystal): XU316 group 8.65 + 0V9 buck chain 1.0 = 9.7, i.e. +0.3.
Cost cut candidates if the PM wants R17 with margin: ES9038Q2M for the DAC (-1 to -3, loses 1 dB), ADP7118 for 3V3_A (-3.9, noise risk), ADP7182 for the -4.5 V rail (-1.8), drop the second audio XO (-1.2).

## 6. Bus-mode current at 5 V (R19: internal <= 600 mA normal, heavy case <= 660 mA)
XU316 group (bridge + flash + crystal + 1.8 V LDO + 0.9 V and 3.3 V share), datasheet Fig 36 p32, rails at 5 V through the buck chain:
| rail | load typ / max | source | at 5 V typ / worst |
|---|---|---|---|
| 0.9 V core: VDD 225 / 830 mA (grade 24), PLL_AVDD 5, USB HS VDD 6 / 9 | 236 / 844 mA = 0.21 / 0.76 W | p32 (budget figures: "highly application dependent") | 54 / 179 (buck eff 78 % / 85 % [E]) |
| 1.8 V: USB_VDD18 30 / 36, VDDIOB18 + XTAL ~1.5 / 3 | 31.5 / 39 mA, via LDO from 3V3 | p32 | counted in 3.3 V |
| 3.3 V: USB_VDD33 0.8 / 1, VDDIO I2S/GPIO 3 / 8, flash idle (boot read 25 mA transient) 1 / 6 | 5 / 15 mA + 1.8 V LDO load = 36.5 / 54 mA = 0.12 / 0.18 W | p32, W25Q64JV [M] | 33 / 47 (buck eff 72 % / 76 % [E]) |
| **XU316 group** | | | **88 typ / 226 worst (datasheet max, all cores flat out); 123 "design worst" (1.4 x typ)** |
Previous bridge estimate 79 / 104; STM32 route 44 / 56. The 3V3_D and 0V9 TPS62933F quiescent (0.125 mA each) is inside the efficiency figures.
| group | typ | worst | source |
|---|---|---|---|
| hub / USB data (2 x GL3523 + muxes) | 265 | 282 | GL3523 p39: 264 config, 280 R/W, + ~1-2 mux |
| power overhead (eFuse, bucks Iq, TPS2546 x2, TPS2553 x2) | 1.4 | 3.4 | parts-power.md |
| keyboard MCU + flash | 11 | 20 | RP2040 Table 637 (9 / 16 at 5 V) + flash |
| audio excl. bridge, headphone only (DAC 24/30, op-amps 69/87, LDO Iq 8/9, XO 3.5/5, HP 13/41) | 118 | 172 | parts-audio.md item 5 minus bridge |
| audio add for mic recording (ADC 47 / 57) | +47 | +57 | |
| XU316 group | 88 | 226 (123 design) | above |
| **total, headphone only** | **483** | 703 (600 design) | |
| **total, mic recording, 4 SS devices (heavy case)** | **530** | **760 datasheet max / 657 with XU316 at 123** | |
Normal case (typ) 483-530 mA passes R19 (<= 600, margin 70 mA with the mic). Heavy case (worst) passes <= 660 only if the XU316 core current stays near 1.4 x typ (657, margin 3 mA, effectively zero); with the datasheet maximum (830 mA core) it is 760 mA, 100 mA over. The datasheet max is an all-cores, worst-corner figure; real UAC2 firmware runs a few cores. Mitigation to record: core clock 300 MHz in bus mode (core dynamic power about halves, about -25 mA at 5 V), measure on the first article. If measured above 123, go back to the PM (R19 heavy case or the bridge).

## 7. XU316 rails and support (summary; details in notes/datasheets/xu316-1024-qf60b.md)
- 0.9 V +-5 % (0.855-0.945 V) VDD pins 4, 12, 19, 27, 34, 42, 49, 57 + paddles 61-64 and PLL_AVDD pin 22 (via FB 600 ohm + 1 uF). TPS62933F fits: 3 A vs 0.84 A max. At 500 kHz, ripple 0.31 A p-p with 4.7 uH, 0.9 V on-time 360 ns (no min-on-time risk). Regulation budget: Vref +-1 % + two 1 % resistors ~ +-3 %, so only +-2 % remains for ripple and load step: use 0.5 % FB resistors and check the 0.2 -> 0.8 A step in the circuit stage (not simulated here).
- 1.8 V: AP2112K-1.8 from 3V3_D: 32/39 mA, 48/59 mW. An LDO costs about 9 mA at 5 V more than a buck (1.8 V would need a third buck with FCCM light-load loss), chosen for noise on the USB analog supply and area.
- 3.3 V: 3V3_D, VDDIOL pin 8, VDDIOR 38, VDDIOT 52, USB_VDD33 30; 100 nF per pin, 10 uF per rail.
- Sequencing: 3V3_D first (also the LDO input), 1.8 V next, 0.9 V buck EN = 1.8 V rail, so VDD is never on with VDDIOB18 off and VDDIOL/R/T are valid before reset release (p26); no supervisor, RST_N left unconnected (internal POR), a 1.8 V open-drain from the RP2040 optional.
- Boot QSPI flash W25Q64JVSSIM on X0D01/04/05/06/07/10 (3.3 V), CS pull-up 10 k. 24 MHz crystal CL 12 pF on XIN/XOUT (1.8 V domain) with 22 pF + 680 ohm. Audio MCLK oscillators are separate (X0D35 = MCLK in).
- XMOS items the audio group must keep: JTAG/xSYS2 0.05" header or another way to program flash/OTP (checklist G.6).

## 8. Alternatives (rejected / fallback)
- STM32F723VET6 + TinyUSB UAC2 (rejected in round 6; 44/56 mA, no 0.9/1.8 V rails, no flash, $9.2, firmware project, LQFP-100, not at JLC): notes/parts-audio.md iteration 3. RP2040 FS UAC1 (24/96 only). STM32H723 + USB3300. CM6631A/SA9227 (no availability).
- 1.8 V LDO alternates: NCP705MT18TCG, BCT2193EJR18-TR. Flash alternates: W25Q32JVSSIM, IS25LP032/064 (check xflash list).

## 9. Release blockers (need a human check of a vendor drawing or datasheet)
- B1 HCTL USB-C 24P footprint is the provisional JAE geometry: compare with the HCTL drawing (PDF not retrievable here).
- B2 HC-ST-003-01-Z USB-A 3.0 height (R24 stack 11.1 mm) and shield holes: vendor drawing.
- B3 LM61460 RJR0014A L-pads (pixel-scaled, ~0.03 mm) and thermal vias; IP2736U and TPS25940A thermal via patterns.
- B4 IP2736U_EF: factory PDO list, current limit, CC behaviour at VIN = 0; get a sample or Injoinic table.
- B5 TPS25940A max trip 1.07 A vs the PM's 1.0 A max: PM acceptance.
- B6 ES9822PRO datasheet (pins 24-41, full-scale input, Zin, land pattern) not readable; ES9039Q2M done.
- B7 XU316: (a) paddle centre offsets +-2.5 mm of footprint kb_audio:QFN-60-5EP_7x7mm_P0.4mm_XU316 are scaled from the XMOS drawing (p35), not dimensioned; check against the drawing at release and add the 4 x 4 ground vias; (b) the chosen flash against the XMOS tools' supported list and QE bit (W25Q64JVSSIM; LCSC code not found); (c) XU316 core current measured on the first article against the 123 mA design worst.
- B8 24 MHz crystal C70590: ESR and drive level not read (XMOS list: max ESR 30-60 ohm at CL 12 pF).
- B9 Kailh CPG151101S11 pad geometry from community footprints (drawing unreachable).
- B10 Alps EC11E153440D shaft length/height and pad grid.
- B11 JLC quote for the 440 x 152 4-layer PCB and PCBA; global-sourcing fees (cost is within +-$9 of the R17 limit).
- B12 OPA1622 and ES9039Q2M/ES9822PRO distributor stock; OPA1612/OPA1622 vendor SPICE not unpacked (fit models in use).

## 10. Risks
Single source: XU316 (XMOS, lead 12 weeks, only 416 due at DigiKey EU; PM has ordered), IP2736U (Injoinic), ES9039Q2M/ES9822PRO (ESS), OPA1622. Low stock: LM61460 (145), FEXL0660A (550), TPD4E02B04 (0 at LCSC). Missing vendor SPICE: all parts use fit or behavioural models. XU316 current spread is the biggest bus-mode risk (section 6).
