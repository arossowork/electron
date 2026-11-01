# Circuit (stage 4, 2026-11-01)

Captured in design.yaml: 83 parts, groups power / modem / lte_rf / sim / gnss / ui / sensor / debug. Project library additions: dog_misc:TPS612994_YBH (balls A1 VIN, B1 SW, C1 EN, C2 VSEL, B2 VOUT, A2 GND; SLVSGS9G Table 5-1, the hw:TPS61299 symbol has DRL numbers 1-6 that do not match the DSBGA pads), dog_misc:TPS3838K33_DBV (1 CT, 2 GND, 3 MR, 4 RESET open drain, 5 VDD; SLVS292F p.3, read with pdftotext), back-face footprints dog_misc:TestPoint_Pad_1.5x1.5mm_back and PinHeader_1x02_P2.54mm_Vertical_back (KiCad originals with F.* layers moved to B.*). Models: models/dog_power.lib (TPS612994_FIT, GZ215F_FIT), models/dog_fets.lib (SI2301CDS / CJ2305 / BSS138 at their max |VGS(th)|).

## Power path
- J1 (BT+/BT- wires, back) -> D1 TPD1E10B06 + C1 1 uF at the pad -> M1 SI2301CDS (D cell+, S BAT_P, G GND: reversed cells leave the body diode off, VGS = 0) -> C2 22 uF 25 V, L1 1 uH DFE201610E, U2 TPS612994 (EN = VIN, always on; R1 22.6 k 1 % = 4.0 V, Table 7-1 p.14) -> VDD_4V0: C3 22 uF 10 V 0805 at VOUT (effective >= 4 uF needed, SLVSGS9G 8.2.2.3: 22 uF X5R at 4 V keeps ~8-10 uF), R2 0 ohm 0805 (R_S option, 0.47 ohm fit if the bench shows loop trouble with the supercaps outside the 4-1000 uF COUT range) -> VSTORE: C4, C5 GZ215F in parallel on the back, BAL pins tied (VSTORE_BAL), R3/R4 4.7 M balancing (0.43 uA).
- U3 TPS7A0230 always on (EN = IN) -> VDD_AON 3.0 V: nRF9151 VDD_GPIO + ENABLE, LIS2DW12, TPL5010, pull-ups. U4 TPS7A0230 with EN = GNSS_EN (R5 1 M pull-down: off at reset) -> VDD_GNSS for LC76G VCC and V_BCKP (backup off in sleep, PM round 7 q2). LDO caps: 1 uF in + 1 uF out each; VDD_GNSS total 1 + 10 + 0.1 + 0.1 + 33 pF = 11.2 uF (TPS7A02 range 1-22 uF; inside).
- Pin compatibility: TPS7A0230 DBV is drawn with Regulator_Linear:TPS7A0530PDBV (IN 1, GND 2, EN 3, NC 4, OUT 5); the TPS7A02 DBV pin table is the same (TI lists TPS7A02 as pin compatible with TPS7A05 in DBV). Read it once on the TPS7A02 PDF before fab (not re-read in this pass).
- U5 TPS3838K33 on VDD_4V0, MR and CT to VDD (MR unused per p.3, CT high = 200 ms delay), open-drain RESET -> RESET_N with R6 100 k to VDD_AON (no path into the 3.0 V domain from 4.0 V). VIT 2.84/2.93/2.99 V (SLVS292F 7.5): trip max 2.99 V is below the 3.2 V hold-up floor. Supply current 250/450 nA typ/max above 3 V (p.6): PWR-5 used 0.25 uA, max 0.45 (+0.2 uA x 1.465 = +0.3 uA battery side, inside the 22 uA floor). U6 TPL5010 on VDD_AON, RST (open drain) wired-OR on RESET_N, DONE pulled down 1 M (R20), DELAY R7 22 k for ~60 s: the TPL5010 resistance table was NOT read in this pass (open, set from the datasheet table before fab).
- VBAT divider (block pfet_vbat_divider bound: M2 CJ2305, R8/R9 200 k 0.1 %, C12 10 nF) + M3 BSS138 gate shifter (R10 1 M to BAT_P, R11 1 M pull-down on VBAT_SENSE_EN: divider off at reset). Block value warnings (20k/10k/1n in the block) are intended: values from parts-power.md (PWR-5 needs the 200 k pair, 10 nF for the SAADC sample).

