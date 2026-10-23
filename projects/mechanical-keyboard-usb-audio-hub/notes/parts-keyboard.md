# Parts: keyboard / MCU / UI group (stage 3)

## Iteration 2 summary (2026-10-22). Where this section conflicts with the iteration 1 text below (GPIO budget, MCU current, encoder, crystal, hot-swap footprint), this section wins.
| role | mpn | lcsc | price | stock | datasheet | lib_id | fp_id |
|---|---|---|---|---|---|---|---|
| keyboard MCU | RP2040 | C2040 | 0.99 | 45.7k | notes/datasheets/rp2040.md (p.216, 622-624) | MCU_RaspberryPi:RP2040 | Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm |
| QSPI flash | W25Q16JVSNIQ | C2456211 | 0.80 (100) | 35k | Winbond | Memory_Flash:W25Q128JVS (same pinout) | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm |
| MCU crystal 12 MHz | CJ13-120001210B20 (JSCJ), CL 12 pF, +-10 ppm | C2875246 | 0.10 (5), 0.06 (3000) | 13.9k | LCSC page; RP2040 wants CL 10 pF / ESR <= 50 ohm | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm |
| matrix diode (x104) | 1N4148W | C81598 | ~0.01 | JLC | Semtech | Diode:1N4148W | Diode_SMD:D_SOD-123 |
| hot-swap socket (x104), PM consigned | Kailh CPG151101S11 | C2803348 (also -1 C5184526, -16 C5156480) | 0.06-0.09 | PM buys | notes/datasheets/connectors-hctl-kailh.md | Switch:SW_Push | kb_keys:SW_MX_Hotswap_Kailh_CPG151101S11_1u_Back (iteration 3: matched to kiswitch SW_Hotswap_Kailh_MX_1.00u: pad 2 x 5.842, holes 3.05 / 1.75; Kailh PDF unreachable; place with side: back) |
| stabilizer holes 2u / 2.25u / 2.75u | mechanical | - | 0 | - | Cherry MX stabilizer cut-out | Mechanical:MountingHole (0 pins) | kb_keys:STAB_MX_2u_2.25u_2.75u (new) |
| stabilizer holes 6.25u | mechanical | - | 0 | - | same | Mechanical:MountingHole | kb_keys:STAB_MX_6.25u (new) |
| volume encoder, vertical with push | ALPS EC11E153440D | C370992 | 1.22 | 805 (iteration 1 read; LCSC page shows none) | LCSC page (5 V, 10 mA, push switch, straight) | Device:RotaryEncoder_Switch | Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm (library, Alps-derived: A/C/B at x=0, y 0/2.5/5; S2/S1 at x=14.5; MP tabs at (7.5,-3.1), (7.5,8.1); the same grid for all EC11E vertical push models; Alps PDF 403, shaft length of the 40D suffix and body height unverified) |
| encoder alternative | EC11L1525G01 | C2991196 | 1.13 | not shown | LCSC | same | same |

### Pin budget: 12 rows x 11 columns, RP2040 30 GPIO
- Matrix 12 x 11 = 132 slots for 104 keys, 23 GPIO. Fold rule (no ghost conflicts: each key has a unique row/column pair): the physical board is split at 11 u from the left edge. Keys with their left edge left of the split go to logical rows 0-5 (physical row 0-5), the others to logical rows 6-11 (physical row + 6). The column is the index of the key within its half-row (0-10). Worst half-row counts (checked by hand on the ANSI 104 layout: Tab/Q-row left 11 keys, caps row left 11 keys, number row left 11 keys, right half max 10 keys): every half-row has at most 11 keys. The total trace length is about the same as the 6 x 21 matrix of the architecture (12 short row nets of ~210 mm and 11 column nets of ~460 mm, against 6 x 430 mm and 21 x 120 mm).
- GPIO map: rows GPIO0-11, columns GPIO12-22, encoder A GPIO23, B GPIO24, push GPIO25, jack detect GPIO26, host Rp OUT1 GPIO27, host Rp OUT2 GPIO28 (HD3SS3220 GPIO mode, see hub note), spare GPIO29. Total 23 + 3 + 3 + 1 = 30; **free after matrix and encoder: 4** (jack detect, Rp level, spare; GPIO26-29 are ADC pins, usable as digital).
- 8 x 14 (22 GPIO) was rejected: it needs a 2-D fold of the 6 physical rows with long mixed nets. 6 x 18 (24 GPIO) leaves 3 spare but cannot hold the numpad columns without index folding. 6 x 21 (27 GPIO) leaves 0.

