# Circuit (stage 4)

## Pass A: key matrix, keyboard MCU, encoder, USB hub / USB data (2026-10-22)

design.yaml is the source of truth. The repetitive entries (matrix, hub support parts) were written once by
`tools/gen_pass_a.py --footprints --apply`; edit design.yaml directly from now on (re-running `--apply` replaces
everything from `nets:` on, so it would delete pass B/C parts).

### Ref ranges
| block (group) | refs |
|---|---|
| matrix | SW1-SW104 sockets, D1-D104 diodes (fixed placements) |
| hub1 | U101 GL3523, Y101, L101, C101-C124, AC caps C131-C140, R101-R110 |
| hub2 | U151 GL3523, Y151, L151, C151-C174, AC caps C181-C184, R151-R158 |
| usb_host | U301 HD3SS3220, J301 host USB-C, D301-D302 SS ESD, D303 D+/D- ESD, R301-R303, C301-C302 |
| usb_c | U311/U312 HD3SS3212, J311/J312, D311-D316, C311-C312 |
| usb_a | J321/J322, D321-D324 |
| mcu | U401 RP2040, U402 W25Q16JV, Y401, C401-C416, R401-R405, SW401 BOOTSEL, SW402 RESET, TP401-TP403 SWD |
| ui | SW501 EC11E, R501-R503, C501-C502 |
Pass B / C: use refs >= 600 to avoid collisions.

### Key matrix (R1, R2, R27)
- ANSI 104 at 19.05 mm: Esc left edge x = 5.7 mm (22.5 u = 428.6 mm centred on the stage-2 key rows at x 220),
  row centres y = 15.725 (F row), 44.3, 63.35, 82.4, 101.45, 120.5 (F row + 0.5 u gap, as the board height note).
  Nav cluster at 15.25 u, numpad at 18.5 u. KP+ and KP Enter are vertical 2u (centre +0.5 u in y).
- 12 x 11 matrix, fold at 11 u (notes/parts-keyboard.md): left edge < 11 u -> logical row = physical row,
  else physical row + 6; column = index in the half row. Keys per logical row 0-11: 9, 11, 11, 11, 10, 5, 7, 10, 10, 5, 7, 8
  (max 11 = COL0-COL10). Each key: COLc -> socket pin 1, socket pin 2 -> K<n> -> diode anode, cathode -> ROWr (COL2ROW).
  The comment above each SW in design.yaml names the key and its row/column.
- GPIO: ROW0-11 = GPIO0-11, COL0-10 = GPIO12-22, ENC_A/B/SW = GPIO23/24/25, JACK_DET GPIO26, HOST_RP_OUT1/2 GPIO27/28,
  PD_STAT GPIO29.