## Modem, RF, SIM, GNSS, UI
- U1 nRF9151: VDD 14 = VDD_4V0 (C13 4.7 uF + C14 100 nF at the pin), DEC0 24 C15 4.7 uF only, VDD_GPIO 65 = VDD_AON (C16 100 nF), ENABLE 10 tied to VDD_AON (low whenever VDD_GPIO is low, DevZone 126378), nRESET 9 = RESET_N, SWD 3/4 to test pads. GPIO map: P0.01 UART_TX, P0.02 UART_RX, P0.03 GNSS_RX (to LC76G RXD), P0.04 GNSS_TX, P0.05 GNSS_EN, P0.06 GNSS_RST_N, P0.07 ACCEL_SDA, P0.08 ACCEL_SCL, P0.09 ACCEL_INT, P0.10 BTN, P0.11 WDT_WAKE, P0.12 WDT_DONE, P0.13/AIN0 VBAT_SENSE, P0.26 VBAT_SENSE_EN, P0.27 LED, P0.28 BUZ_P, P0.29 BUZ_N. All nRF9151 GPIOs wake from System OFF / sleep through the GPIO SENSE mechanism, so BTN, ACCEL_INT and WDT_WAKE are on wake-capable pins. No strap pins on the nRF9151 (no boot-mode GPIO). 54 pins nc (AUX, GPS, COEX, MAGPIO, RFFE, unused GPIO, SIM_DET, NC).
- LTE: ANT 35 -> pi pad set at the pin (C17 DNP shunt, R12 0 ohm series, C18 DNP shunt) -> Ignion 60 mm column match, read from AN Fig 6: Z7 L2 2.2 nH series, Z6 C19 2.5 pF series, Z5 L3 9.1 nH shunt + Z4 C20 1.1 pF shunt (same node), Z3 C21 1.2 pF series, Z2 L4 13 nH shunt, Z1 L5 4.7 nH series -> AE1 NN02-220 pad 1; pads 2-4 not grounded (UM_NN02-220 Fig 4). RF nets 50 ohm on F.Cu.
- eSIM U7 on SIM_1V8 / RST / CLK / IO (C22 100 nF). No ESD array: the MFF2 is soldered and has no exposed contact (the reference "ESD as in reference" refers to a SIM holder). SIM_RST has no pull resistor: the modem drives it and the eUICC is unpowered while SIM_1V8 is off (reset-state answered).
- GNSS: AE2 patch -> FL1 SAFFB1G56 -> U8 LC76G RF_IN 11. VCC 10 uF + 100 nF + 33 pF, V_BCKP 100 nF. D_SEL nc (UART), RESET_N driven open-drain only (internal pull-up to 1.8 V: nets.GNSS_RST_N pullup internal). No LNA option: no SKY65723-81 symbol/footprint in the libraries (the parts stage named the class only); the passive patch path stays as chosen (risk in parts-rf.md).
- Back-power rule (interface.yaml GNSS_RX / GNSS_RST_N): while GNSS_EN is low, nRF P0.03 (UART TX) and P0.06 are driven low or Hi-Z.
- Buzzer BZ1: BUZ_P -> R13 150 ohm -> C27 470 nF -> BZ1+, BUZ_N -> R14 150 ohm -> C28 470 nF -> BZ1-, R15 10 k across BZ1. A stuck pin is DC-blocked (F3). Node swing 2 x 3.06 V peak: nets BUZ_A/B v_max 6.2 V < 7.2 V rec max.
- LED D2 + R16 330 ohm from P0.27: (3.0 - 2.0) / 330 = 3 mA. Button SW1 to GND, R17 1 M pull-up to VDD_AON (3 uA only while pressed), D3 ESD9B5.0ST5G.
- LIS2DW12 U9 in I2C mode (CS to VDD_AON, SA0 GND = 0x18, RES pin 7 to GND), INT1 = ACCEL_INT, R18/R19 10 k I2C pull-ups to VDD_AON (bus idle high: no sleep current).
- Test pads TP1-7 (back, fixed at the old item row): VDD_4V0, SWDIO, SWCLK, RESET_N, GND, UART_TX, UART_RX.