### RP2040 current (datasheet RP-008371-DS-1 Table 637 p.623, notes/datasheets/rp2040.md)
48 MHz with USB (BOOTSEL active): DVDD 9.4 + IOVDD 1.2 + USB_VDD 1.4 = 12.0 mA typ, 21.0 mA max, from the 3.3 V rail; idle with USB connected 10.4 typ / 19.2 max. At 5 V through the 88 % buck: 9 mA typ, 16 mA max, plus flash 2-4 mA. This replaces the 6-8 mA class estimate of iteration 1: for C12 the keyboard MCU costs about 9-13 mA typ at 5 V (the architecture assumed 25 mA typ / 40 mA peak, so the saving is 12-16 mA typ and ~24 mA at the peak, only if firmware holds clk_sys at 48 MHz).

### 12 MHz crystal load capacitance
RP2040 reference: ABM8-272-T3, CL 10 pF, ESR <= 50 ohm, 15 pF caps, 1 k damping resistor in XOUT (datasheet p.216-217). The CJ13 has CL = 12 pF: use 18 pF NP0 0402 on XIN and XOUT (9 pF + ~3 pF stray = 12 pF), keep the 1 k. ESR of the CJ13 not read (confirm <= 50 ohm); frequency stability over temperature to be tested as the datasheet requires for non-reference crystals.

### Hot-swap, stabilizer and library approach (fewest files)
One 1u hot-swap footprint (kb_keys) for all keys, drawn mirrored for side: back, with the MX centre post 4.0, plate pins 1.7 and the 3.0 mm holes; stabilizer holes live in two mechanical footprints (2u/2.25u/2.75u share +-11.9 mm, 6.25u is +-50 mm; same numbers as the KiCad Cherry MX footprints), placed on the key centre with the same rotation, flagged board_only/exclude_from_bom. Numpad vertical 2u keys use the 2u stabilizer rotated 90 degrees. Pad geometry of the socket comes from community footprints (Kailh drawing unreachable, certificate expired); see connectors-hctl-kailh.md. The diode stays a separate part (D_SOD-123 back side).

### Risks added in iteration 2
- Socket pad geometry not from the Kailh drawing: a 0.25 mm error is possible (LCSC EasyEDA pads sit 0.25 mm further out).
- Stabilizer footprints have no pads: check that the placer accepts pad-less board_only parts; if not, add them as mounting-hole parts in design.yaml.
- The encoder suffix EC11E153440D: shaft length and detent/pulse count not verified; the 20 mm library footprint family is for the 20 mm shaft models: confirm pad grid and height against the Alps drawing (front-strip budget 7.0 mm).
- Hot-swap sockets on the back and the 1u footprint are drawn mirrored (side: back); the layout agent must place them with side: back.

Data quality warning: `hwflow parts` text/lcsc filters do not work (server ignores `search`; `microcontrollers` crashes on a None price;
`crystals` gives HTTP 404). Prices and stocks below come from LCSC/JLCPCB pages via web search. Items marked (unverified) must be
re-checked in the circuit/BOM stage. The RP2040 datasheet PDF could not be converted to text in this session (pdftotext was not
permitted), so the RP2040 supply currents are class estimates, not datasheet table values.

## Architecture facts used
- Matrix 6 rows x 21 columns, 104 keys (architecture sec. 7 and block diagram). MCU on hub2 DS3 (full speed). Supply: 3V3_D buck.
- Architecture sec. 3/7: no MCU control lines exist. ILIM select is hardware from CC Rp, PD status/port limits are in the power
  controllers. GPIO demand from the MCU: encoder A, B, push only.
- Encoder: knob facing the user inside the front strip (R28, R24), architecture depth budget 7.0 mm encoder body.

## GPIO budget (RP2040: 30 GPIO, GPIO0-29; QSPI, USB, SWD, RUN, XIN/XOUT are separate pins)
| use | GPIO |
|---|---|
| rows (6) | 6 |
| columns (21) | 21 |
| encoder A, B, push | 3 |
| total | 30 of 30 |

No shift register needed. Zero spare GPIO. Options if one is needed later: 6 x 18 matrix (QMK full-size ANSI layouts commonly share
columns; frees 3, but changes the row/column net map and the floorplan assumption) or a 74HC595 on the columns. Recommendation:
keep 6 x 21 as in the architecture, accept 0 spare. GPIO23-25 and 29 are free on the bare chip (they are used for power functions only on
the Pico board). Use diode direction col2row (diode cathode on the row, anode on the key) - decision for firmware config, either works.

