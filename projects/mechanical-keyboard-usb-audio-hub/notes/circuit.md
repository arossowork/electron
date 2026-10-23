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
  - PGANG: superseded by stage 5 S9, R104 / R154 = 220 k to GND (individual over-current mode, p.32); the pass-A
    "10 k to GND, < 100 k down" reading was wrong. CHIPEN 10 k to V33, RESETJ 10 k to V33
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
  0.8 V in 1 k x 11 uF x ln(9 / 0.8) = 27 ms (tSafe0V 650 ms). [pass 5 correction: R736 / R786 are 2 k now and the
  node carries ~13 uF: 2 k x 13 uF x ln(9 / 0.8) = 63 ms, still << 650 ms.]
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
`# ==== STAGE 5 AUDIO ANALYSES` before `PASS C ANALYSES END`. Power refs touched: only R700 / R750 (RT). No datasheet PDF could be opened in this session (no WebFetch tool): figures tagged [M]
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

### S11: mic preamp U980 supply (NOT resolved: product / engineering decision)
- Tried: U980 V- switched from AUD_N4V5 (M981 AO3400A, gate pulled to ~0 V by a P-FET while the ADC is on; R995 /
  R996 100 k, C999 10 nF gate ramp, C997 1 uF + C998 100 nF on V-), V+ = ADC_4V5: 9.04 V total, headroom 0.83 V / 5.3 V,
  all nominal measures passed. Taken out again because of the bus-mode Monte Carlo: with the ADC off the preamp and its
  switch are unpowered, and the op-amp fit (output current taken from unpowered rail nodes) pumped ADC_4V5 / MIC_VN to
  +4..+7 V and relaxed (MC bus_floor 76 %, ihost +1.7..+4.5 mA). Three model / circuit variants (V- bleeder R997,
  gate driven from ADC_PWR_EN through a 2N7002 + P-FET from AUD_P4V5, a supply-gated op-amp fit) did not remove it,
  and R19 bus_floor has zero margin (0.8998 A at the 0.90 A eFuse minimum), so even a 0.1 mA artefact fails it.
  Needed to finish it: an op-amp fit whose output stage has no current path without supply (ESD-diode model), then the
  switch as above (+$0.10, 10 parts). Scripts: scratch/s5a_variants.py, s5a_mcruns.py.
- Today: U980 on ADC_4V5 / AGND as in pass C, 4.527 V simulated (mic_supply_min, limit 4.5 V: 27 mV margin). The
  LT3045 SET tolerance (+-1 % ISET [M] and R954 1 %) gives 4.44-4.62 V, i.e. up to 60 mV below the OPA1612 minimum
  (+-2.25 V [V]). Cheapest alternative: R954 45.3 k -> 46.4 k (4.64 V nominal, 4.55-4.73 V), only if the ES9822PRO
  AVCC maximum allows 4.73 V (datasheet not read, B6).
- Fixed, found by the new full-scale test: R994 (gain-leg bleeder) went from MIC_SW to AGND. At minimum gain MIC_SW
  follows the signal (the 4 x 100 uF couple it), so a 1 Vrms input swung it below -0.6 V, the AO3400A body diode
  switched the +40 dB leg in and the output clipped. R994 now goes to MIC_CM: MIC_SW stays 0.8-3.7 V. Consequence:
  the gain-leg caps sit at ~0 V with the FET off, so a gain change makes a 2.26 V step on the leg (output clips for
  ~1 s, tau 400 uF x 1.7 k). Firmware rule: mute the ADC samples for 2 s after a MIC_GAIN change (a rare user setting).
- Full-scale headroom on the single supply is enough: the preamp is DC-coupled around MIC_CM = 2.26 V, 1 Vrms swings it
  0.83-3.70 V.

| analysis (new) | result 0 / 40 C | limit | req |
|---|---|---|---|
| mic_fs_min, 1.000 Vrms at the tip, minimum gain | pos 0.825 V, neg 0.826 V, supply 4.528 V (MC 4.524-4.531) | >= 0.5 V, >= 4.5 V | R11 |
| mic_fs_max, 10.0 mVrms at the tip, +40.4 dB | pos 0.895 / 0.904 V, neg 0.689 / 0.690 V | >= 0.5 V | R11 |
ADC differential drive at full scale 2.03 Vrms (min gain) / 2.12 Vrms (max gain): the ES9822PRO full-scale input is
not read (B6). Noise unchanged: mic_in_noise_min 0.9475 uV (40 C, limit 0.9999), mic_ein_max 0.261 uV (limit 1.0).