## Simulation (build --until sim, 10/10 measures pass, no Monte Carlo)
Testbench: battery Thevenin source on J1.1 with alter per analysis; boost input limit and supercap ESR set by global control nodes (ilim_ctl 0.08 / 0.12 A, esr_ctl 1.7 = -20 C from Fig 7); supercap C at the 72 mF/module minimum, ESR 132 mOhm max; modem load 5 mA + 0.5 A pulse. Burst runs start charged (`uic` + .ic 3.98 V): the input-limited boost has a second, start-up DC equilibrium that the OP finds otherwise.

| measure | req | value | limit |
|---|---|---|---|
| pwr4_vdd_min_i80 (Li-FeS2 3.0 V / 1.5 ohm, 2 ms 0.5 A, ILIM 80 mA) | PWR-4 | 3.925 V | >= 3.0 |
| pwr4_vdd_min_i120 | PWR-4 | 3.929 V | >= 3.0 |
| pwr7_vdd_min_i80 (alkaline 2.8 V / 4.0 ohm, 100 ms 0.5 A) | PWR-7 | 3.614 V | >= 3.2 (and > 2.99 V trip max) |
| pwr7_vdd_min_i120 | PWR-7 | 3.630 V | >= 3.2 |
| vbat_div_err_mv_1v8 / _3v6 | PWR-6 | -0.03 / -0.33 mV | +-50 |
| pwr1 VDD_4V0 at 1.8 / 3.6 V in | PWR-1 | 3.981 / 3.985 V | 3.88-4.12 |
| pwr1 VDD_AON at 1.8 / 3.6 V in | PWR-1 | 3.001 / 3.001 V | 2.94-3.06 |
Debug: BAT_P min 2.81 V (Li-FeS2, 120 mA) and 2.30 V (alkaline, 120 mA) under the burst. Hand check PWR-7: 0.458 A deficit x 0.1 s / 0.144 F = 0.318 V + 0.458 x 0.112 ESR = 0.37 V droop from 3.98 V = 3.61 V (sim 3.614 V).

PWR-6 total error (analysis): divider ratio with two 0.1 % resistors = +-0.2 % -> +-7.3 mV at 3.65 V battery (x2 of the sense error); divider loading by the SAADC input and M2 drop at 9 uA (sim) 0.3 mV; nRF9151 SAADC gain/offset error not read from the PS (open item): 50 - 7.6 = 42 mV remains for the ADC + reference at 3.65 V (1.2 % of full scale). Firmware samples between TX bursts (F7).
Start-up charge (informative, not simulated: the charged-start .ic is global): parts-power.md F2 arithmetic, ~12 s from empty supercaps on cold alkaline (0.144 F min x 4.0 V; 20 mA / 80 mA / 42-66 mA phases).

## Budgets
- vdd_burst_copper (PWR-7): 3.614 V worst - 3.2 V = 0.414 V; 0.400 V to the supercap -> SiP VDD copper at 0.5 A = 800 mOhm.
- bat_path_copper (PWR-7): input drop sensitivity 0.0177 V of VDD droop per V at the boost input (0.12 x 0.85 / 4.0 x 0.1 s / 0.144 F); the remaining 0.014 V -> 0.79 V -> /0.12 A = 6.6 ohm, set 6000 mOhm.
- d1_tvs_lead, d3_tvs_lead: copper_len 5 mm (tvs-budget).

## Checks answered
- abs-max / cap-voltage: 0 errors. GPIO nets carry v_max 3.06 V (VDD_GPIO domain) and SIM nets 1.9 V; the default bound from the SiP's highest supply (VDD 4.0 V) does not apply to its I/O.
- escape (SiP pads 10/14/65): VDD_AON is a 0.15 mm signal-class net (< 5 mA loads); VDD_4V0 keeps its power class and necks at pad 14 only (0.5 A x ~1 mm x 0.15 mm = ~2 mV).
- floating-node (9 islands): all are nets of unsimulated parts (SiP, LC76G, buzzer, LED, I2C, RF match); the 1 Gohm leaks only define their levels.
- reset-state: GNSS_EN (R5), VBAT_SENSE_EN (R11), WDT_DONE (R20) pulled down; RESET_N pulled up (R6); BTN pulled up (R17); SIM_RST answered above.
- isolation: n/a (no mains, no isolation group; board.isolation not used).
- block warnings: vbat_div value differences intended (above). block match: no further library block fits.