## Chosen parts
| ref group | role | mpn | LCSC | price USD | KiCad symbol | footprint | notes |
|---|---|---|---|---|---|---|---|
| U_MCU | keyboard MCU | RP2040 | C2040 | 0.99 (45.7k in stock, JLC extended) | MCU_RaspberryPi:RP2040 | Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm (symbol default; pads checked with lib show, pin 57 = EP GND) | Cortex-M0+ 2 core, USB 1.1 FS PHY, QMK and ZMK(Zephyr) support, 133 MHz max |
| U_FLASH | QSPI flash | W25Q16JVSNIQ (2 MB) | C2456211 | 0.80 @100 (35k stock) | Memory_Flash:W25Q128JVS (same W25Q pinout, pins 1 CS,2 DO,3 IO2,4 GND,5 DI,6 CLK,7 IO3,8 VCC) | Package_SO:SOIC-8_3.9x4.9mm_P1.27mm (150 mil body; the symbol default is the 208 mil one, override) | 2 MB is plenty for QMK; active read about 4 mA, standby about 1 uA (class values, W25Q16JV) |
| Y_MCU | 12 MHz crystal | JSCJ CJ13-120001210B20 (SMD3225-4P) | C2875246 | 0.10 @5, 0.06 @3000 (13.9k stock) | Device:Crystal_GND24 | Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm | load capacitance (CL) not verified. Pico reference: 12 MHz, caps 15 pF to GND, 1 kOhm series in XOUT. Pick CL so that CL = (C1 x C2)/(C1+C2) + Cstray, circuit stage to confirm CL against the part datasheet |
| D1..D104 | matrix diode | 1N4148W (Semtech) | C81598 | about 0.01 each (price not verified, 1N4148W class $0.008-0.015 at qty 100+, so about $1.0-1.6 per board) | Diode:1N4148W (pin 1 K, pin 2 A) | Diode_SMD:D_SOD-123 (pad 1 K, pad 2 A: matches) | 75 V, 150 mA, Vf 1 V @50 mA, trr 4 ns, Ir 1 uA @75 V, 400 mW |
| SW1..SW104 | MX hot-swap socket | Kailh CPG151101S11-16 (cheapest), alternatives CPG151101S11 / -1 | C41430893 (HanElectricity listing of the -16 part, up to 131k stock claimed, unverified) ; C5156480 (-16, stock 0) ; C2803348 (0 stock) ; C5184526 (-1, 385 stock) | $0.0898 @50, $0.0793 @150, $0.0714 @500 (C2803348 tiers); about $0.07-0.09 each at 10 400 parts: $7-9.5 per board | Switch:SW_Push (generic, 2 pins) | NO hot-swap footprint in the installed KiCad lib: project footprint needed (see below) | 2 pins, SMD, back side, MSL 5, JLC standard PCBA only |
| SW_KEY | MX switch mechanical footprint | - | - | - | - | Button_Switch_Keyboard:SW_Cherry_MX_{1.00,1.25,1.50,1.75,2.00,2.25,2.75,6.25}u_PCB (2.00u also _Vertical_). 1.00u pads: 1 at (0,0), 2 at (-6.35,2.54); NPTH 4.0 at (-2.54,5.08), 1.7 at (-7.62,5.08) and (2.54,5.08) | Stabiliser holes included in the 2u+ variants (verify with lib pads at footprint stage). Numpad 2u keys (0, +, Enter) use 2.00u / 2.00u_Vertical |
| ENC1 | rotary encoder with push | candidate: Bourns PEC12R-2217F-S0024 (horizontal shaft; not found in stock on LCSC). Found: Bourns PEC12R-2220F-S0024 C49673168 ($1.57 @75, 0 stock, vertical?) ; Alps EC11E153440D C370992 ($1.22, 805 stock, vertical) ; EC11L1525G01 C2991196 ($1.13) ; EC111012000Y C2687280 ($0.92) | see left | $0.92-1.6 | Device:RotaryEncoder_Switch (pins A, B, C, S1, S2; C = common) | Rotary_Encoder:RotaryEncoder_Bourns_Horizontal_PEC12R-2x17F-Sxxxx / ..._PEC09-2xxxF-Sxxxx (pads A,B,C,S1,S2 match symbol; PEC09-S bbox -9..5 x -7.5..2.5) ; vertical Alps EC11E-Switch_Vertical_H20mm | KEY PROBLEM: R28 needs the shaft facing the user. That needs a horizontal (right-angle) encoder. All LCSC hits are vertical or zero stock. Open: find an in-stock horizontal part (e.g. a PEC09/PEC12R horizontal on LCSC, or a vertical encoder plus a right-angle mount). Footprint exists for Bourns horizontal only |
| C_DEC | RP2040 decoupling | 100 nF 0402 x 10-ish + 1 uF (DVDD/VREG_VOUT) + 1 uF VREG_IN + 10 uF IOVDD... | see circuit stage (use JLC basic caps) | - | - | - | Pico minimal design: 100 nF per IOVDD pin (6x), 100 nF USB_VDD, 100 nF ADC_AVDD, 1 uF + 100 nF VREG_VOUT, 1 uF VREG_IN |
| R_USB | USB series resistors | 27 Ohm 1 % 0402 x2 | JLC basic resistor | 0.002 | Device:R | 0402 | Pico reference. RP2040 has no on-chip termination, so 27 Ohm each in D+ and D- |
| R_FL | RP2040 QSPI_SS pull-up / BOOT button | 1 kOhm in series with BOOT switch to QSPI_SS | basic | - | - | - | BOOTSEL: SW to GND on QSPI_SS via 1 k |
| SW_BOOT, SW_RUN | tactile reset / boot | SW_SPST_PTS810 / TL3342 class | JLC basic tact switch (e.g. TS-1187A class) | about 0.03 | Switch:SW_Push | Button_Switch_SMD:SW_SPST_PTS810 or SW_SPST_TL3342 | RUN to GND with reset switch (RUN has an internal pull-up); QSPI_SS via 1 k to GND with boot switch |
| J_SWD | SWD | 3-pad (SWCLK, SWDIO, GND) as test points | - | 0 | Connector:TestPoint (or Connector_Generic:Conn_01x03) | TestPoint:TestPoint_Pad_D1.0mm x3, or Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical (no part cost) | RP2040 pins 24 SWCLK, 25 SWD |