- Back face: hwflow has no placement side (every part is placed unflipped, blocks.py "hwflow places every part on the
  front"; the placer reads the side from the courtyard layer). The sockets and diodes therefore use project footprints
  drawn in front-view coordinates with B.Cu/B.Paste/B.Mask pads and a B.CrtYd courtyard:
  kb_keys:SW_MX_Hotswap_Kailh_CPG151101S11_{1u,2u,2u_Vertical,6.25u}_Back and kb_keys:D_SOD-123_Back.
  The earlier 1u_Back file was drawn mirrored on F.Cu (meant for a flip that hwflow does not do; placed unflipped its
  holes were mirrored against the MX pins); it is rewritten: MX pins at (-3.81,-2.54)/(2.54,-5.08) front view, socket
  pads at (-7.085,-2.54)/(5.842,-5.08) on B.Cu (same kiswitch geometry, B9 still open). F.CrtYd = switch housing 14.5 x 14.5.
  Fab note: the position file will list these parts as top side; the CPL for JLC must mark SW*/D1-D104 bottom.
- Stabilizers: the pad-less STAB_MX parts cannot pass `check` (Mechanical:MountingHole has no pins, `no-pins` error).
  The stabilizer holes are merged into the 2u / 2u vertical / 6.25u socket footprints (same hole positions as the KiCad
  SW_Cherry_MX_2.00u_PCB, _2.00u_Vertical_PCB, _6.25u_PCB footprints): Backspace, Enter, LShift, RShift, KP0 (2u
  family), KP+, KP Enter (vertical), Space (6.25u). STAB_MX_* footprints are unused. The 4 mm stabilizer holes of the
  bottom row reach y = 130.74, 0.74 mm into the front strip (NPTH, no copper): the front-strip parts must keep clear.
- Diode at key centre + (0, 5.6) mm, back face, clear of the 4.0 centre post and the 1.75 plate pins.

### Hub / USB data (R3, R4, R5, R15)
- Topology per architecture §1: hub1 US <- HD3SS3220 <- host; DS1 C1 (HD3SS3212), DS2 C2 (HD3SS3212), DS3 A1, DS4 hub2.
  hub2 DS1 A2, DS2 audio bridge (AUD_DP/AUD_DM, USB 2 only), DS3 RP2040 (USB FS via 27 ohm), DS4 unused (all pins nc).
- AC caps 100 nF at every transmitter (hub TX pins): hub1 5 pairs, hub2 2 pairs (US and DS1) = 14 caps (the parts list
  had 16 incl. 2 spare). RX lines have no caps (the far transmitter carries them).
- HD3SS3220 (datasheet notes p.3-5): PORT = GND (UFP), ADDR open (GPIO mode), ENn_CC = ENn_MUX = GND, CURRENT_MODE = GND
  (UFP: don't care), VBUS_DET via 910 k from VBUS_HOST (TI asks 900 k; 910 k E24 is +1 %), OUT1/OUT2 open drain with
  200 k to 3V3_D -> RP2040 GPIO27/28 (and pass B may read them for the eFuse ILIM). DIR, ID, INT_N, VCONN_FAULT_N open.
  It owns the host CC pins (HOST_CC1/HOST_CC2, Rd inside); pass B adds only a CC TVS (abs max 6 V).
- HD3SS3212: A0 = hub TX (after the cap), A1 = hub RX, B = connector TX1/RX1, C = TX2/RX2; SEL = C1_ORIENT/C2_ORIENT
  (L = A-B = CC1 orientation; pass B must drive this polarity), OEn = GND, VCC = 3V3_D.
- ESD: 8 x TPD4E02B04 (0.25 pF, R15 <= 0.3 pF) at the SS pins of J301 (2), J311 (2), J312 (2), J321, J322; 5 x PUSB3FR4Z
  on D+/D- of the five data connectors. SBU pins nc. CC and VBUS TVS belong to pass B.
- GL3523 support (gl3523.md): RTERM 20 k (p.15), 25 MHz crystal with 2 x 18 pF (CL 12 pF, 3 pF stray), V5/VDDP 10 uF +
  2 x 100 nF, V33 LDO output 10 uF + 100 nF, 100 nF per 3.3 V and 1.2 V pin, 1.2 V DC-DC 4.7 uH + 2 x 10 uF with FB
  tied to the 1.2 V output.
  UNVERIFIED (Genesys design guide / datasheet p.33-34 not read; decide before layout release):
  - 1.2 V DC-DC: inductor 4.7 uH (NR3015 class, Isat > 1 A vs 800 mA max) and FB connected directly to the output
    (fixed 1.2 V); if the GL3523 needs a divider, add it.
  - VBUS detect divider 47 k / 100 k (5 V -> 3.4 V): ratio and threshold from p.33.
  - PAMBER2 = power-switch polarity strap (p.34): 10 k to GND placed; pass B confirms against the TPS2546/TPS2553 EN
    polarity and may move it to 3.3 V.
  - PGANG 10 k to GND = individual over-current mode (gl3523.md: < 100 k down). CHIPEN 10 k to V33, RESETJ 10 k to V33
    + 1 uF (10 ms) to GND.
  - Internal ports: OVCUR of hub1 DS4 and hub2 DS2-DS4 tied to the hub's 3.3 V (no fault); PWREN of those ports open.
  - SPI flash pins open: ROM defaults (internal ports show as removable, cosmetic, gl3523.md).
- Net intent: SS and USB 2 nets `class: high_speed`, impedance_ohm 45: hwflow has no differential-pair field, so 45 ohm
  is the single-ended (odd-mode) half of the 90 ohm differential target of R5; the stack-up calculation for 90 ohm diff
  stays a layout item. edge_ns 0.05 (SS), 0.5 (USB 2 HS), 20 (matrix).

### Keyboard MCU (RP2040 hardware design guide, rp2040.md)
- 100 nF on each IOVDD (6), USB_VDD, ADC_AVDD; 1 uF VREG_IN; 1 uF on VREG_VOUT + 100 nF on each DVDD (MCU_1V1, internal
  regulator); 10 uF on 3V3_D at the MCU. TESTEN = GND. 12 MHz CJ13 with 18 pF / 18 pF and 1 k in XOUT (CL 12 pF).
- W25Q16JVSNIQ on QSPI (SS, SCLK, SD0-SD3), 100 nF. BOOTSEL: SW401 to GND via 1 k on QSPI_SS. RESET: SW402 on RUN,
  10 k pull-up. SWD: TP401 SWCLK, TP402 SWDIO, TP403 GND (1 mm pads).
- USB: 27 ohm series in D+ and D- (RP_DP/RP_DM -> MCU_DP/MCU_DM) to hub2 DS3.

### Encoder (R28)
- EC11E153440D: A/B/push to GPIO23/24/25, C and S2 to GND, 10 k pull-ups to 3V3_D, 10 nF on A and B (10 k x 10 nF =
  100 us, Alps recommended RC class). The MP mounting tabs are unmapped pads of the footprint (no symbol pin): R15 asks
  the metal shaft to be grounded; tie MP to GND in layout or pick a symbol with MP.

### Pass A results
- `hwflow check`: 0 errors, 6 warnings (no power source on 3V3_D, 5V_SYS, VBUS_A1/A2: pass B; HUB1/2_1V2 come from the
  hub's own DC-DC through L101/L151, a passive SW pin, so the warning stays). Check run time 720 s (barrier proof 638 s,
  cutcap 88 s with 208 fixed matrix parts): run `hwflow check` on its own (background, long timeout) before
  `build --until sim`, which then skips the check stage.
- ERC: 2 errors, U311/U312 SEL not driven (C1_ORIENT/C2_ORIENT come from pass B); 14 warnings, single-pin labels of the
  interface nets.
- Floorplan (--iters 2000 --seeds 1): PASS with risks. Front 47 %, back 18 %; depth 10.45 / 11.1 mm; track capacity
  10 % (risk > 10 %), peak cell 4.8 x; 45 overlaps left by the quick placement in the hub region (risk, not failure).
- BOM of this pass: $23.18 per board (incl. consigned sockets 104 x 0.075; TP pads unpriced).

## Pass B: power / USB PD (2026-10-23)

Refs 600-899. Components, pass-B nets, sim and budgets were written by `tools/gen_pass_b.py --apply` (marked
blocks in design.yaml); from 2026-10-23 design.yaml is edited by hand: re-running the helper rewrites only the three
marked blocks (it does not touch pass-A edits such as FB101/FB151), but any hand edit inside those blocks would be lost.

### Ref ranges
| block (group) | refs |
|---|---|
| pd_sink | J600 PD input, U600 CH224D, M600/M601 AO4407A back-to-back, D600-D604, R600-R604, C600-C602 |
| pwr_ctl | D630/D631, M630-M634, R630-R638, C630 (PD-mode detect, PD_STAT, HOST15/HOST30 decode, bus Rp kill) |
| vbus_path | U650 TPS25940A eFuse, D650-D652 TVS, R650-R657, M650-M652, C650-C656 |
| buck_5v | M680 input-isolation P-FET, U680 LM61460AFS, L680, R680-R684, C680-C690 |
| pd_src1 / pd_src2 | U700/U750 LM61460AFS, U701/U751 IP2736U_EF, U702/U752 TPS2553, U703/U753 LMV331, M700-M707 / M750-M757, L700/L750, TH700/TH750 |
| usba_sw | U800/U820 TPS2546, R800-R803 / R820-R823, M800/M801 / M820/M821, C800-C803 / C820-C823, D800/D820 |
| buck_dig | U830 TPS62933F 3V3_D, U840 TPS62933F 0V9_CORE, U850 AP2112K-1.8 |
| aux_pm | U860 TPS65131 +-5.5 V, L860/L861, D860/D861 |

### PD input (R9)
- CH224D (ch224d.md p9): CFG1 open = 20 V request; VBUS pin through R600 100 ohm + D600 BZT52C22 + C600 1 uF (abs max
  24 V, the input TVS SMBJ22A clamps at 35.5 V); VDD 1 uF; Rd 5.1 k on CC1/CC2 (reference circuit); NMOS#, ISP, ISN = GND.
- Sink path M600/M601 AO4407A common-source, gate 10 k to GND, 100 k G-S, 12 V zener G-S (Vgs abs max 20 V): on as soon
  as VBUS > |Vth|; the 5 V phase before the 20 V contract is ignored by the mode logic below.
- PD mode detect: D630 12 V zener + R630 10 k, R631 100 k / C630 1 uF to PDS_G; at VIN20 = 20 V, PDS_A = 8 V and
  PDS_G crosses the 2N7002 Vth (~2 V) after -100 ms x ln(1 - 2/8) = 29 ms; D631 discharges it fast on removal.
  PDS_G -> M630 -> PDMODE_N (100 k to 3V3_D) -> M631 -> PD_STAT (high = PD mode, 100 k to 3V3_D, also RP2040 GPIO29).
- Buck EN (all three LM61460): R634 100 k / R635 9.09 k from VIN20: rising 1.263 x 109.09 / 9.09 = 15.2 V (lm61460.md
  p6, EN 1.263 V +-5 %: 14.4-15.9 V), so the bucks do not start on the 5 V pre-contract VBUS.

### Host VBUS path (R19, R21)
- TPS25940A (tps25940a.md p5): R_ILIM 88.7 k = 0.90/0.99/1.07 A (900 mA host); HOST15 adds 133 k: 88.7 || 133 =
  53.2 k = 1.64 A typ (87.5/R); HOST30 adds 52.3 k: 88.7 || 52.3 = 32.9 k = 2.66 A, both = 26.4 k = 3.31 A typ.
  EN/UVLO 100 k / 30.1 k: 0.99 x 130.1 / 30.1 = 4.28 V rising, 3.98 V falling. OVP 100 k / 20 k: 0.99 x 6 = 5.94 V.
  dVdT 10 nF, IMON 10 k (0.523 V/A, IMON gain 52.3 uA/A; HOST_IMON is a test point net for now). M650 (PD_STAT)
  pulls EN low in PD mode, so the host never feeds 5V_SYS while the buck runs. 3 x 22 uF + 100 nF on OUT.
- HOST15 / HOST30: M632/M633 invert HD3SS3220 OUT1/OUT2 (open drain, low = 1.5 A / 3 A advertised, pass A notes).
- TVS: SMBJ5.0A on VBUS_HOST, PESD5V0S1UB on HOST_CC1/2 (HD3SS3220 CC abs max 6 V).

### 5V_SYS buck (PD mode)
- LM61460AFS (lm61460.md, TI BOM p45): FB 102 k / 24.9 k: 1.0 x (1 + 102/24.9) = 5.096 V; RT 33.2 k = 400 kHz;
  CFF 22 pF; CIN 2 x 4.7 uF 50 V + 2 x 100 nF; COUT 4 x 22 uF 25 V (+3 x 22 uF of the eFuse output on the same rail);
  L 8.2 uH FEXL0660A (Isat 8.4 A; ripple (20-5.1) x 5.1/20 / (400k x 8.2u) = 1.16 A pp); BIAS = VOUT (< 12 V).
- M680 AO4407A in VIN, source on BUCK5_VIN: in bus mode 5V_SYS would back-feed VIN20 through the high-side body diode
  and SW; M680 turns on only when PDS_G is high (M681 pulls B5_G through 47 k; 100 k + 12 V zener G-S).

### USB-C PD source x2 (R7)
- LM61460AFS per port, FB 100 k / 24.9 k: 1.0 x (1 + 100/24.9) = 5.016 V default (IP2736U FB pin idle). For 9 V the
  IP2736U sinks I_FB = (9 - 1)/100k - 1/24.9k = 39.84 uA at the FB node (through R704/R754 1 k); the testbench models
  this with BFB1/BFB2 (the IP2736U is spice: false). Same RT/CFF/CIN as the 5V_SYS buck, COUT 4 x 22 uF. 9 V ripple
  (20-9) x 9/20 / (400k x 8.2u) = 1.5 A pp.
- IP2736U_EF Fig. 1 (ip2736u.md p4): VIN from the converter output through R703 27 ohm (datasheet 28 ohm, E24),
  VCC 2.2 uF, VIN 1 uF, CMPV 1 k + 3.3 nF, CMPI 200 k + 3.3 nF, 330 pF per CC, NTC 10 k B3380, VOUT1 sense 1 k to
  port VBUS, VOUT1G drives M700/M701 AO4406A back-to-back (common source, 100 k G-S), CSP/CSN across R709 10 mohm
  2512 in the port ground return (C1_RTN): J311 GND pins sit on C1_RTN, not on GND (low-side sense of the reference).
- Bus mode (no PD input): TPS2553 from 5V_SYS, EN = HUB1_PWRENx through 10 k, forced low by PD_STAT (M704). OUT 7 V
  abs max, so M705 AO3401A sits between OUT and VBUS_C (gate 100 k to VBUS_C, pulled low by M706 = PDMODE_N high).
  ILIM (fit I = 24.23 / R^0.978 A, R in k, checked against p7: 210 k -> 130 mA, 49.9 k -> 529 vs 520 mA):
  232 k = 118 mA; HOST15: 232 || 200 = 107.4 k = 250 mA; HOST30: 232 || 84.5 = 61.9 k = 427 mA; both 47.3 k = 558 mA
  (HOST15 resistor was 196 k, changed for Monte Carlo yield, see below).
- Bus Rp: 56 k per CC from 5V_SYS through M707 AO3401A (RP_G pulled low by M634 in bus mode; in PD mode R638 pulls
  RP_G to 5V_SYS and R717 100 k holds RP_C low): default-USB Rp of the Type-C spec.
- Orientation (R15/pass A HD3SS3212 SEL): LMV331, IN+ = CC1/2, IN- = CC2/2 (100 k / 100 k each, 200 k load on CC:
  Rd 5.1 k || 200 k = 4.97 k, inside Rd +-10 %); open-drain output 100 k to 3V3_D, then 100 k / 100 nF (10 ms) to
  Cn_ORIENT: sink on CC1 -> CC1 < CC2 -> low -> SEL low = A-B (TX1/RX1).
- Port protection: SMF12A on VBUS_C, PESD12VS1UB on each CC, 10 uF at the receptacle.

### USB-A (R8)
- TPS2546 (tps2546.md p31-32): ILIM_HI 28.0 k: 50250/28 = 1.795 A typ, min 0.98437 x 45271/28 + 30 = 1.62 A >= 1.5 A
  (R8). ILIM_LO 562 k = 89 mA typ (bus mode); HOST15 adds 309 k (199 k, 252 mA); HOST30 adds 162 k (126 k, 400 mA),
  both 89.4 k = 562 mA. ILIM_SEL = PD_STAT. CTL1-3 = 3V3_D (1111 = CDP, data through DP_IN/DP_OUT). 2 x 22 uF on VBUS_A.

### Digital rails
- 3V3_D: TPS62933F, RT to GND = 1.2 MHz, FB 31.6 k / 10.0 k: 0.8 x 4.16 = 3.328 V; L 2.2 uH, COUT 2 x 22 uF (p31 30 uF),
  CIN 10 uF + 100 nF, SS 10 nF, EN 100 k to 5V_SYS.
- 0V9_CORE: TPS62933F, RT open = 500 kHz, FB 10.0 k / 80.6 k 0.5 %: 0.8 x (1 + 10/80.6) = 0.8993 V; L 4.7 uH,
  3 x 22 uF; EN = 1V8_D (core after IO, XU316 sequencing). 1V8_D: AP2112K-1.8 from 3V3_D, 1 uF in/out.
- +-5.5 V audio pre-regulator: TPS65131 1.38 MHz, FBP 715 k / 200 k: 1.213 x (1 + 715/200) = 5.55 V; FBN 909 k /
  200 k to VREF: -1.213 x 909/200 = -5.51 V; 4.7 uH x 2, B5819W x 2, 2 x 10 uF per rail, 2.2 k dummy load each
  (2.5 mA, keeps PWM at idle).

### Pass B check fixes (2026-10-23)
- GL3523 V5/VDDP (pins 43/42) moved from 5V_SYS (4 A, 2.1 mm track) to local nets HUB1_5V / HUB2_5V, fed through
  FB101 / FB151 (BLM18PG221SN1D 220 ohm at 100 MHz, 1.4 A, 0603) with the existing 10 uF + 2 x 100 nF local. No class
  power on these nets (0.4 mm-pitch QFN). The bead is spice: false (the hub is spice: false and its load is the
  lumped BLD5 on 5V_SYS). Bead value: common practice, not from the GL3523 datasheet.
- external_supply on the passively fed rails (no power_out pin in their symbols): 3V3_D (L830), HUB1/2_1V2 (L101/L151),
  HUB1/2_5V (beads), VIN20 (sink FETs), BUCK5_VIN (M680), C1/C2_CONV (L700/L750), CH224_VBUS (R600), C1/C2_IPVIN (R703/R753).

- VBUS_C1/C2 and PDIN_VBUS lost current_a / class power: the IPC width (1.4 / 1.6 mm) cannot leave the 0.5 mm-pitch
  USB-C receptacle pads (check pad_escape error). Their copper is sized by the copper_r budgets L2/L4 at 3 A / 3.19 A.

### Pass B results (build 2026-10-23 01:44, temps 0/25/40 C)
check 0 errors 0 warnings; ERC 0 errors (U311/U312 SEL now driven from Cn_ORIENT), 3 warnings (AUD_DP/AUD_DM/JACK_DET
single-pin labels, pass C). SPICE 121/121 pass. Worst over temps:
| measure | worst | limit | req |
|---|---|---|---|
| vc1/vc2_9v (2.22 A both) | 8.906 V | 8.55-9.45 | R7 |
| vc1/vc2_5v (3 A both) | 4.892 V | 4.75-5.25 | R7 |
| va1/va2 (1.5 A both, PD) | 4.981 V | >= 4.75 | R8 |
| pin_pd_w (9 V case) / 5 V case | 62.83 / 52.16 W | <= 65 | R9 |
| ihost_ports_at_limit (typ internal 483 mA) | 0.8974 A | <= 0.900 | R19 |
| port limits bus mode C / A | 117.9 / 89.7 mA | >= 75 | R19 |
| bus_floor VBUS C / A, ihost | 4.563 / 4.568 V, 0.8994 A | >= 4.45, <= 0.900 | R19 |
| bus_heavy VBUS C / A, ihost | 4.566 / 4.570 V, 0.8967 A | >= 4.45, <= 0.900 | R19 |
| eFuse limit | 0.987 A | 0.90-1.10 | R19 |
| 1.5 A host: ports, ihost | 250 / 252 mA, 1.492 A | >= 225, <= 1.5 | R21 |
| 3 A host: ports, ihost | 557 / 562 mA, 2.740 A | >= 500, <= 3.0 | R21 |
| Tj at 40 C: U680 / U700 / U800 / U830 / U860 / M600 pair / M700 pair | 75.4 / 80.7 / 47.9 / 49.7 / 51.4 / 49.9 / 50.3 C | 125-150 | R16 |
Budgets (tools/pass_b_budgets.json, unchanged numbers confirmed by this run): L2_vbus_c1/c2 47.3 mohm at 3 A
((4.892 - 4.75)/3), L3_vbus_a1/a2 153.7 mohm at 1.5 A ((4.9806 - 4.75)/1.5), L1_host_5v_sys 125.1 mohm at 0.9 A
((4.5626 - 4.45)/0.9), L4_pd_input 213 mohm at 3.19 A ((65 - 62.832)/3.19^2).

### Monte Carlo (50 runs)
First run: bus_floor 94 % (ihost_floor 0.8989..0.9001 A against 0.900; failing runs had the TPS65131 FB resistors
high, i.e. larger |V| on the +-5.5 V rails) and bus_1a5 96 % (ihost_1a5 1.495..1.501 A against 1.5 A; failing runs
had low TPS2553 ILIM resistors). Changes: R860-R863 (TPS65131 FB) 1 % -> 0.1 %, R831/R832 (3V3_D FB) 1 % -> 0.5 %,
R711/R761 (TPS2553 HOST15 parallel ILIM) 196 k -> 200 k (port limit 253 -> 250 mA, R21 floor 225 mA kept).
Second run: every analysis 100 % (ihost_floor 0.8992..0.8996, ihost_1a5 1.490..1.495, ic_1a5 >= 247.9 mA). +$0.04 BOM.
Note: these host-current margins are 0.4-0.6 % of the limit; they hold for typical model efficiencies and loads only
(the bucks' efficiency and the load currents are not part of the Monte Carlo).

### Values not from a datasheet
R704/R754 1 k (IP2736U FB series; Fig. 1 text gives "100 k and 3.3 nF" on FB, ambiguous), mode-logic RCs (R631/C630,
R723/C720), 12 V gate zeners, 2.2 k dummy loads, ferrite beads, LMV331 dividers, 10 k gate resistors.

### Interface nets for pass B / C (allow_single until then)
5V_SYS, 3V3_D, GND, VBUS_HOST (external_supply), VBUS_C1, VBUS_C2, VBUS_A1, VBUS_A2, CC1_C1, CC2_C1, CC1_C2, CC2_C2,
HOST_CC1/HOST_CC2 (TVS only), C1_ORIENT, C2_ORIENT, HUB1_PWREN1/2/3 (C1, C2, A1), HUB2_PWREN1 (A2), HUB1_OVCUR1/2/3,
HUB2_OVCUR1 (10 k pull-ups here, open-drain fault outputs of the port switches), HOST_RP_OUT1/2, AUD_DP/AUD_DM,
JACK_DET, PD_STAT.

## Pass C: audio (2026-10-23)

Generated by `tools/gen_pass_c.py --apply` (marked PASS C blocks in design.yaml: nets, components, sources,
analyses). Refs 900-1199. Fit models in `lib/spice/audio_fit.lib` (the TI vendor models OPA161x / OPA1622 were
translated, `lib/spice/opa16*_ti.lib`, but their transient hung in ngspice: not used).

### Blocks
- DAC (audio_dac): U900 ES9039Q2M, I2S slave of the XU316 (XMOS reference, CODEC_MASTER = 0), I2C 0x90, MODE = GND,
  CHIP_EN = DAC_EN with R901 100 k pull-down. Current outputs DC-blocked with 330 uF (T520B) into the I/V summing
  nodes at Vg = 0 (Vg = AVCC/2 drove 33 mA DC into the 100 ohm network).
- I/V (U910 L, U911 R, OPA1612): Rf 383 ohm 0.1 % || 1.8 nF C0G. 383 instead of 374: 2.03 Vrms nominal at 0 dBFS
  (374 gives 1.98 < 2.0). Rdac +-15 % (p.21) moves the level +-15 %: level calibration is digital (product note).
- Diff-to-SE (U912, OPA1612, gain 1): 100 ohm 0.1 % network R922-R929 with 6.8 nF C0G.
- HP buffer (U913 OPA1622, gain 1, +-4.5 V): EN = HP_TN (R934 1 M to 3V3_A; jack TN opens with a plug, so EN goes
  high). 0.47 ohm series R930/R931, 10 k R932/R933 to AGND, TVS D930-D932, J930 SJ-3524. M930 2N7002 + R935 100 k
  give JACK_DET (3V3_D logic) to the pass-A MCU.
- LDOs (audio_ldo): FB950 -> U950 ADP7118 (27.4 k / 10 k = 4.488 V) AUD_P4V5; FB951 -> U951 LT3094 (45.3 k) AUD_N4V5;
  FB952 5V_SYS -> U952 LT3045 (33.2 k) 3V3_A; U953 LT3045 (45.3 k) ADC_4V5 with EN = ADC_PWR_EN (R955 100 k
  pull-down); FB953 3V3_D -> 3V3_DA. Single AGND-GND tie R900 0 ohm.
- Mic (audio_mic): J980, R980 2.2 k bias from 2V5_BIAS (R956 470 / R957 1.43 k from 3V3_A, C967 47 uF: 2.32 V),
  RF filter R983 47 ohm + C980 1 nF, C981 10 uF AC coupling, R984 100 k to MIC_CM = ADC_4V5 / 2. U980 OPA1612 on
  ADC_4V5: A non-inverting, Rf 1.69 k, Rg 15 ohm + 4 x 100 uF (C984/C988/C989/C995) + M980 AO3400A (MIC_GAIN): gain
  1 or 110; B inverter 220 / 220 for the differential ADC drive; anti-alias 2 x 100 ohm, 2.2 nF + 2 x 220 pF.
- ADC (audio_adc): U1000 ES9822PRO, both channels in parallel. Pins relied on but unverified (B6): 3 MODE = AGND,
  8 XIN = MCLK, 25 SDA, 26 SCL, 27 ADDR1 = 3V3_DA, 28 ADDR2 = AGND, 29 DVDD (1 uF, internal regulator assumed),
  30 AVDD, 31/40/41 grounds, 37 GPIO3 = I2S_DIN, 38 GPIO2 = LRCK, 39 GPIO1 = BCLK; ACLK (4) nc.
- Bridge (audio_bridge): U1100 XU316-1024-QF60B, Y1100 24 MHz with R1101 680 ohm and 22 pF (datasheet Fig 11),
  U1101 W25Q64JVSSIM with R1100 10 k CS pull-up, Y1101 24.576 MHz / Y1102 22.5792 MHz SiT1602 enabled by
  OSC48_EN / OSC44_EN (R1104 pull-up, R1105 pull-down), 33 ohm source terminations on MCLK and I2S, I2C pull-ups
  R1106/R1107 2.2 k, FB1100 + 1 uF for PLL_AVDD, RST_N and JTAG on TP1100-TP1105.

### Testbench and method
- DAC model per pin: 390 ohm noiseless to AVCC/2, noise current 1.016e-11 A/rtHz from DNR 130 dB re 10 Vrms
  (p.54; 0.60 uV at Rf 374), AC 0.5 on the positive pins. A-weighting network of E buffers / C / noiseless G
  (1 kHz -0.002 dB, 100 Hz -19.15 dB, 10 kHz -2.49 dB).
- R22: host VBUS noise 100 mV rms white in the audio band and a correlated 0.5 mV rms ground drop, injected in series
  in the audio ferrite subckts (FB_INJ, global nodes vbn / gdn); the TPS65131 fit has no line transfer, so 0 dB
  converter rejection is assumed. Coupled-noise runs scale both generators x 1e4 and the result x 1e-4.
- Full-board operating point: needs the `.nodeset` from tools/pass_c_nodeset.txt (converged op of the board with the
  HP and the mic blocks dropped in turn, 524 nodes) and `option gmin=1e-9` in each pass C analysis (default gmin
  gives NaN in Newton; KLU converges but has no noise analysis).
- hwflow takes the `meas` kind from the first word of `cmd`; pass C analyses start with `option`/`alter`, so their
  `meas` statements sit in the cmd and the measures read the vectors.
- Pass B lumped loads (VILDP/VILDN/VILD5) were reduced by the modelled audio draw (+rail 37.2 mA with ADC_4V5 on,
  27.4 off; -rail 29.3 mA; 5V_SYS 4.46 mA), and the 5 bus analyses run with ADC_4V5 off: R19 margins unchanged.

### Fixes during pass C
- Mic preamp: Rf 15 k / Rg 150 ohm gave 2.03 uVrms input-referred at minimum gain (R11 0.944): with the gain FET off
  the Rf thermal noise (15.7 nV/rtHz) is in series with the output. Now 1.69 k / 15 ohm with 4 x 100 uF (low-frequency
  corner 26.5 Hz nominal at maximum gain, about 40 Hz with X5R bias derating) and a 220 ohm inverter.
- R11 gain is measured from the jack tip to the ADC (amplifier gain at its input terminals); the gain from the
  150 ohm source EMF is 0.57 dB lower (2.2 k bias loading) and is kept as a debug probe. EIN uses the EMF gain.
- Op-amp clamp diodes: N = 0.1 with the default IS temperature law made their leakage noise dominate at 40 C
  (1.05 uV); XTI = 0, EG = 0.01 (behavioural clamp, no drift).
- Board transients (bus_*: tran uic from 0) stalled with the audio models: (1) the op-amp clamps of U980 crossed when
  ADC_4V5 is off (hi below lo, kA in series diodes) and a constant Iq drove the off rail to -33 V; now hi/lo stop at
  the rail midpoint, the clamps are N = 1 with the knee 0.55 V inside, Iq scales with the supply below 2 V;
  (2) LDO fits softened: LT3045/LT3094 ROUT 20 mohm -> 0.1 ohm [E], ADP7118 GM 100 -> 20 A/V (85 kHz crossover).
- LDO CSET 4.7 uF -> 470 nF (C955, C959, C962): 4.7 uF took 0.2 s to start, so the 150 ms bus transients still saw
  the rails charging. RSET noise corner 7.5-10 Hz, below the A-weighted band.
- Lumped audio loads re-fitted to the transient draw (+rail 27.5, -rail 29.6, 5V_SYS 4.46 mA in bus mode): bus
  VILDP 0.0025, VILDN 0.0004 (allocation 30 / 30 mA), heavy 0.0077 / 0.0154 (45 / 45 mA), default 0.0477 / 0.0554.
  bus_floor ihost back to 0.8994 A (pass B 0.8994).
- 0V9_CORE current_a 0.85 -> 0.4: the 0.3 mm track could not leave the 0.4 mm pitch XU316 VDD pads (check error);
  the core current enters through the 4 paddles from the plane.

### Design calculations (pass C)
- AUD_P4V5 (U950 ADP7118, VREF 1.2 V [M]): 1.2 x (1 + 27.4 k / 10 k) = 4.488 V. AUD_N4V5 (U951 LT3094, ISET 100 uA [M]):
  -100 uA x 45.3 k = -4.53 V. 3V3_A (U952 LT3045): 100 uA x 33.2 k = 3.32 V. ADC_4V5 (U953 LT3045): 4.53 V.
  Simulated: 4.483 / -4.527 / 3.320 / 4.527 V.
- I/V: Rf 383 ohm || 1.8 nF C0G, pole 1 / (2 pi x 383 x 1.8 n) = 231 kHz. Diff-to-SE 100 ohm || 6.8 nF: 234 kHz.
  Level at 0 dBFS: 2.002 Vrms into 32 ohm, 2.028 Vrms into 300 ohm (R29: >= 1.0 / 2.0).
- Response: the 330 uF DC blocks against the 383 ohm I/V input and the poles above give -0.0647 dB at 20 Hz to 20 kHz
  relative to 1 kHz (R31 limit +-0.0969 dB).
- Output Zout: 0.47 ohm series (R930/R931) + OPA1622 closed-loop Zout: 0.470 ohm simulated (R32 <= 1 ohm).
- Mic bias: Thevenin 3.3 V x 1.43 / (0.47 + 1.43) = 2.484 V, 354 ohm; at 0.5 mA capsule current 2.484 - 0.5 m x 354 =
  2.307 V (sim 2.3215 V, R33 2.0-3.0 V); tip 2.32 - 0.5 m x 2.2 k = 1.22 V.
- Mic gain (FET on): 1 + 1.69 k / 15 = 113.7 (41.1 dB) less the AO3400A RDS(on) and the 4 x 100 uF reactance: 40.40 dB
  simulated at 1 kHz, jack tip to ADC input (R11 >= 40 dB). Low-frequency corner 1 / (2 pi x 15 x 400 u) = 26.5 Hz.
- Anti-alias: 2 x 100 ohm + 2.2 nF differential: 1 / (2 pi x 200 x 2.2 n) = 362 kHz.
- ADC ADDR2 (U1000 pin 28): now strapped low through R1001 10 k instead of a hard AGND tie (pin 28 is MISO,
  bidirectional, in SPI mode; KiCad pin_to_pin warning with the AGND PWR_FLAG). An I2C address strap input sees 0 V
  through 10 k at leakage < 1 uA (< 10 mV).

### ES9822PRO pins 24-41 relied on (third-party pin list, datasheet not read: B6)
25 SDA, 26 SCL, 27 SS/ADDR1 = 3V3_DA, 28 MISO/ADDR2 = R1001 to AGND, 29 DVDD (1 uF, internal regulator assumed),
30 AVDD = 3V3_DA, 31 / 40 / 41 (EP) = AGND, 37 GPIO3 = I2S_DIN, 38 GPIO2 = LRCK, 39 GPIO1 = BCLK. Not connected:
24 (GPIO9), 32-36 (GPIO8-4). Every one of these must be checked against the ESS datasheet before layout release.

### Whole-board simulation convergence (pass C continuation, 2026-10-23)
Every analysis runs on the full netlist, so a stiff or floating part anywhere breaks every test. Causes found and fixed:
1. **SPARSE 1.3 pivoting** (root cause of the MC aborts and errors, 2-12 % per analysis): the op converged from the
   `.nodeset` only at nominal values. One MC sample (mic_bias run 34) failed on a 0.05 % change of R860 alone, and
   then the op went to a false solution (AUD_P4V5 = 226 kV, "out of range for pwr" in the pass B smin). KLU converged
   on every failing run that was tested (mic_bias 34, hp_level_32 14, bus_limits 20). KLU in turn stalls on the first
   step of the board start-up transients (tran uic, ivs_ln), and it has no noise analysis. So: SPARSE stays global
   (pd_*, bus_*), the op-based audio analyses and the derating op start with `option klu` (`sim.derating_cmd`), and
   the noise runs use `option sparse`.
2. **KLU AC ignores rshunt**, so nets joined only by a coupling cap or series R between two non-simulated pins are
   singular in AC: 14 hub SuperSpeed caps C131-C140/C181-C184, R402/R403/R404 (RP2040), R1108-R1110 and MCLK (XU316).
   Fix: sim-only 1e9 ohm `RFL*` lines (testbench, not BOM).
3. **Ferrite model**: FB_INJ was 1 uH + 0.05 ohm only, which makes a Q ~ 30 resonator with the 10 uF LDO input cap.
   bus_3a stalled at xfb952.x1. Rp 120 ohm across L1 (the bead's resistive 120 ohm at 100 MHz [M]). Audio band
   unchanged (|j w L| = 0.13 ohm at 20 kHz).
4. **MIC_GAIN source**: an ideal V source on the M980 gate stalled bus_floor at 0 C (vmicg#branch). It now has
   100 ohm source resistance (GPIO output class) [E].
5. **R22 method**: the coupled-noise generators were a 1 k resistor's thermal noise times an E gain of 1.7e5..1.7e9.
   That gain sat in the op matrix and gave the 142.9 uV false result. Now an AC transfer:
   V_gen x sqrt(mean |H_A|^2) over 1000 linear points from 20 Hz to 20 kHz, RSS of the VBUS and ground generators.
   See notes/noise-allocation.md.
6. **Stiff start-up** (bus_* MC, timestep too small at mic_gain / ivs_ln at 7-12 ms): `method=gear` (sim.options).
   Gear converged on all 7 failing runs that were tested.
7. Tried and reverted: tanh bounds on the fit outputs (power_fit smin arguments, op-amp / LDO output currents). They
   fixed the KLU MC op failures they were tested on, but the SPARSE operating point that the noise analyses need then
   failed at nominal values ("DC solution failed"). The libs are unchanged. The few KLU MC op errors that remain
   (false Newton start, "out of range for pwr") stay within the 95 % yield.
The derating list from the broken runs (U910/U911 Tj 234756 C, C918 228060 V, R950 1e9 mW) is cleared: the KLU
derating op gives U700/U750 1.16 W as the top dissipation. Two items remain, C701/C751 with 9 V across them (limit
8 V). These are the PD-source bootstrap caps: CB is not driven in the averaged buck model, so the cap sees the full SW
voltage. On the real board the cap sees about VCC, 5 V. This is a model artifact.

### Values not from a datasheet (pass C)
FB_INJ Rp 120 ohm [M, bead rating], MIC_GAIN 100 ohm GPIO source [E], RFL 1e9 ohm (sim only), ground drop 0.5 mV rms
(0.5 mohm x 1 A, C11 [E], post-layout replaces it), host VBUS noise 100 mV rms (R22 text), TPS65131 line rejection
0 dB (fit has none), ADDR2 strap 10 k [E], op-amp PSRR / AOL / ROUT and LDO PSRR / ROUT [M]/[E] as tagged in
lib/spice/audio_fit.lib.

## Stage 5 fixes, power (design review part 1 of 2, 2026-10-23)

Scope: POWER / USB findings M1-M3, S5-S7, S9, S10, S12, S14 and the product question S8. Audio refs >= 900 and the
encoder SW501 untouched (part 2). Edits: `scratch/s5edit.py`, `s5an.py`, `s5fix1.py`, `s5fix2.py` (one-shot text
edits, already applied; design.yaml is the source of truth). New parts sit in the block
`# ==== STAGE 5 POWER FIXES` before `PASS B COMPONENTS END`; new analyses in `# ==== STAGE 5 POWER ANALYSES`.
Datasheet PDFs could not be opened in this session (no WebFetch, no shell download): numbers come from
notes/datasheets/*.md and the review; items marked [unverified] must be re-read before layout release.
Board nodeset regenerated (tools/stage5_nodeset.txt: pd_9v end state for the power / CC nodes, pass C values for the
audio nodes; the old one made every op-based audio analysis land on a false solution once the CC nodes changed).

### M1: C-port connector return through the shunt (J311 / J312)
- Change: J311.S1 / J312.S1 (shell) moved from GND to C1_SHELL / C2_SHELL; R724 / R774 1 M + C723 / C773 10 nF 100 V
  (0603) shell -> GND; C724-C726 / C774-C776 3 x 10 nF + C727 / C777 100 pF from Cn_RTN to GND at the GND pins.
  Connector GND pins stay on Cn_RTN (low-side sense of the IP2736U Fig. 1).
- Why: with the shell on GND the cable shield carried part of the DC return around R709 / R759 (10 mohm): the
  IP2736U read up to 2 x low and the OCP could sit above 5 A (R6). Now every DC return passes the shunt; the shell
  only closes the HF / ESD path (10 nF; 1 M bleeds static charge).
- R6 re-check: the IP2736U OCP reads the full port current again, so its trip (PDO current + IP2736U margin, < 5 A
  for a 3 A PDO) is the port limit; the LM61460 HS limit 8.9-11.5 A (lm61460.md p.6) is a second, non-R6 limit.
- ESD path: shell discharge -> C723 (8 kV HBM, 150 pF -> 1.2 uC -> 118 V on 10 nF for < 1 us; 100 V X7R, MLCC
  transient withstand ~2.5 x rated) -> GND plane; pin discharges -> TVS (GND) -> C724-C727 -> Cn_RTN pins, bypassing
  the 2512 shunt ESL (~1 nH). Layout: C724-C727 within 2 mm of the GND pins; shunt Kelvin sense to CSP/CSN.
- Sim: no DC change (pd_9v / pd_5v at 40 C: vc1_9v 8.906 V, vc1_5v 4.892 V, as before).

### M2: port limits with datasheet spread + firmware power sharing (R19, R21)
Datasheet limits. TPS2553 p.7 fitted as power laws through 49.9 k and 210 k: min = 28.56 / R^1.0397 A,
max = 18.86 / R^0.904 A (R in k). TPS2546 p.32 (corrected in tps2546.md): min = 45271 / R^0.98437 - 30 mA,
max = 1.0139 x 50250 / R + 30 mA.

| port / host level | network | min | typ | max (mA) | floor |
|---|---|---|---|---|---|
| C default | 232 k | 99 | 118 | 137 | >= 75 |
| C 1.5 A | 232 // 182 k = 102.0 k | 233 | 263 | 288 | >= 225 |
| C 3 A | 232 // 182 // 86.6 k = 46.8 k | 524 | 563 | 583 | >= 500 |
| A default | 464 k | 77 | 108 | 140 | >= 75 |
| A 1.5 A | 464 // 316 k = 188 k | 231 | 267 | 301 | >= 225 |
| A 3 A | 464 // 316 // 165 k = 87.9 k | 523 | 572 | 610 | >= 500 |

Changed: R711 / R761 200 k -> 182 k, R712 / R762 84.5 k -> 86.6 k, R801 / R821 562 k -> 464 k, R802 / R822
309 k -> 316 k, R803 / R823 162 k -> 165 k (562 k gave 59 mA min, 199 k 217 mA min: both under the floor).
Every floor holds at the datasheet minimum with >= 2.7 % left for the 1 % resistors.

Worst-case sums (internal 483 mA typ, heavy +100 mA headphone at 0 dBFS, design worst 600 mA):
- 900 mA host: 2 x 137 + 2 x 140 + 483 = 1037 mA > eFuse min 0.90 A (TPS25940A p.5, 88.7 k). Even at typical
  limits 2 x 118 + 2 x 108 + 483 = 935 mA: a guaranteed 75 mA A-port floor makes shedding necessary.
- 1.5 A host: 2 x 288 + 2 x 301 + 483 = 1661 mA > eFuse 1.5 A level min 1.49 A (87.5 / 53.2 k = 1.64 A, -9 %).
- 3 A host: 2 x 583 + 2 x 610 + 483 = 2869 mA (heavy 2969, worst 2986) <= eFuse min 3.01 A: no shedding.
- C2 + A2 shed: 900 mA host 137 + 140 + 483 = 760 mA (heavy 860, worst 877) <= 0.90 A; 1.5 A host
  288 + 301 + 483 = 1072 mA (heavy 1172) <= 1.49 A.

Hardware for the firmware (RP2040 GPIO26-29 are the only ADC pins and were all used):
- GPIO27 (pin 39) = HOST_IMON (ADC1): TPS25940A IMON, R657 10 k -> 0.523 V/A (52.3 uA/A, 47.8-57.2, p.5), C657
  10 nF (0.1 ms). 3.3 A -> 1.73 V.
- GPIO28 (pin 40) = HOST_LVL (ADC2): R660 2 M from HOST15, R661 2 M from HOST30, R662 330 k from PD_STAT, R663 1 M
  to GND, C658 100 nF. Levels: default 0.002 V, 1.5 A host 0.318 V, 3 A host 0.637 V (simulated), PD mode ~1.6 V,
  PD + 1.5 A ~1.9 V, PD + 3 A ~2.3 V (computed). Firmware thresholds midway (0.16 / 0.48 / 1.1 V).
- GPIO29 (pin 41) = PWR_SHED (output, high = shed; R664 100 k pull-down = no shed while the MCU is in reset): M653
  pulls C2_BUSEN low (U752 off), M654 pulls A2_EN low (U820 EN; R824 10 k now in series from HUB2_PWREN1).
  HOST_RP_OUT1/2 and PD_STAT no longer go to the MCU (the decode M632 / M633 is unchanged; firmware reads HOST_LVL).
  pass A pin note (GPIO27/28 = HOST_RP_OUT1/2, GPIO29 = PD_STAT) is superseded.

Firmware rule (for the firmware owner; the hardware cannot do it alone):
- ADC free-running with DMA at >= 20 kS/s on ADC1 and a threshold IRQ: reaction <= 100 us. Shed when the calibrated
  host current exceeds 0.89 A (default host) / 1.48 A (1.5 A host); never at 3 A or in PD mode (HOST_LVL). Release
  after 5 s below 0.60 A / 1.10 A. C2 and A2 shed together (one GPIO).
- IMON gain spread is +-9 %: uncalibrated, no fixed threshold sits between the legitimate R19 floor case (0.8997 A
  simulated) and the eFuse minimum 0.90 A. A one-point IMON calibration at production test (known load, +-1 %) is
  required. The R19 floor case itself has zero margin to the eFuse minimum (both 0.90 A), as before stage 5.
- Fast steps (the ms before firmware, or a device plugged during MCU reset): each port's own switch limit
  (TPS2553 constant current, TPS2546 current limit, us response) bounds every port to the max above, and the
  TPS25940A regulates the total at its IOS (>= 0.90 A) instead of tripping (thermal timer, auto-retry A version).
  During the <= 100 us firmware reaction the worst excess (137 mA) discharges the ~250 uF on 5V_SYS by
  137 mA x 100 us / 250 uF = 55 mV. Without firmware (bootloader, crash) a worst-case unit with all four ports at
  their max limit sags 5V_SYS: residual risk.

Testbenches (limits unchanged; the steady-state "ports at limit" sums now include the firmware response):
| analysis | result | limit | req |
|---|---|---|---|
| bus_limits (no shed): port limits C / A | 117.9 / 108.5 mA (dbg host 0.936 A unshed) | >= 75 | R19 |
| bus_limits_shed: ihost | 0.707 A (0-40 C) | <= 0.90 | R19 |
| bus_1a5 (no shed): ports C / A | 263.1 / 267.5 mA (dbg host 1.550 A unshed) | >= 225 | R21 |
| bus_1a5_shed: ihost | 1.014 A | <= 1.5 | R21 |
| bus_3a: ports, ihost | 563 / 572 mA, 2.770 A | >= 500, <= 3.0 | R21 |
| corner_max_def (all max, shed): ihost | 0.759 A (ports 137 / 140 mA) | <= 0.90 | R19 |
| corner_max_1a5 (all max, shed): ihost | 1.073 A (288 / 301 mA) | <= 1.5 | R21 |
| corner_max_3a (all max, no shed): ihost | 2.883 A (C 583 mA) | <= 3.0 | R21 |
| corner_min_def: C / A | 99.3 / 77.6 mA | >= 75 | R19 |
| corner_min_1a5: C / A | 233.1 / 231.5 mA | >= 225 | R21 |
| corner_min_3a: C / A | 523.5 / 522.6 mA | >= 500 | R21 |
| bus_floor / bus_heavy: ihost | 0.8997 / 0.8969 A | <= 0.90 | R19 |
Corner selector: global node `klim` (source VKLIM: 0 typ, 1 max, -1 min) read by ISW_LIM and TPS2546_FIT in
lib/spice/power_fit.lib. IMON in the testbench: BIMON (52.3 uA/A x host current; EFUSE_FIT has no IMON port). The
new 3V3_D loads (3 x LMV339, ladders) moved ihost_floor 0.8994 -> 0.8997 A (dbg_internal_floor 0.5997 A, inside
the 600 mA internal allocation).

### M3: hardware over-voltage cut on C1_CONV / C2_CONV (R7, R16)
- Change: U700.7 / U750.7 EN on C1_EN / C2_EN, fed from BUCK_EN through R726 / R776 100 k, C728 / C778 10 nF (1 ms
  restart delay). U660 LMV339 (shared, 3V3_D, C659) comparators 1/2: in- = Cn_CONV x 14 / 114 (R727/R728,
  R777/R778), in+ = CC_VH 1.299 V through 10 k (R729 / R779) with 470 k hysteresis from the output (R730 / R780);
  open-drain output pulls Cn_EN low. Trip 1.299 x 114 / 14 = 10.58 V, release ~10.1 V, hiccup through the EN RC
  while the cause persists. U680 (5V_SYS) unaffected: one port low loads BUCK_EN (Thevenin 8.3 k) through 100 k,
  1.633 -> 1.51 V (two ports 1.40 V), above the 0.91 V falling threshold. C-buck rising threshold 15.2 -> 15.8 V.
- pd_ovp (IP2736U FB sink 99.84 uA = 15 V request: (15 - 1) / 100 k - 1 / 24.9 k): C1_CONV max 10.59 V, VBUS_C1
  max 10.50 V (limit 12 V: SMF12A standoff; LM61460 BIAS abs max 16 V p.5); C2 normal 8.996 V.
- The IP2736U_EF PDO list (28 V EPR part) stays a RELEASE BLOCKER: the cut protects the hardware, it does not make
  a 12 / 15 / 20 V contract work (the port hiccups).

### S5: orientation with an e-marked cable (Ra on the unused CC)
- Change: U703 / U753 LMV331 -> LMV339 (TSSOP-14) with Rd windows on both CC (dividers R718-R721 / R768-R771
  unchanged): RD1 = CC1 in window (R734 / R784 pull-up), CMPO = CC2 in window = SEL source (R722, R723/C720 as
  before); SEL high = CC2. Shared references: CC_VH 1.299 V (R665 15.4 k / R666 10 k: CC 2.6 V, vRd max of the 3 A
  level) and CC_VL 0.105 V bus / 0.413 V PD (R667 100 k, R668 3.3 k, R669 11 k; M655 shorts R669 while PDMODE_N is
  high): CC 0.21 V (above Ra 0.09 V with the 56 k bus Rp, below Rd 0.36-0.48 V) / 0.83 V (above Ra <= 0.40 V and
  below Rd >= 1.48 V with the IP2736U 330 uA Rp).
- Results: Rd CC1 + Ra CC2 (bus) SEL 0.002 V (<= 0.5); Rd CC2 + Ra CC1 (bus) 2.957 V (>= 2.8); Rd CC2 + Ra CC1
  (PD, Rp 330 uA as a testbench source) 3.247 V (>= 2.8).
- [unverified] the PD-mode window assumes the IP2736U_EF uses the 3.0 A Rp (330 uA, p.12) with a 5 V / 3 A PDO.
  With the default Rp (80 uA) Rd gives 0.41 V < 0.83 V: no orientation and no attach in PD mode. Confirm with
  Injoinic together with the PDO list.

### S6: no 9 V on the TPS2553 output after PD removal
- Change: M706 / M756 gate on Cn_PTG = PDMODE_N through R737 / R787 1 M, pulled low by M713 / M763 while Cn_VHI is
  high. U660 comparators 3/4: Cn_VHI (R733 / R783 pull-up) high when VBUS_Cn x 20.5 / 120.5 (R731/R732,
  R781/R782) > VHI_REF. VHI_REF 0.994 V in bus mode (trip 5.84 V; R670 178 k from 3V3_D, R671 100 k to GND through
  M656 while PDMODE_N is high, R672 330 k) and 2.14 V in PD mode (no trip: VBUS_C <= 10.6 V -> 1.80 V). Cn_VHI also
  drives a discharge FET (M712 / M762) through R736 / R786 1 k.
- pd_unplug (phone on C1 idle at 9 V, host 5.0 V): C1_BUSOUT max 4.87 V (limit 7 V abs max, tps2553.md); VBUS_C1
  9 -> 1.5 V in 18 ms by the discharge, pass-through only after PD_STAT fell; 5V_SYS min 4.834 V.

### S7: PD plug / unplug with the host connected (5V_SYS >= 4.75 V)
- Change: M650 removed (PD_STAT no longer disables the eFuse). The TPS25940A stays enabled in PD mode as an ideal-
  diode OR with the 5 V buck: the reverse comparator (-9.3 mV) holds its FET off while the buck keeps 5V_SYS above
  VBUS_HOST; the forward comparator (100 mV, tps25940a.md p.5-6) turns it on when the buck stops. This replaces the
  "switch after U680 PGOOD + fast switch-back" idea: an EN-based switch-back restarts through the dVdT ramp (10 nF,
  1 uA: tens of ms) while VIN20 collapses in < 1 ms at full load. Consequence: a host above 5.096 V (USB 5.25 V,
  Type-C 5.5 V max) shares the PD-mode load up to the eFuse level it advertises, never more. U680 PGOOD stays nc.
  [unverified] forward-recovery time of the TPS25940A after reverse blocking (text not read).
- pd_plug (host 5.0 V, adapter 0 -> 20 V in 2 ms): 5V_SYS min 4.851 V (= the bus-mode level; limit 4.75 V,
  GL3523). pd_unplug: 4.834 V. pd_plug runs with `option gmin=1e-10` and a 5 us max step (it stalled on the audio
  A-weighting node at the plug edge without them).

### S9: GL3523 straps, hot socket, discharge
- R111, R112, R113 (HUB1_PWREN1-3), R159 (HUB2_PWREN1) 47 k to GND: AMBER2 = 1 (R105 / R155 pull-ups kept) = active-
  high enables that need pull-downs (Table 4.1 p.34, per review). PGANG R104 / R154 10 k -> 220 k (p.32).
- Hot socket: TPS2553 EN gated by attach: M708 / M709 (gates RD1, CMPO) pull Cn_NATT low when an Rd is on either CC;
  Cn_NATT high (R735 / R785) turns M710 / M760 on and pulls Cn_BUSEN low. Ra alone (cable without sink) is no attach.
- Discharge: M711 / M761 (gate Cn_NATT) and M712 / M762 (gate Cn_VHI) through R736 / R786 1 k: 11 uF from 9 V to
  0.8 V in 1 k x 11 uF x ln(9 / 0.8) = 27 ms (tSafe0V 650 ms).
- bus_nosink: VBUS_C1 max 0.111 V without Rd (limit 0.8 V); bus_detach: VBUS_C1 < 0.8 V 19.1 ms after the Rd is
  removed (limit 650 ms); orient_bus_ra2: VBUS_C1 4.618 V with Rd + Ra (>= 4.45 V).

### S10: BC1.2 CDP in bus mode (documented, no change)
- USB-A: TPS2546 CTL1-3 = 1 and ILIM_SEL = PD_STAT: bus mode = 1110 = SDP1 (no CDP handshake, ILIM_LO), PD mode 1111
  = CDP with ILIM_HI (Table 3 p.28 as recalled: [unverified], re-read).
- GL3523 downstream (C ports, A ports' data): the hub's BC1.2 CDP default could not be checked. Residual risk: a
  phone that sees CDP asks for 1.5 A; the TPS2553 holds 99-137 mA (constant current, no latch), VBUS sags, the
  device backs off. Removal: SPI flash with a Genesys config that disables CDP (~$0.10 + Genesys tool) or SMBus;
  ask the Genesys FAE with the design-guide items.

### S12: three free-running 400 kHz bucks (documented, not implemented)
- LM61460 pin 7 is EN/SYNC. From the family datasheet (memory, [unverified]: lm61460.md has no sync page) the AFS
  part synchronises to an external clock and its spread spectrum is then off. Needs a 400 kHz clock (no RP2040 pin
  free; oscillator ~$0.10-0.30) AC-coupled onto three EN/SYNC pins on top of the EN levels (and the M3 pull-down).
  Cheaper alternative: detune (U680 400 kHz, U700 ~470 kHz RT 28.0 k, U750 ~540 kHz RT 24.3 k): fundamentals' beats
  >= 50 kHz with +-2 % dither, slightly higher switching loss. Not done: needs the sync page and the part 2 coupled-
  noise result.

### S14: PD input D+/D- ESD
- D605 PUSB3FR4Z on PDIN_DP / PDIN_DM (same part as the other data connectors). Pins 8/9 left connected: ch224d.md
  does not say DP/DM may float.

### S8: 20 V adapter below 65 W (product question, NOT implemented)
Today the CH224D requests 20 V whatever the current; a 45 W (2.25 A) or 30 W (1.5 A) adapter accepts and is
overloaded at the 62.6 W full load (adapter OCP -> hiccup; with the S7 OR the keyboard falls back to the host and
charging toggles).
- (a) CH224Q / CH224A (I2C 0x22/0x23, 32 V, PG pin; ~$0.25, CH224D price) + firmware budget. Needs 2 I/O for I2C
  (none free on the RP2040; the XU316 I2C bus could host it: part 2 decision) and a way to cut fast charge. The
  IP2736U PDO list cannot be limited by a pin (firmware property of the _EF variant). Levers that exist now: pull
  C1_EN / C2_EN low (port off; one 2N7002 + 1 GPIO per port) or break the FB sink (series FET in R704 / R754:
  forces 5 V, but the IP2736U sees a contract under-voltage and resets the link). ~$0.05-0.10 BOM + 2-4 pins +
  firmware; with a 45 W adapter one C port goes 5 V-only or off. [unverified] whether the CH224Q reports the source
  PDO list or only the agreed voltage.
- (b) Label / manual "65 W USB-C PD adapter required": $0; smaller adapters hiccup at high load.

### Build result (2026-10-23 10:51, build --until sim --mc 50)
check 0 errors / 0 warnings, ERC 0 / 0, SPICE 215/215 pass, requirements 34/35 verified (R36 could, not planned).
Monte Carlo 50: every analysis 100 % except bus_floor 98 % (run 38: ihost_floor 0.9000 A at the 0.900 A limit;
the R19 floor case has zero margin to the eFuse minimum by the product arithmetic, see M2). Derating: C701 / C751
9 V model artifact as before. Budgets unchanged (vc1_floor 4.563, vc1_5v 4.892, va1_pd 4.981 V, pin_pd_w 62.84 W
equal the pass B numbers they were derived from).

### BOM delta
$103.96 -> $104.52 per board (+$0.56): 3 x LMV339 replacing 2 x LMV331, 16 x 2N7002 added (M650 removed), D605, ~45
passives. LMV339IPWR price is an estimate (parts search offline); LCSC numbers for the LMV339 and the 100 V 0603
10 nF at the BOM stage.

## Stage 5 fixes, audio (design review part 2 of 2, 2026-10-23)

Scope: M4 (encoder), S11 (mic preamp supply), S13 (pop / thump), S12 audio side (jack filter, buck detune), S15
(MC false points). Edits: `scratch/s5a_edit.py` (one-shot, applied; backup `scratch/design.pre_s5a.yaml`), then
hand edits. New parts in `# ==== STAGE 5 AUDIO FIXES` before `PASS C COMPONENTS END`; new analyses in
`# ==== STAGE 5 AUDIO ANALYSES` before `PASS C ANALYSES END`. Power refs touched: only R700 / R750 (RT) and the
testbench lumped -rail loads. No datasheet PDF could be opened in this session (no WebFetch tool): figures tagged [M]
must be re-read before release (OPA1622 EN threshold and shutdown output state, LM61460 RT curve above 400 kHz).

### M4: encoder bushing and ESD (R15)
- Symbol `kb_keys:RotaryEncoder_Switch_MP` (lib/symbols/kb_keys.kicad_sym, made by scratch/s5a_mksym.py): a copy of
  Device:RotaryEncoder_Switch plus pin MP (passive), mapped to the two MP pads of
  Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm (`lib pads`: A, B, C, MP x2, S1, S2). SW501.MP = GND:
  the metal shaft / bushing is grounded at the front strip (the encoder sits next to the audio corner; GND, not AGND,
  so ESD current does not cross the single AGND tie R900).
- D501 PUSB3FR4Z (same 4-channel array as D303, USON-10, TPD4E05U06DQA symbol): 1 ENC_A, 2 ENC_B, 4 ENC_SW, 3/8 GND.
  Rated IEC 61000-4-2 +-8 kV contact class like the USB data lines it already protects [M]; its pF load is nothing
  next to the 10 nF RC caps C501 / C502. Layout: D501 between the encoder pins and R501-R503.

### S11: mic preamp U980 on +-4.5 V
- U980 V+ stays ADC_4V5 (4.53 V, switched by ADC_PWR_EN with the ADC), V- moved from AGND to MIC_VN: 9.04 V total
  (simulated, mic_supply_min) against the OPA1612 minimum 4.5 V. ADC_4V5 at the LT3045 SET low end (4.44 V) still
  gives 8.97 V.
- MIC_VN is switched from AUD_N4V5 so the preamp draws nothing while the ADC is off (bus floor case R19 has zero margin,
  0.8999 A at the 0.90 A limit): M981 AO3400A (source AUD_N4V5), gate MIC_VNG held at AUD_N4V5 by R995 100 k, pulled
  to ~0 V by M982 AO3401A (source ADC_4V5, gate AGND) through R996 100 k when ADC_4V5 is up: Vgs 4.5 V on, 0 V off.
  C999 10 nF slows the gate (tau 0.5 ms with 50 k) so C997 1 uF + C998 100 nF charge at ~9 mA, not an inrush step on
  the headphone -rail. Divider current 45 uA while on.
- DC coupling kept: the preamp works around MIC_CM (ADC_4V5 / 2) as before; the new -rail only adds headroom.
- Found by the new full-scale test: R994 (gain-leg bleeder) went from MIC_SW to AGND. At minimum gain MIC_SW follows the
  signal (the 4 x 100 uF couple it), so a 1 Vrms input swung it below -0.6 V, the AO3400A body diode switched the
  +40 dB leg in and the output rail-clipped (PA -3.8 / +3.7 V). R994 now goes to MIC_CM: MIC_SW stays 0.8-3.7 V.
  Consequence: the gain leg caps now sit at ~0 V with the FET off, so a gain change makes a 2.26 V step on the leg
  (output clips for ~1 s, tau 400 uF x 1.7 k). Firmware rule: mute the ADC samples for 2 s after a MIC_GAIN change.
  Gain changes are a user setting, rare.
- Lumped -rail load (bookkeeping, allocation unchanged): U980 Iq 7.2 mA now on the -rail when the ADC is on, so
  VILDN 0.0554 -> 0.0482 (default) and bus_heavy 0.0154 -> 0.0082 A. Bus floor runs with the ADC off: unchanged.

| analysis (new) | result 0 / 40 C | limit | req |
|---|---|---|---|
| mic_fs_min, 1.000 Vrms at the tip, minimum gain | pos 0.825 / 0.825 V, neg 5.34 V, supply 9.04 V | >= 0.5 V, >= 4.5 V | R11 |
| mic_fs_max, 10.0 mVrms at the tip, +40.4 dB | pos 0.839 / 0.858 V, neg 5.14 V | >= 0.5 V | R11 |
ADC differential drive at full scale 2.03 Vrms (min gain) / 2.12 Vrms (max gain): the ES9822PRO full-scale input is
not read (B6). Noise unchanged: mic_in_noise_min 0.9475 uV (40 C, limit 0.9999), mic_ein_max 0.261 uV (limit 1.0).

### S13: pop / thump
- OPA1622 EN (U913.8) moved from HP_TN to HP_EN: R936 100 k pull-up to 3V3_A, C936 100 nF (10 ms release ramp, contact
  debounce), pulled low by M931 2N7002 (gate AUDIO_MUTE) or M932 2N7002 (gate JACK_DET, high = jack empty). EN =
  plug present AND NOT AUDIO_MUTE. JACK_DET (M930 / R935, to the RP2040) is unchanged: M932 only loads its gate.
- AUDIO_MUTE = XU316 pin 39 (X1D22, IOR 3.3 V bank, was nc), R937 100 k pull-up to 3V3_D: muted while the bridge is in
  reset or unprogrammed. Firmware rule: drive AUDIO_MUTE low >= 1.3 s after DAC_EN rises (and after the rails are
  up); drive it high before DAC_EN falls, before a sample-rate / clock switch and before suspend.
- Why a GPIO and not an RC + Schmitt from power-good: the thump is caused by DAC_EN, which the firmware drives, so only
  the firmware knows when it happens; an RC from the rails would not cover a DAC re-enable. The pull-up is the
  hardware default (mute until firmware says otherwise).
- DC blocks kept (330 uF T520B): a DC servo removes the caps but puts the DAC bias 1.65 V / Rdac on the I/V stage, i.e.
  4.2 mA per leg through Rf (1.6 V DC per I/V output, 33 mA in the 100 ohm diff network: the pass C Vg = AVCC/2 trial),
  or needs ~1 k bias-cancel resistors whose 3.9 pA/rtHz noise adds ~7 % to the I/V noise: R10 has 3.4 % left
  (0.9667 / 0.9999). Film / C0G at 330 uF does not exist; the 1.24 Hz corner is needed for R31 at 20 Hz. Leakage is
  now modelled (RLK910, sim-only) as 0.01 CV at 6.3 V = 20.8 uA, i.e. 303 k, on one leg only (worst asymmetry).
- hp_thump (32 ohm, 25 C, worst mismatch: Rdac +15 % / -15 % p.21, DC blocks +20 % / -20 %, leakage on one leg; DAC
  bias 0 -> 1.65 V in 10 us at 1 ms; mute released at 1.3 s):

| probe | value | note |
|---|---|---|
| dbg_thump_unmuted_mvpk (OPA1622 input, what the jack would see without the mute) | 506 mV | review estimate 0.2-0.5 V |
| dbg_thump_muted_mvpk (jack, 0-1.29 s) | 0.35 mV | OPA1622 off, 10 k to AGND |
| dbg_thump_mvpk (jack, 1.3-1.4 s after release) | **3.06 mVpk** | target 10 mVpk |
| dbg_thump_residual_mv / end | 3.03 / 2.59 mV | 2.1 mV of it is the static leakage offset |
  The 10 mVpk target is the review's number; no requirement covers pops (R10 is the noise floor, R29 the level), so
  the measure is a debug probe. Basis: 10 mVpk into 32 ohm = 3 uW peak, -43 dB re the 1 Vrms R10 reference.
  **Product question:** should a pop limit become a requirement (then hp_thump becomes a measure)?
  [M] the model's OPA1622 output is high-Z when disabled (TI: output in shutdown, to be read).

### S12 (audio side): 400 kHz tone at the jack, buck detune
- The ~470 uVpk "tone" was a testbench error: r22_tones switched off IACLP but not IACLN, so the reported value was the
  DAC test signal (AC 0.5) through the I/V chain at 400 kHz, not supply coupling. With IACLN off: 3.46e-5 uVpk at
  400 kHz and 9.3e-6 uVpk at 1.38 MHz for 10 mVpk on every audio supply entry (-169 dB). r22_tones fixed.
- So the jack RC / LC was NOT added: it would fix nothing measurable and costs R31 margin. The 20 kHz end already sits
  at -0.065 dB of the -0.0969 dB budget (the I/V and diff-to-SE poles at 231 / 234 kHz); a filter with >= 20 dB at
  400 kHz and <= 0.03 dB at 20 kHz is a 2nd-order section at ~70-80 kHz whose Q depends on the load (32 / 300 ohm /
  open), i.e. a Zobel network too. Not justified by a 3.5e-5 uV tone.
- Detune (LM61460 RT, lm61460.md p.6: 33.2 k = 400 kHz (360-440), 66.5 k = 200 kHz; fit f = 13320 / (RT_k + 0.1)):
  U680 R682 33.2 k 400 kHz (unchanged); U700 R700 33.2 k -> 24.9 k = 533 kHz; U750 R750 33.2 k -> 18.7 k = 708 kHz.
  Spread per part: datasheet +-10 % x spread +-2 % = 0.882 ... 1.122 x nominal.
  | pair | fundamental ranges (kHz) | beat range (kHz) |
  |---|---|---|
  | U700 - U680 | 470-598 vs 353-449 | 21-245 |
  | U750 - U700 | 625-795 vs 470-598 | 27-325 |
  | U750 - U680 | 625-795 vs 353-449 | 176-442 |
  Every fundamental beat is above 20 kHz for every part combination. Residual: 2 x U680 (706-898 kHz) overlaps U750;
  no RT choice avoids every 2nd-harmonic beat with +-12 % per part. Losses [E]: switching part ~0.14 W at 400 kHz,
  +0.05 W (U700) / +0.11 W (U750), about +2.4 C Tj at 22 C/W; the averaged fits have fixed efficiency, so the power
  measures do not show it (pd_9v tj_u750 80.7 C at 40 C, unchanged). [M] LM61460 internal compensation at 700 kHz:
  the TI table lists COUT for 400 kHz; a higher fsw only raises the margin, re-check with the datasheet table.
- Power measures re-run: all pass, unchanged (pd_9v pin 62.84 W, pd_5v vc1 4.892 V, bus_floor 0.8999 A, bus_heavy
  0.8973 A).

### S15: MC false operating points
- The part-1 MC (KLU op runs, new nodeset) has no false points: bias_source_v 2.319-2.323 V, hp_zout_max 0.466-0.473
  ohm, hp_vrms_32 2.000-2.004 V. bias_source_v was already two-sided (R33 2.0-3.0 V: a 1.71 V point fails);
  hp_zout_max had max 1.0 (10.3 k fails). hp_vrms_32 / hp_vrms_300 had only a minimum, so 118.7 V would have passed:
  now max 2.79 Vrms = (4.44 V rail at the ADP7118 low end - 0.5 V R29 headroom) / sqrt(2), the most a real output can
  reach. hp_zout_max also min 0.

### Results (stage 5 audio)
Direct run of all analyses (scratch/s5a_sim.py, same netlists as the build): 225 / 225 measures pass. R10 worst
hp_total_300 0.9667 uV (40 C, limit 1.0), hp_intrinsic_300 0.9667 (0.9999); R11 worst mic_in_noise_min 0.9475 uV
(0.9999), mic_ein_max 0.261 uV (1.0); R22 hp_coupled 1.55e-4 uV, mic 1.13e-5 uV (0.33). Build and MC: see below.

### BOM delta (stage 5 audio)
+$0.13: D501 0.044, M931 / M932 2N7002 0.031, M981 AO3400A + M982 AO3401A 0.04, R936 / R937 / R995 / R996 0.008,
C936 / C997 / C998 / C999 0.011. RT and R994 changes $0.