## Gates
- hwflow check: 0 errors, 4 warnings (answered above). ERC: 0 errors, 1 warning.
- floorplan: PASS with risks (routability prediction: hot spot x 34-38.5 / y 18.5-22.5 around the divider and buzzer drive, 5.8x; 25 single-exit pads; GNSS_RF_IN and LTE_RF corridors tight at U8.11/FL1.4 and C17). Components on the back face need `side: back` (supercaps, test pads, J1).
- hwflow fw: 1 error, tool gap: `src/hwflow/fw` FAMILIES has esp32c3 and rp2040 only; no nRF91 table (GPIO from pin name `P0.(\d+)`, AIN on P0.13-P0.20, wake on any GPIO via SENSE, no strapping pins). Needs an hwflow change (not a design edit).

## Open
1. TPL5010 DELAY resistor for 60 s (22 k placeholder) from the TPL5010 table.
2. nRF9151 SAADC accuracy (PWR-6 remainder 42 mV) and abs-max numbers (5.8 V used, unread).
3. TPS7A02 DBV pinout re-read; SI2301CDS IGSS (Vishay PDF) still unverified.
4. fw gate: nRF91 family table in hwflow.
5. Boost model is an averaged fit (no ripple, no loop dynamics): loop stability with 144 mF behind 0 ohm is a bench item (R2 0.47 ohm option).

## Stage-5 review batch (2026-11-01)
Done: GNSS_ANT on [B.Cu, F.Cu] (B.Cu from the patch pin, via + F.Cu stub at FL1.1, FL1 is SMD) with patch keep-outs (F.Cu r 3.0 mm tracks/vias/pour; B.Cu r 3.5 mm vias/pour, tracks allowed so the own feed can leave: KiCad keep-outs are not per-net); TPD4E05U06DQAR x2 (U10, U11) on the back at the test pads; board.ref_plane_h_mm 0.1 (JLC04081H-3313) and 0.18 mm 50 ohm microstrip width on the RF nets; copper_len budgets gnss_ant_feed 12, gnss_saw_to_rx 2, lte_pin_to_pi 2, lte_match_chain 12 (4 legs), lte_match_to_feed 14; GZ215F keep-out B.Cu only, "+"/"BAL" silk, zone_connect 1 on GZ215F pad 1 and J1 pad 2; MFF2 pads x 1.9..2.85 + 3.4 x 4.2 F.Cu keep-out, 1NCE MPN (pinout first-article); R21/R22 1 k at U1 on GNSS_RX / GNSS_RST_N; interface.yaml attach_min_vbat_mv 3900, bor_threshold_mv 2990, boost_uvlo_mv 500, F4 text; C17/C18 50 V; screw_tr/br text (0 mm to the LTE clearance, not moved); buzzer 6.0 Vpp H0H1 note in parts-ui.md.
Not done: FL1 rotation/region and the divider/buzzer spreading region (U8 and U1 have no fixed position, so a region near them cannot be drawn without fixing them).
Floorplan: FAIL, quick placement illegal on copper_len budgets only (fill 69 % front, the tool reports "a legal placement should exist: placer failure, another seed may find it"): the quick placer does not pull budget pairs together (FL1.4-U8.11 30.5 mm, U1.35-R12.1 5.8 mm, L2-C19 8.9 mm, AE2.1-FL1.1 12.1 mm, even the stage-4 D1-J1 5 mm budget now 4.78 > 4.0 mm). No routability prediction from an illegal placement. Remedy: fix U1/U8 positions in the floorplan (sip/gnss_rx `at`) with FL1/R12/match parts placed at their pins, or a block for each RF chain; or an hwflow change to make the quick placer honour copper_len budgets.