## Missing KiCad items (project library needed)
1. Kailh MX hot-swap socket footprint (CPG151101S11): none in the installed library (`lib fp hotswap/Kailh/SW_Hotswap` gives nothing).
   Needs a project footprint on the back copper next to the Cherry MX 1.00u footprint: 2 SMD pads under the switch (about
   2.6 x 2.2 mm each) and the through holes of the switch pins. Pad geometry from the Kailh datasheet (CPG151101S11) - not downloaded
   in this session; to fetch at the layout-library step. The socket pins carry the keys 1 and 2 (matrix col / diode node).
2. Symbol: Switch:SW_Push is enough for socket and key (2 pins).
3. Horizontal encoder LCSC part (see ENC1).

## MCU current in bus mode (conflict C12)
Hub/bridge/audio are not in this group. Estimate for the MCU group only (RP2040 + flash, 3.3 V; the architecture uses 25 mA typ /
40 mA peak at 5 V-equivalent, i.e. 41 mA... see below). Datasheet table not read (see warning). Class values from the RP2040
datasheet power-vs-frequency data and the W25Q16JV datasheet:
- 125 MHz, both cores busy, USB: about 22-25 mA from 3.3 V (core ~ 20 mA + PHY ~ 2 mA + flash 4 mA when executing from XIP).
- 48 MHz sysclk (USB needs only the 48 MHz clk_usb from PLL_USB), one core, sleeping (WFI) between scans: about 8-10 mA from 3.3 V, i.e.
  about 5-6 mA at 5 V-equivalent after the 88 % buck.
- Scan rate 1 kHz, matrix drive 0.5 mA average: negligible.
So the MCU can be lowered from the architecture's 25 mA to about 6-8 mA at 5 V-equivalent. Saves about 17-19 mA typ out of the 12 mA
typ miss in C12 (R19 shortfall 12 mA typ, 51 mA peak). Peak 40 mA to about 12 mA with the 48 MHz clock cap. Firmware has to enforce
the lower clock (QMK config, `PICO_FLASH`... clock set via `SYS_CLK`). These are estimates, to be checked with the real datasheet table
(RP2040 datasheet section 2.15 "Power consumption" and the W25Q16JV electrical table) at the circuit stage.