### S13: pop / thump
- OPA1622 EN (U913.8) moved from HP_TN to HP_EN: R936 100 k pull-up to 3V3_D (same logic rail as JACK_DET and
  AUDIO_MUTE; on 3V3_A the SPARSE op of mic_noise_max at 0 C failed; the amp also mutes first if 3V3_D drops at
  power-down), C936 100 nF (10 ms release ramp, contact
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

### Simulation housekeeping (stage 5 audio)
- Nodeset: v(mic_sw) 0 -> 2.264675 (R994 now holds MIC_SW at MIC_CM; the old 0 V hint sent mic_noise_max at 0 / 40 C
  to "out of range for pwr" Newton errors).
- sim.derating_cmd KLU -> SPARSE: KLU "DC solution failed" once HP_EN existed; SPARSE gives the pass-C result (only
  C701 / C751, the bootstrap-cap model artefact). scratch/s5a_derate_var.py has the comparison.
- The `hwflow check` stage takes ~31 min on this board (barrier check: design.nets() rebuilt for each of 2857 forced
  pads x 520 nets, src/hwflow/barrier.py _nodes); an engine item, not a design one.

### S15: MC false operating points
- The part-1 MC (KLU op runs, new nodeset) has no false points: bias_source_v 2.319-2.323 V, hp_zout_max 0.466-0.473
  ohm, hp_vrms_32 2.000-2.004 V. bias_source_v was already two-sided (R33 2.0-3.0 V: a 1.71 V point fails);
  hp_zout_max had max 1.0 (10.3 k fails). hp_vrms_32 / hp_vrms_300 had only a minimum, so 118.7 V would have passed:
  now max 2.79 Vrms = (4.44 V rail at the ADP7118 low end - 0.5 V R29 headroom) / sqrt(2), the most a real output can
  reach. hp_zout_max also min 0.

### Results (stage 5 audio)
Build 2026-10-23 14:59 (`build --until sim --mc 50`): check 0 errors / 0 warnings, ERC 0 / 0, SPICE 225 / 225, MC 50
every analysis 100 % (bus_floor ihost 0.8995-0.9000 A), derating only C701 / C751 (known artefact), requirements
34 / 35 (R36 could, not planned). R10 worst hp_total_300 0.9667 uV (40 C, limit 1.0), hp_intrinsic_300 0.9667
(0.9999); R11 worst mic_in_noise_min 0.9475 uV (0.9999), mic_ein_max 0.261 uV (1.0); R22 hp_coupled 1.55e-4 uV, mic
1.1e-5 uV (0.33). hp_thump 3.06 mVpk (unmuted 506 mV).

### BOM delta (stage 5 audio)
+$0.08: D501 0.044, M931 / M932 2N7002 0.031, R936 / R937 0.004, C936 0.003. RT and R994 changes $0. (The switched
U980 V- would add ~$0.10, not in the design.)

## Stage 5 fixes, delta 1 (delta review D1-D12, 2026-10-23)

### D1 (must): U980 mic preamp common mode and supply
- OPA1612 input CM window (V-)+2 V to (V+)-2 V (SBOS450C p.5) was violated with the inputs at MIC_CM 2.26 V on a
  4.5 V / 0 V supply, and the 0 V V- rail left 4.5 V total (min 4.5 V, zero headroom). Now: U980 inputs referenced to
  AGND; V- = MIC_VN, a switched -4.5 V (M981 AO3400A from AUD_N4V5, gated on by M982 AO3401A when ADC_4V5 is up, so
  the op-amp never sees V- without V+); outputs AC-coupled (C990 / C991 10 uF) and re-biased to MIC_CM through
  R995 / R996 10 k 0.1 % into the ADC. Supply span 9.05 V. C992 1 uF + C993 100 nF decouple MIC_VN.
- Fit model: OPA_SEC has VCM / ERRCM (CM distance node cmh; error grows outside the window) and a supply-span gate
  (output off below 2 V span) and a substrate diode V- -> V+ (stops the unpowered back-feed / MIC_VN float that made
  the bus-mode MC land on false points). Measures mic_cm_a_min / mic_cm_b_min / mic_cm_a_max (cmh >= 0),
  mic_supply_min (ADC_4V5 - MIC_VN >= 4.5 V).
- Results (25 C fast sims): CM margin a 1.107 V, b 2.518 V (fs_min), a 2.505 V (fs_max); supply 9.05 V; headroom
  +3.09 / -3.08 V (fs_min), 3.01 / 3.00 V (fs_max); ADC 2.01 Vrms; mic_in_noise_min 0.9286 uV (limit 0.9999),
  mic_ein_max 0.255 uV.

### D2 (must): host back-feed into the FPWM buck
- Host 5.25-5.5 V > U680 5.1 V: the TPS25940A ideal diode conducted and the forced-PWM LM61460 sank it into VIN20.
  Now a series P-FET M657 AO4407A (source on EFUSE_OUT, drain on 5V_SYS: the body diode conducts only 5V_SYS -> EFUSE_OUT, so it blocks the host) is held off
  in PD mode: B5_PG = PD_STAT AND U680 PGOOD (R675 1 M pull-up from PD_STAT, PGOOD open drain) -> M660 -> M658
  pulls HF_G to EFUSE_OUT. On PD unplug PGOOD (94 %) or PD_STAT drops and R673 turns M657 on (S7 brown-out kept away:
  pd_unplug 5V_SYS min 4.83 V). EFUSE_OUT is a new net (C656 moved to it).
- BUCK_FB fit: ISNK param (smooth negative-current limit, U680 ISNK 3 A) so the FPWM sink is modelled.
- pd_host_525 / pd_host_55 (C ports idle): host current 1.40 / 1.48 mA (limit 10 mA), buck reverse current none.
- D11 rejected (see below). Sim-only convergence aid: BKFIX (1 kS from HF_G to EFUSE_OUT, kfix = 1000 only in the
  op-based audio / derating analyses, which are all PD mode where M657 is off anyway). 1 S was not enough (op landed on
  the dead-board point, 5V_SYS 0.28 V).

### D3 (must): shedding
- bus_floor now 75 mA per port, bus_heavy 60 mA (no shed expected); corner_max_def_hw puts ports at datasheet MAX.
- R664 is a pull-up to 3V3_D (PWR_SHED high = shed until firmware clears it). C657 removed.
- Hardware backstop: eFuse PGOOD (PGTH divider R676 100 k / R677 28.7 k: 4.13 V) low -> M661 -> R659 47 k / C660
  220 nF (10 ms, port-enable inrush does not latch) / R658 10 M (2.2 s hold) -> SHED_HW -> M663 / M664 pull C2_BUSEN
  and A2_EN low. M662 clamps SHED_HW in PD mode (PD_STAT high). PGTH threshold assumed equal to EN (0.99 / 0.92 V)
  [unverified, tps25940a datasheet p.? to confirm].
- Heavy case (R19, 900 mA eFuse min trip): internal 657 mA + 4 x 60 = 897 mA holds without shed by 3 mA. Largest
  per-port floor at zero margin (900 - 657) / 4 = 60.75 mA. Firmware IMON (gain +-9 %, offset ~15 mA, uncalibrated):
  threshold must be <= 0.804 A, so the largest no-shed load is 0.724 A = 16.7 mA per port; calibrated (+-1 %, ~2 mA)
  ~55 mA per port. Firmware rule: shed at IMON 0.4764 V (0.92 A calibrated); 3 A host: shed above 2.95 A.
- corner_max_def_hw (ports at MAX, hw shed): host 0.761 A (<= 0.9); corner_max_3a_worst (shed): 1.82 A (<= 3.0).

### D5 (should): TPS2546 max formula
- Corrected to I = 55325 / R^1.0139 + 30 mA with R at -1 % (notes/datasheets/tps2546.md). Max corners 140.6 / 306.5 /
  627.7 / 1936 mA. 3 A host sum 2 x 583 + 2 x 628 + 483 = 2.905 A + 600 internal = 3.02 A > 3.0: shed needed on a
  3 A host (corner_max_3a_worst).

### D6 / D7 (should)
- C729 / C779 100 nF on C1_NATT / C2_NATT. pd_burst (BMC 1 ms bursts on CC): NATT max 0.319 V (limit 1.0),
  VBUS_C1 min 8.91 V (8.55).
- R669 11 k -> 6.65 k: CC_VL 0.299 V -> CC comparator at 0.60 V. pd_txng (SinkTxNG, 1.5 A Rp 180 uA): NATT 0,
  orientation 0.002 V. pd_plain_cable (Rp voltage-limited to 3.0 V open): orientation 3.25 V (>= 2.8), NATT 0.
  The open-CC voltage of a real source/cable is an assumption and a release blocker until bench-checked.

### D8, D10, D11, D12
- D8: notes/layout-constraints.md LC1 (Cn_RTN pour, stitching-cap rows, shunt the only DC tie).
- D10: R736 / R786 1 k -> 2.0 k 0603.
- D11 not done: HOST15 / HOST30 / PD_STAT are 100 k pull-up nodes; the ladder / 10 (33 k) loaded PD_STAT to 1.5 V,
  so the host FET stayed on in PD mode (ihost 0.70-0.99 A). C658 100 nF feeds the ADC sample cap instead.
- D12: L750 ripple (20 - 9) x 9 / 20 / (708 kHz x 8.2 uH) = 0.853 A p-p = 38 % of 2.22 A, 14 % of the 6 A limit;
  peak 2.65 A. The TI COUT table is for 400 kHz [unverified at 708 kHz].
- Comments: gl3523.md l.31 PGANG (220 k, p.32), circuit.md l.69 superseded, HP_EN comment 3V3_D.

### Results (delta 1, build 2026-10-23 `--until sim`, MC not run): FAIL, open
- check OK (0 errors, 2 warnings); SPICE 211 / 257. 46 fails in two groups:
- Power (real): bus_floor ihost 0.9018-0.9021 A (limit 0.900; series M657 loss through constant-power loads, the
  R19 floor has zero margin); shed_hw 0.77-1.00 V (limit 0.5): the hardware shed path half-turns on at the floor
  (eFuse at its 0.90 A trip). As a result, bus_heavy ihost 0.911 A and imon 0.4767 V, bus_3a ic2 0.195 A / ia2 0.484 A
  and corner_min_3a ic2 0.259 A (C2 / A2 shed on a 3 A host), bus_nosink vbus_c1 1.58 V (limit 0.8),
  corner_max_def_hw 0.987 A (fast sim gave 0.761 A).
- Audio (0 C / 40 C only; the 25 C runs pass): mic_noise_min/max @ 0 C, mic_bias / hp_level / hp_response /
  hp_zout / mic_fs @ 40 C are dead-board operating points (bias 0 V, -42 V headroom), the same false op as before
  the kfix aid. The kfix aid does not hold at the other temperatures.

## Monte Carlo aborts: numerical, not design failures (PM note 2026-10-23)

The product person accepts the nominal results (180/180 at the end of pass C) as the stage 4 evidence and asks not to spend more MC rounds on solver convergence. Every MC failure of the pass C build (build of 2026-10-23 04:46, `--mc 50`) was a solver abort or a numerical error in the behavioral/fit models (timestep too small, singular matrix, "out of range for pow/pwr", one 600 s timeout), not a measure outside its limit:

| analysis | seeds | failure |
|---|---|---|
| bus_limits | 20, 37 | timestep too small (vmicg#branch) |
| bus_floor | 11, 12, 16, 43 | timestep too small (vmicg#branch, ivs_ln, xfb952.x1) |
| bus_1a5 | 8, 13, 28, 37, 44 | timestep too small (vmicg#branch, xfb952.x1) |
| bus_3a | 5, 14, 19 | timestep too small (vmicg#branch) |
| mic_bias | 14, 25, 34, 36 | singular matrix / out of range for * and pwr |
| hp_level_32 | 14, 15, 24, 34, 36 | out of range for pwr / * (singular matrix xu951.o1) |
| hp_level_300 | 1, 11, 13, 31, 42 | run 1 timeout 600 s; others out of range for pwr/pow |
| hp_response_32 | 14 | out of range for * (singular matrix xu952.o1) |
| hp_response_300 | 35 | out of range for * (singular matrix xu950.a2) |
| hp_zout | 11, 12, 49 | out of range for pwr / ^; run 12 transient op failed |

The pass C solver settings (KLU/SPARSE per analysis, sim-only 1e9 ohm paths, ferrite 120 ohm, gear) removed these aborts (MC 100 % after stage 5 fix passes 1-2). Classed as numerical; not used as design evidence either way.

## Stage 5 fixes, pass 4 (PM round 7, 2026-10-23)

### q1: eFuse R_ILIM (R654) for the 0.911 A worst legitimate load and the 1.13 A ceiling
TPS25940A IOS typ = 87.5 / R(k) A; window -9 % / +8.5 % (p.5 table at 88.7 k: 0.90 / 0.99 / 1.07 A; notes give
-9 / +8 %, the wider side of each is used). With resistor tolerance t:
- min trip >= 0.911 A: 0.91 x 87.5 / (R (1 + t)) >= 0.911 -> R (1 + t) <= 87.40 k
- max trip <= 1.13 A: 1.085 x 87.5 / (R (1 - t)) <= 1.13 -> R (1 - t) >= 84.02 k
- 1 %: 84.87 k <= R <= 86.53 k: no E96 value (84.5 k max 1.135 A; 86.6 k min 0.9104 A < 0.911); E192 85.6 k fits
  (0.921 / 1.022 / 1.120 A). 0.1 %: 84.10-87.31 k.
- Chosen R654 86.6 k 0.1 % (0402, ~$0.01): min 0.91 x 87.5 / 86.687 = 0.9185 A, typ 87.5 / 86.6 = 1.010 A, max
  1.085 x 87.5 / 86.513 = 1.0974 A. Typical closest to the designed 0.99 A of the candidates; min 7.5 mA above
  0.911 A, max 33 mA under 1.13 A (no margin added: the product numbers are the limits).
- Other levels: 1.5 A host 86.6 // 133 k = 52.45 k -> typ 1.668 A (min ~1.52 A); 3 A host 26.19 k -> typ 3.341 A,
  min 0.91 x 87.5 / 26.19 = 3.04 A (R655 / R656 stay 1 %; their spread is not in the corner model).
- Model: EFUSE_FIT corner node `kef` (0 typ, 1 max = x 1.085 / 0.999, -1 min = x 0.91 / 1.001); knee exponent 8 ->
  24 (the p = 8 knee cut the current ~8 % under IOS, so the typical model already "limited" softly at 0.90 A); internal
  nodes clamped at 1000 (pwr() out-of-range on diverging iterates).
- Measures: bus_trip efuse_lim_typ [0.911, 1.13] A (0/25/40 C), bus_trip_min efuse_lim_min >= 0.911 A, bus_trip_max
  efuse_lim_max <= 1.13 A.

### R19 measures as the acceptance reads now
- bus_floor / bus_heavy (eFuse typ, 0/25/40 C): VBUS >= 4.45 V; host draw <= 0.9185 A (the new minimum trip; the
  0.900 A nominal-sharing target stays in the realism text and in dbg_internal_*); efuse_pg_* >= 2.0 V (PGOOD high,
  the eFuse is not limiting); shed_hw_* <= 0.5 V (under the 2N7002 1.0 V min Vth); imon_fw_* <= 0.4772 V.
- bus_floor_mintrip (new, 25 C): bus_floor with the eFuse at its minimum IOS (kef = -1), power-up port-enable
  inrush included: same limits. The heavy case at the minimum trip is not simulated from power-up: the heavy load
  needs enumeration (audio stream, mic recording) and never coincides with the power-up inrush (stress case); its
  steady state is covered by ihost_heavy 0.911 A <= 0.9185 A. (The kef = -1 heavy power-up run did not finish in
  550 s: the 7.5 mA spare current makes the start-up inrush ~70 ms long and the transient crawls.)

### Firmware IMON rule: secondary, calibration-free
Threshold above the worst legitimate reading of an uncalibrated unit: IMON gain up to 57.2 / 52.3 = 1.094, R657
+1 %, offset +15 mA: 0.9125 A x 0.572 V/A x 1.01 + 0.015 x 0.572 x 1.01 = 0.536 V. Firmware sheds C2 / A2 when
HOST_IMON > 0.536 V for > 10 ms (default host level only; never in PD mode or on a 3 A host). On a low-gain unit
the threshold can sit above the eFuse trip: there the hardware path below acts first. Nominal-gain equivalent of the
threshold for the testbench: (0.536 - 0.0087) / 1.094 = 0.4820 V (corrected in round 7 MC: R657 is sampled by MC, so
the earlier extra / 1.01 giving 0.4772 V was dropped). No production calibration needed. The earlier calibrated
0.4764 V rule is withdrawn. [pass 5: the RP2040 ADC error is added below; the rule becomes secondary.]

### Hardware shed (SHED_HW)
Root causes found in the delta-1 failures:
- shed_hw 0.77-1.00 V at the floor: not steady-state limiting. The hub enables all ports at 41 ms; the port caps
  (2 x 11 uF + 2 x 44 uF) charge with the eFuse in current limit for ~3 ms (typ) to ~30 ms (min trip), PGOOD drops
  and R659 47 k x C660 220 nF (10 ms) charged SHED_HW, then R658 10 M held it (2.2 s) through the 140-150 ms window.
- bus_3a / corner_min_3a shed C2 / A2: on a 3 A host EFUSE_OUT is ~4.21 V at 2.77 A (2 x 75 mohm cable + Ron),
  under the PGOOD threshold, so PGOOD was low in a legitimate state.
- corner_max_def_hw 0.987 A (build) vs 0.761 A (fast sim): the overload has two operating points. Once the eFuse
  limits, 5V_SYS sags to ~3.9 V, the constant-power internal loads draw more, and 3V3_D collapses (1.04-1.08 V
  average): the 3V3_D-powered shed driver (M661, R678) and the MCU cannot act, and with only C2 / A2 off the rest
  still needs the full eFuse current (host 1.0 A, 5V_SYS 3.92 V with C2 off). The build landed on this latched
  point; the delta-1 fast sim started from a state where the shed had acted before the sag (high point, 0.761 A).
Changes:
- R659 47 k -> 1 M (C660 220 nF: 220 ms; on-level VBUS_HOST x 10 / 11 = 4.2 V): the power-up inrush leaves SHED_HW
  <= 0.43 V (typ) / 0.69 V (min trip, fast sim); a sustained limit reaches the 1.0 V min Vth after ~60 ms and
  2.5 V after ~200 ms.
- R678 (PGOOD pull-up) and M661 source from 3V3_D to VBUS_HOST: the shed driver works while 5V_SYS / 3V3_D sag.
  [PGOOD abs max assumed >= 7 V: tps25940a.md has no abs-max row, confirm p.4.]
- M665 (C1_BUSEN) and M666 (A1_EN, new R825 10 k in series from HUB1_PWREN3, U800.5 on A1_EN): the hardware shed
  turns all four bus-mode ports off; internal loads alone at 3.9 V (~0.62 A) sit under the 0.9185 A minimum trip,
  so the board leaves the latched point. Ports return after the ~2 s hold (auto-retry). Firmware shedding (IMON) still
  sheds only C2 / A2.
- M667 clamps SHED_HW on a 3 A host (HOST30 high): there the eFuse 3 A level minimum 3.04 A is above the worst
  all-ports-at-max sum 3.02 A, so it never limits legitimately.
- R677 28.7 k -> 29.4 k: PGOOD rising 4.36 V (was 4.44 V, too close to the legitimate 1.5 A-host EFUSE_OUT 4.45 V),
  falling 4.05 V (overload point 3.99 V below it). PGTH thresholds still assumed equal to EN/UVLO (unverified).
- Testbench nodeset: v(ef_pg) 3.33 -> 5.0 V (new pull-up rail), v(a1_en) added (without it the PD-mode audio op
  landed on the dead-board point at 25 C too).
- corner_max_def_hw runs to 520 ms (window 500-520 ms, timeout 1800 s) and checks shed_hw_max_def >= 2.5 V.

### bus_nosink (VBUS_C1 1.58 V)
Not a source path: C1_BUSEN stays at 0.4 mV. The shared .nodeset is the PD-mode point and `tran ... uic` started
VBUS_C1 at 9 V; R736 2 k x ~13 uF discharges it (9 V at 0, 2.1 V at 39 ms, 1.58 V at 45 ms). Window moved to
150-200 ms: 0.012 V (limit 0.8 V). No hardware change.

### Audio 0 / 40 C (one bounded attempt)
- 40 C (mic_bias, hp_level_32/300, hp_response_32/300, hp_zout, mic_fs_min/max): KLU lands on the dead-board point at
  40 C only (gmin 1e-10..1e-12, gmin / source stepping did not help); SPARSE converges there. Split into 40 C twins
  `<name>_40c` (same measures, suffix _40c, SPARSE); the original analyses keep KLU at 0 / 25 C.
- 0 C (mic_noise_min / max, SPARSE + noise; KLU has no noise): op check after `option sparse`; if AUD_P4V5 is not
  within 1 V of 4.5 V, gmin 1e-11 (converged in the test: mic_in_noise_min 0.889 uV at 0 C).
- Switching the solver after a failed op does not work (the second op lands on another false point): the solver is
  chosen per analysis, not by fallback.


## Round 7 verification by direct simulation (`hwflow sim`, 2026-10-23 evening, PM instruction)

The build started at 17:25 spent ~2 h in `hwflow check` and was stopped at the PM's request. The round 7 / pass 4 design was verified with `uv run hwflow sim` (509.6 s): 265/271 measures pass, ERC 0/0. All R19/R21 power and bus measures (bus_floor, bus_heavy, bus_floor_mintrip, bus_trip/_min/_max, bus_1a5, bus_3a, bus_limits(_shed), corner_min_*, corner_max_* except _def_hw, pd_*, orient_*, bus_nosink, pd_host_*) pass. BOM $105.12.

The 6 failing measures all come from analyses that aborted or errored in the solver (numerical, not design failures, per the PM note above):

| analysis @ temp | failure | values reported |
|---|---|---|
| corner_max_def_hw @ 25 C | TRAN timestep too small at 173.8 ms (nmos_logic mm660) | ihost_max_def_hw, shed_hw_max_def n/a |
| r22_coupled @ 40 C | out of range for pwr, singular matrix aud_p4v5 (dead-board op) | mic_coupled_min_gain 6.658 uV (passes at 0 / 25 C: ~1e-5 uV) |
| mic_noise_min @ 0 C | out of range for pow, singular matrix xu912/xu913 ks | mic_in_noise_min 7.8e4 uV (dead-board op) |
| mic_noise_max @ 0 C | same | gain -113.5 dB, EIN 2.8e5 uV (dead-board op) |
| hp_noise_32/300, hp_total_32/300 @ 0 / 40 C | out of range for pow/pwr (errored, no failing measure counted) | - |
| hp_thump @ 25 C | TRAN timestep too small (ivs_ln) | debug probe only |

Design risk carried to first article (not proven by simulation): the overload shed path (corner_max_def_hw: all four ports at their max limits, eFuse limiting, SHED_HW >= 2.5 V and host <= the trip window) aborts in the solver; the pass 4 fast sim shows the latched point is left. Bench test: load all four bus-mode ports to their switch limits on a 900 mA host and confirm the ports shed and return (~2 s) and 3V3_D stays up.
Also check: mic_bias, hp_level_*, mic_fs_* now run at 0 C and in 40 C twins only (no 25 C point).

### Monte Carlo of the round 7 power/bus analyses (scratch copy, MC only on pd_*/bus_* analyses, 50 runs)
- bus_floor 100 %, bus_trip 100 % (efuse_lim_typ 1.010-1.012 A), bus_1a5 100 %, bus_3a 100 %, bus_limits_shed 100 %; bus_limits 98 % (run 19 aborted: timestep too small, AO4407A), bus_1a5_shed 98 % (run 7 aborted): numerical.
- pd_9v / pd_5v 94 %: runs 3, 15, 31 converge to a false operating point (VBUS_C -8.8e-5 V, input 930 W from a 65 W adapter, Tj 1266 C): numerical, not design (same seeds in both analyses).
- bus_heavy 80 %: runs 1, 5, 7, 10, 18 (and 5 more) fail imon_fw_heavy only (0.4731-0.4802 V vs 0.4772 V). Limit construction error: the firmware threshold 0.536 V already includes the worst IMON gain (1.094) and the offset (8.7 mV); the old testbench limit also divided by 1.01 for R657, but MC samples R657 itself. Corrected derived limit for imon_fw_floor / imon_fw_heavy: (0.536 - 0.0087) / 1.094 = 0.4820 V. All 50 runs pass against it (max 0.4802 V); not re-run (values known).
- R19 heavy: ihost_heavy 0.911-0.9115 A vs eFuse min trip 0.9185 A; shed_hw_heavy <= 0.064 V (off). Floor: ihost 0.9017-0.902 A, shed off.

### hwflow check, time-boxed (PM instruction)
The stopped 17:25 build had already finished its check on the pass 4 design (status.json 19:19, design.yaml 17:24): 0 errors, 2 warnings (5V_SYS, MIC_VN power_in without a power_out driver), barrier/escape proofs included; the build was stopped during the simulations. The time-boxed re-run (`timeout 1800 uv run hwflow check`) did not finish in 30 min (exit 124), so it adds nothing. The two warnings are cleared with external_supply (5V_SYS fed through M657 / the buck inductor, MIC_VN through M981/M982). Later changes (these net flags, the imon_fw limits) do not touch geometry, footprints or nets, so the proof result stands. Check time is ~2 h on this board (37-58 min earlier in stage 5): a tooling issue to report.


## Stage 5 fixes, pass 5 (delta review 2, 2026-10-23 night; `hwflow sim` only, no check / build per PM)

### Finding 1 (must): no latch, real off-hold-retry
Root cause confirmed: the pass 4 SHED_HW only charged while PGOOD was low; the shed turned the ports off, EFUSE_OUT
recovered, PGOOD went high and SHED_HW stopped just above the shed-FET threshold, so it decayed back within tens of
ms (10-20 Hz port toggling, never a 2 s retry; shed_hw_max_def >= 2.5 V unreachable).
New circuit (all from VBUS_HOST, design.yaml group pwr_ctl):
- Detector SHED_HW: M661 (PGOOD low) -> R659 68 k -> C660 4.7 uF (CL31B475KBHNNNE, 1206 50 V X7R: little DC-bias
  loss at 3 V), R658 150 k discharge. On-level 4.6 x 150 / 218 = 3.17 V; tau 46.8 k x 4.7 uF = 220 ms; reaches 1.0 V
  (2N7002 min Vth) after 220 x ln(3.17 / 2.17) = 83 ms, 2.5 V (max Vth) after 220 x ln(3.17 / 0.67) = 342 ms. The 1 M /
  220 nF of pass 4 could not be kept: with any discharge resistor small enough for the M661 leakage (finding 3) the
  1 M source divides the on-level under the 2N7002 Vth (1 M / 150 k: 0.6 V).
- Hold SHED_HOLD: M668 2N7002 (gate SHED_HW) pulls the M669 AO3401A gate low (R679 100 k pull-up); M669 charges C661
  22 uF (CL31A226KAHNNNE) through R826 1 k (tau 22 ms, level 4.55 V); R827 100 k discharges it (tau 2.2 s). SHED_HOLD
  drives M663-M666 (all four bus-mode ports off).
- One-shot kick R828 1 k + C662 1 uF from SHED_HC (M669 drain) to SHED_HW: the 0 -> 4.6 V drain step lifts the
  detector by 4.6 x 1 / 5.7 = 0.8 V, so M668 stays on 0.2-0.6 s after the ports are off and C661 fills. First
  attempt without the kick (scratch/p5_shed_tb.py): hold only 1.6-3.9 V, retries every 0.5-0.75 s: the kick is needed.
- Clamps: M662 (PD_STAT) and M667 on SHED_HW (detector); M667 gate moved HOST30 -> HOST15 (finding 2).
Off time = detector decay to the M668 Vth + hold decay 4.55 V -> M663-M666 Vth: 2.2 x ln(4.55 / 2.5) = 1.3 s ..
2.2 x ln(4.55 / 1.0) = 3.3 s, plus the detector part.

Evidence, dedicated testbench of the shed stage alone (scratch/p5_shed_tb.py, ngspice direct; real parts of the stage,
AO3401A_FIT, 2N7002 LEVEL 1 with VTO swept; eFuse replaced by sources: EFUSE_OUT 3.99 V while ports on + overload
(corner_max_def_hw point), 4.63 V with ports off; PGOOD model as the board testbench (PGTH < 0.95 V); ports enabled at
41 ms; 8 s):

| M668 Vth / M663-666 Vth | first shed | on time (retry) | off time | SHED_HOLD max | SHED_HW max |
|---|---|---|---|---|---|
| 1.5 / 1.5 V (model) | 221 ms | 0.18 s | 2.84, 2.74 s | 4.554 V | 2.33 V |
| 1.0 / 2.5 V | 160 ms | 0.10 s | 1.87, 1.61 s (steady) | 4.554 V | 1.89 V |
| 2.5 / 1.0 V | 463 ms | 0.35 s | 3.66 s | 4.554 V | 3.29 V |
| 1.0 / 1.0 V | 148 ms | 0.03 s | 3.99 s | 4.554 V | 1.83 V |
| 2.5 / 2.5 V | 475 ms | 0.40 s | 1.53, 1.43 s | 4.554 V | 3.29 V |
- power-up inrush (PGOOD low 30 ms only, Vth 1.0 V): SHED_HW max 0.336 V, SHED_HOLD 0.014 V: no shed.
- leakage 5 uA on both M661 and M669, PGOOD high, Vth 1.0 V: SHED_HW 0.740 V, SHED_HOLD 0.377 V: no shed (0.26 V
  under the min Vth; leakage + min-trip inrush + min Vth + 55 C together is a stress case, see finding 3).
Board testbench corner_max_def_hw (pass 5 `hwflow sim`, no abort now, 221 s): 500-520 ms ihost_max_def_hw 0.480 A
(<= 0.90 A: ports shed, internal loads only), shed_hw_max_def = min SHED_HOLD 4.632 V (>= 2.5 V), 5V_SYS 4.641 V
(recovered from the 3.9 V brown-out). The window ends inside the first hold; the retry cycle past 2.5 s is from the
dedicated testbench above (the whole board for 2.5 s+ is not practical: 0.5 s took 221 s with the shed acting and
aborted earlier without it).

### Finding 2 (should): PGTH window, 0.1 % divider
PGTH falling 0.90 / 0.92 / 0.94 V (TPS25940A p.6, as quoted by the review; the rising row was NOT re-read in this
pass: no PDF access in this session, assumed 0.97 / 0.99 / 1.01 V). Divider k = 1 + R676 / R677.
- falling min >= 4.0 V: 0.90 k_min >= 4.0 -> k_min >= 4.444; falling max <= 4.35 V: 0.94 k_max <= 4.35 -> k_max <=
  4.628; rising max under the legitimate 900 mA-host EFUSE_OUT 4.75 - 0.902 x (0.15 + 0.064 Ron hot) = 4.557 V ->
  k_max <= 4.557 / 1.01 = 4.512.
- Chosen R676 100 k 0.1 %, R677 28.7 k 0.1 % (was 29.4 k 1 %): k = 4.4843; ratio error +-0.2 % on k - 1 -> k 4.4773 ..
  4.4913. PGOOD falls at 0.90 x 4.4773 = 4.030 V min, 4.126 typ, 0.94 x 4.4913 = 4.222 V max (window met; overload point
  3.99 V is under the minimum). Rising 4.343 / 4.440 / 4.536 V (assumed row): the 900 mA-host floor (4.557 V hot, 4.577
  V at 42 mohm) and the recovered point 4.63 V clear it.
- The legitimate 1.5 A-host EFUSE_OUT 4.45 V (1.55 A unshed) cannot clear a rising threshold while falling stays >=
  4.0 V: needs rising_max / falling_min <= 4.45 / 4.0 = 1.1125, the part gives 1.01 / 0.90 = 1.122. So M667 now clamps
  on HOST15 (high on 1.5 A and 3 A hosts; the 3 A host point 4.21 V is under the window anyway). HOST15 is pulled up
  from 3V3_D: in a real brown-out 3V3_D collapses and the clamp releases [not simulated].
- Testbench PGOOD model unchanged (single 0.95 V threshold -> 4.26 V at k = 4.4843).

### Finding 3 (should): leakage
AO3401A IDSS max 1 uA at VDS -30 V, 25 C; 5 uA at TJ 55 C (AOS AO3401A datasheet electrical table, value as quoted by
the review and as I know the AOS table; not re-read in this pass, no PDF access). Pass 4 10 M: 10 V -> clamps SHED_HW at
VBUS (latched shed). Now: detector 5 uA x 150 k = 0.75 V, hold 5 uA x 100 k = 0.5 V, both under the 2N7002 1.0 V min
Vth; the hold time comes from the larger C661 22 uF instead of the resistor. The IDSS limit is the -30 V test
condition; at 4.6 V the real leakage is lower.

### Finding 4 (should): R655 / R656 host-level trips (numbers only, no change)
- 1.5 A host: now R_eq 86.6 k // 133 k = 52.45 k: R_eq tolerance 0.45 % (R654 0.1 %, R655 1 %): min 0.91 x 87.5 / (52.45 x 1.0045) =
  1.511 A, typ 1.668 A, max 1.085 x 87.5 / (52.45 x 0.9955) = 1.818 A > 1.5 A advertised. Proposed R655 249 k (R_eq 64.25 k, tolerance 0.33 %):
  min 1.235 A, typ 1.362 A, max 1.483 A (window met). NOT applied: the legitimate R21 load does not clear the min:
  bus_1a5 (four ports at their limits, internal typical) 1.553 A, and R21's own promise 4 x 225 mA + 600 mA = 1.5 A
  are above 1.235 A; only the shed state (bus_1a5_shed 1.016 A, corner_max_1a5 1.082 A) clears it. With 249 k a
  min-trip unit would limit (and the hardware shed would cycle the ports) at the R21 load. Product question: protect
  a 1.5 A host to <= 1.5 A (R21 per-port 225 mA only with two ports shed) or keep the 1.82 A max (R21 met).
- 3 A host: R_eq 26.19 k, tolerance 0.73 %: min 0.91 x 87.5 / (26.19 x 1.0073) = 3.018 A, typ 3.341 A, max
  1.085 x 87.5 / (26.19 x 0.9927) = 3.652 A; spread x 1.21;
  legitimate 2.761 A (bus_3a) x 1.22 -> the max can be no lower than ~3.36 A > 3 A. No fix with this part (product
  question, as the review says).

### Finding 5 (should): firmware IMON rule: secondary, not a protection
ADC reference = ADC_AVDD = 3V3_D: TPS62933 Vref 0.8 V +-1 % (tps62933.md p.1) with 31.6 k / 10.0 k 1 % (ratio +-1.5 %)
-> +-2.5 %; reading error +-2.6 %. RP2040 ADC gain / offset error: no guaranteed figure in notes/datasheets/rp2040.md
(the RP2040 datasheet gives ENOB / INL / DNL; not re-read in this pass). Even with an ideal ADC core:
- worst legitimate reading (high-gain IMON, R657 +1 %, offset +15 mA, reference -2.5 %): 0.536 x 1.026 = 0.550 V;
- lowest reading at the eFuse minimum trip (IMON 47.8 uA/A, R657 -1 %, reference +2.5 %): 0.9185 x 0.478 x 0.99 /
  1.025 = 0.424 V (offset -15 mA makes it lower).
No calibration-free threshold fits (0.550 V > 0.424 V; it did not fit before the ADC error either: 0.536 > 0.435).
Outcome: the firmware rule stays as a secondary load-sharing aid only (threshold 0.550 V + the RP2040 offset once read,
> 10 ms, default host only), never needed for R19; the hardware shed is the protection (PM round 7: R19 must not depend
on calibration). imon_fw_floor / imon_fw_heavy keep their 0.4820 V limit (nominal-unit equivalent of the worst
legitimate reading). Consequence to report: on a nominal unit the firmware rule acts only above ~1.05 A, i.e. never
before the eFuse (typ 1.010 A); with four over-asking devices on a 900 mA host the host sees bus_limits 0.938 A
unshed (4 % over the 900 mA nominal, inside the 1.13 A ceiling); bus_limits_shed / corner_max_def / bus_1a5_shed
(PWR_SHED forced) show what a shed gives, not what the firmware will do.

### Nits (8)
- design.yaml stale shed comment rewritten (block above M661); bus_floor realism now cites 0.4820 V; corner_max_def_hw
  realism and measure (SHED_HOLD) updated to the new timing; notes "Firmware IMON rule" 0.4772 -> 0.4820 V (with the
  reason); S9 discharge time corrected to 2 k x 13 uF x ln(9 / 0.8) = 63 ms.
- bus_floor_mintrip timed out at the default 600 s with the new stage (it passed in round 7): timeout_s 1500 set.

### Results (pass 5)
- `hwflow sim` on design.yaml (823 s): 261/271; all R19 / R21 power and bus measures pass except bus_floor_mintrip,
  which hit the default 600 s timeout. With timeout_s 1500 it passes alone (scratch/p5_one, 648 s): vc1 4.531 V, va1
  4.537 V, host 0.9019 A (<= 0.9185 A), PGOOD 4.614 V, SHED_HW 0.387 V (<= 0.5 V). The full run of the final
  design.yaml (scratch/mc_bus copy, nominal pass): 266/271.
- corner_max_def_hw: no abort now (221 s): host 0.480 A, SHED_HOLD 4.632 V, 5V_SYS 4.641 V.
- bus_floor SHED_HW <= 3.8e-7 V, bus_heavy <= 0.0385 V (was 0.054 V); efuse_pg 4.61 V; imon_fw 0.4717 / 0.4767 V.
- Numerical (solver) failures, not design (PM note): mic_noise_min @ 25 C (dead-board op, 7507 uV), mic_noise_max @ 0 C
  (aborted) and @ 25 C (dead-board op, gain -99.5 dB); errored without counted measures: hp_noise_32 0 / 25 C,
  hp_total_32 0 / 25 / 40 C, hp_noise_300 0 C, hp_total_300 0 / 40 C, r22_coupled 40 C, mic_noise_min 0 C, hp_thump
  25 C. New versus round 7: mic_noise_min / max now also land on the dead-board point at 25 C (the added shed nodes
  change the op search path; the audio circuit is unchanged).
- Monte Carlo (scratch/mc_bus, fresh copy, MC on bus_floor / bus_heavy / bus_1a5 only, 50 runs): bus_floor 100 %
  (ihost 0.9015-0.9021 A, SHED_HW <= 2.4e-7 V, imon_fw 0.4675-0.4755 V), bus_heavy 100 % (ihost 0.9108-0.9116 A,
  SHED_HW 0.036-0.041 V, imon_fw 0.4723-0.4805 V), bus_1a5 100 % (ports 0.262-0.269 A).

### BOM delta (pass 5)
+ M668 2N7002 $0.0155, M669 AO3401A $0.02, R679 / R826 / R827 / R828 4 x $0.002, C661 22 uF $0.0932, C662 1 uF $0.003;
C660 220 nF -> 4.7 uF 1206 (+$0.028); R676 / R677 1 % -> 0.1 % (+$0.016). Total +$0.18: $105.12 -> $105.30.


## Stage 5 fixes, pass 6 (monostable shed)

Delta review 3 (must): the pass 5 shed retried only when PGOOD went high again. With host VBUS under ~4.44-4.64 V at
the connector, EFUSE_OUT after the shed stays under the PGTH rising threshold (4.343 / 4.440 / 4.536 V), so PGOOD stayed
low and the ports latched off until replug (also at power-up). The level detector / hold / kick (M661, R659, C660,
R658, M668, R679, M669, R826, R827, R828, C661, C662) is removed. It is replaced by an edge-triggered timer.

### Circuit (design.yaml group pwr_ctl, comment above U661)
- U661 74LVC1G123 (SN74LVC1G123DCTR, SM8 / KiCad Package_SO:SSOP-8_2.95x2.8mm_P0.65mm, symbol 74xGxx:74LVC1G123,
  pins from `lib show`). Supply: VBUS_HOST through R685 100 ohm, C663 100 nF (SHED_VCC). This is the host side of the
  eFuse, so it stays up while 5V_SYS / 3V3_D sag. The inputs are 5.5 V tolerant, and Q swings to VBUS_HOST into the 2N7002 gates.
- Trigger on A (Schmitt): PGOOD (R678 now 10 k to VBUS_HOST) -> R686 47 k -> SHED_TRG, C664 2.2 uF (0805 25 V X7R).
  D660 1N4148W bypasses R686 on the way up (recharge 10 k x 2.2 uF = 22 ms), so only PGOOD-low time is filtered.
- B = power-up gate: R687 100 k / C665 4.7 uF (1206 50 V). While B is low nothing can trigger. B rising while A is
  still low (PGOOD low since power-up, e.g. devices already plugged in on a 900 mA host) triggers one pulse.
- CLR: R688 10 k pull-up. M662 (PD_STAT) and M667 (HOST15) pull it low, which clears Q, so there is no shed in PD mode
  or on 1.5 A / 3 A hosts. M662 / M667 moved from SHED_HW to SHED_CLR. When HOST15 falls in a brown-out, CLR rises,
  and with PGOOD low that triggers.
- Timing: R689 120 k (SHED_VCC -> Rext/Cext), C666 22 uF CL31A226KAHNNNE (C12891, ex-C661 part) between Cext and
  Rext/Cext. Q = SHED_Q drives M663-M666 (all four bus-mode ports off). R690 100 k pulls SHED_Q down (Ioff outputs
  float when U661 is unpowered).
- Datasheet facts: notes/datasheets/sn74lvc1g123.md. Function table, K, Schmitt thresholds and leakage are NOT
  re-read (the PDF could not be reached in this session: no WebFetch, and a shell download needs approval). The
  envelopes below are assumptions to confirm before release.

### Timing arithmetic
- Deglitch: tau = 47 k x 2.2 uF = 103 ms. C_eff x0.75..x1.15 (10 % tolerance, DC bias, temperature); A VT- 0.87-2.29 V
  (assumed). Trigger delay = tau ln(V0 / VT-). Minimum 0.75 x 103.4 x 0.99 = 76.8 ms x ln(4.75 / 2.29) = 56 ms at the
  4.75 V host minimum. This is above the 30 ms worst inrush (eFuse minimum trip, round 7) and the brief's >= 40 ms.
  Maximum 1.15 x 103.4 x 1.01 = 120 ms x ln(5.25 / 0.87) = 216 ms. Testbench retry on-times: 78-143 ms.
- Pulse: tw = K x R689 x C666 = 1.0 x 120 k x 18.7 uF (22 uF X5R, -15 % DC bias at ~3 V) = 2.24 s. Testbench values:
  2.25 s typ; 1.52 s (K 0.9, C x0.75, 5.25 V, low thresholds); 2.85 s (K 1.1, C x1.15, 4.45 V, high thresholds).
  With 5 uA Rext/Cext leakage on top (55 C stack), 3.88 s. 5 uA x 120 k = 0.6 V; the RC asymptote stays 3.85 V >
  KREF x VCC 3.04 V, so the pulse always ends (no latch).
- B gate: 100 k x 4.7 uF = 470 ms. B rises at tau ln(V / (V - VT+)), VT+ 1.58-3.4 V: earliest 0.75 x 470 x ln(5.25 /
  3.67) = 126 ms, latest ~1.0 s (testbench: 4.45 V, VT+ 3.4 V, C x1.15, 0.5 V leakage). The trigger node is ready
  (above VT+) <= 37 ms after PGOOD rises (1.48 x 25 ms), well before 126 ms.
- Leakage (5 uA assumed max per LVC input, 10 uA 2N7002 on CLR at 55 C, 1 uA D660 reverse): SHED_TRG high level -0.05
  V (10 k), B -0.5 V (3.95 V at a 4.45 V host > VT+ max 3.4 V), CLR -0.15 V.
- PGTH (should, review 3): leakage +-100 nA x 22.3 k = +-2.2 mV at PGTH = +-10 mV at EFUSE_OUT. Falling becomes
  4.020-4.232 V and rising 4.333-4.546 V. The window conclusions of pass 5 hold. TPS25940A PGOOD abs max 20 V and
  deglitch 0.42-0.66 ms were added to notes/datasheets/tps25940a.md, so the earlier ">= 7 V assumed" is replaced.
- R679 / C662 (should): both removed. The only remaining pull-ups are R688 10 k (CLR) and R678 10 k.

### Testbench (scratch/p6_shed_tb.py + p6_matrix.py; eFuse = sources, PGOOD comparator 0.99 / 0.92 V on the real PGTH divider, ports at 41 ms, 8 s)
| case | result |
|---|---|
| overload, typ | shed at 0.337 s (B gate), off 2.25 / 2.26 / 2.27 s, retry on 0.12 s, re-shed: periodic |
| overload, low VT / C x0.75 / K 0.9 / 5.25 V | first 0.170 s, off 1.52-1.53 s, on 0.13-0.14 s |
| overload, high VT / C x1.15 / K 1.1 / 4.45 V | first 0.743 s, off 2.85-2.86 s, on 0.078 s |
| overload + 55 C leakage, high VT, 4.45 V | first 0.998 s, off 3.88 s, on 0.064 s |
| overload cleared at 3 s (typ; and leakage + low VT + 5.25 V) | one or two cycles, then ports stay on |
| inrush 30 ms at 41 ms + port-enable 30 ms at 4.0 s (typ; high VT-, C x0.75, 4.75 / 5.25 V; + leakage) | no shed, SHED_Q 0.000 V |
| host 4.40 V, PGOOD never rises (normal load or overload) | one 2.25 s pulse when B opens (0.38 s), then ports on to the end: no latch |
| PD_STAT high or HOST15 high, overload | never shed (SHED_Q 0.000 V) |
| 4.45 V, 55 C leakage, normal load | no shed |
Remaining behaviour to know: a host under ~4.44 V where PGOOD never rises gets one 2 s port delay at power-up, then no
further shedding (the eFuse current limit stays the only protection there, as the brief accepts).

### Board simulation (`hwflow sim`, 543 s): 265/271
- All bus_* / corner_* / pd_* measures pass. bus_floor shed_hw_floor (now max SHED_Q) 9.5e-9 V, bus_heavy 9.5e-9 V,
  bus_floor_mintrip 9.5e-9 V (inside its 1500 s timeout), efuse_pg 4.52-4.64 V, ihost_floor 0.9017 A, ihost_heavy
  0.9109 A, imon_fw 0.4716 / 0.4764 V.
- corner_max_def_hw: host 0.480 A (<= 0.90 A), shed_hw_max_def = min SHED_Q 4.673 V (>= 2.5 V), 5V_SYS 4.641 V.
  No abort.
- The 6 failing measures are numerical (dead-board operating point, PM note), the same set as pass 5:
  mic_noise_min @ 0 / 25 C (mic_in_noise_min 1.1e4 / 7508 uV), mic_noise_max @ 0 / 25 C (gain -96 / -99.5 dB, EIN).
  Errored without a counted measure: hp_noise_32 0 / 25 C, hp_total_32 0 / 25 / 40 C, hp_noise_300 0 C, hp_total_300
  0 / 40 C, r22_coupled 40 C.
- Model: MONO123_BEH in lib/spice/power_fit.lib (behavioral). Edge detection uses an arm / discharge latch, not a
  differentiator; the first differentiator version missed triggers when ngspice took large steps.

### Monte Carlo (fresh scratch/mc_bus, MC only on bus_floor / bus_heavy, the analyses whose measures changed; 50 runs)
- bus_floor 100 %: ihost 0.9015-0.902 A, SHED_Q <= 9.51e-9 V, efuse_pg 4.634-4.638 V, imon_fw 0.4673-0.4759 V.
- bus_heavy 100 %: ihost 0.9108-0.9115 A, SHED_Q <= 9.51e-9 V, imon_fw 0.4722-0.4808 V.
- Nominal pass of the copy: 265/271, with the same 6 numerical mic_noise failures.
- Not run under MC: bus_floor_mintrip (~650 s per run) and corner_max_def_hw (a single fault corner). The shed timing
  spread is covered by the testbench corners above.

### BOM delta (pass 6)
- Removed: M661 $0.02, R659 / R658 / R679 / R826 / R827 / R828 6 x $0.002, C660 $0.03, M668 $0.0155, M669 $0.02,
  C662 $0.003, C661 $0.0932 (-$0.196).
- Added: U661 ~$0.20 (LCSC number and price NOT verified), R685-R690 6 x $0.002, C663 $0.003, C664 $0.01, C665
  $0.03, C666 $0.0932, D660 $0.012 (+$0.360).
- Net +$0.16: $105.30 -> ~$105.46. R678 100 k -> 10 k (same price). R655 is unchanged (product decision pending).


### Delta review 4 (pass 6): no must. Applied: R686 47 k -> 100 k (trigger 62-312 ms with the datasheet VIL/VIH bounds; eFuse current-limit dwell up to ~0.3 s + the B-gate delay, at 0.7-1 W, inside the thermal check), U661 LCSC C123302, notes/datasheets/sn74lvc1g123.md rewritten from the datasheet.
Open (should, not re-simulated, PM rule: no more rounds on this): shed testbench re-run with VT- 0.3-0.7 VCC and R686 100 k; corner_max_def_hw window 500-520 ms proves the typical corner only (B opens 0.13-1.0 s; slow corner ~0.68 s): the host can sit at the eFuse trip for up to ~1 s before the first shed. LCSC numbers for C664/C665 missing. First-article bench: overload shed timing (trigger delay, ~2 s off, retry) on a 900 mA host.