## Matrix noise into the audio (R35, 5 mm keep-out)
RP2040 pads have software-programmable drive strength (2, 4, 8, 12 mA) and a slew rate bit (slow / fast) per GPIO
(PADS_BANK0 GPIOx register). Settings: 2 mA, slow slew on all 27 matrix pins and the encoder pins. Matrix traces are DC-quiet when
idle: rows driven only in the scan window, columns with pull-ups. Row-by-row scan at 1 kHz: edge rise time with 2 mA / slow slew into
about 30 pF per column is a few ns to tens of ns, which is slow compared with a 12 MHz crystal. Put a 100 Ohm series on each row drive in
the matrix block if the audio result needs it (costs 6 resistors). Keep the matrix >= 5 mm from the audio region (floorplan has 5.5 mm).

## Per-board cost (group, 100 qty)
| item | qty | USD |
|---|---|---|
| hot-swap sockets | 104 | 7.4-9.3 (0.0714-0.0898 each, stock risk) |
| diodes | 104 | about 1.0-1.6 |
| RP2040 | 1 | 0.99 |
| flash | 1 | 0.80 |
| crystal | 1 | 0.10 |
| encoder | 1 | 0.9-1.6 |
| R/C/switch/test points | about 20 | about 0.40 |
| total | | about 11.6-14.8 |
Switch footprints and keys cost nothing (the switches are not part of the electronics cost). JLC PCBA has a per-component fee
for extended parts (RP2040, flash, crystal, sockets) and a placement fee per component; not included here.

## Alternatives
- MCU: STM32F072CBU6 (USB FS crystal-less, 48 MHz, 37 GPIO in UFQFPN48, no external flash, no ZMK/QMK maturity as high as RP2040 on
  QMK, ChibiOS support yes). Reason not chosen: price about 2x RP2040 and 3.3 V 48 MHz run current about 10-15 mA, same as RP2040
  at 48 MHz. CH32V203 (WCH, $0.6) USB FS but QMK port not mature. RP2040 wins on 30 GPIO, price and firmware support. STM32F411 (100 MHz
  Cortex-M4F, UFQFPN48/LQFP64): faster than needed, higher current.
- Socket: Gateron hot-swap sockets exist on LCSC but not checked (no web budget). Kailh CPG151101S11 (C2803348) and -1 (C5184526)
  are the other listings of the same part.
- Diode: 1N4148WS SOD-323 (smaller, 75 V, 150 mA) if the back-face pitch needs the smaller pad; footprint Diode_SMD:D_SOD-323, symbol Diode:1N4148WS.

## SPICE (diode, from the 1N4148W datasheet class values)
`.model D1N4148W D(Is=2.52n Rs=0.568 N=1.752 Cjo=2p M=0.4 Vj=0.6 BV=75 Ibv=1u Tt=6n)` - the classical 1N4148 fit. Datasheet values: Vf 1 V @
50 mA, Cd about 2 pF, trr 4 ns, Ir 1 uA @ 75 V. spice.source: fit.

## Risks
1. Hot-swap socket stock: only 385 pieces (C5184526) are confirmed in stock; 104 x 100 boards = 10 400 pieces. C41430893 claims 131k but the
   listing is mislabelled (HanElectricity pushbutton). Confirm at order time. Single-vendor part (Kailh), JLC standard PCBA only,
   MSL 5 (bake before reflow).
2. No hot-swap footprint in the KiCad lib; a project footprint is needed and its pad geometry must be taken from the Kailh drawing.
3. No in-stock horizontal-shaft encoder confirmed; R28 depends on it.
4. 0 spare GPIO on the RP2040 (30 of 30).
5. MCU current figures are estimates (datasheet PDF not converted); bus-mode saving depends on firmware holding 48 MHz and sleeping.
6. `hwflow parts` search is broken for text filters; all prices and stocks need a re-check from the BOM stage.
7. Crystal CL not verified; the 12 MHz CJ13 part may need a different load cap value.

## Datasheet links
- RP2040: https://pip-assets.raspberrypi.com/categories/814-rp2040/documents/RP-008371-DS-1-rp2040-datasheet.pdf (hardware design: https://datasheets.raspberrypi.com/rp2040/hardware-design-with-rp2040.pdf)
- W25Q16JV: https://www.winbond.com/hq/product/code-storage-flash-memory/serial-nor-flash/
- 1N4148W: LCSC C81598 datasheet
- Kailh CPG151101S11: LCSC C2803348 / C5184526 / C5156480
