# Conflicts (stage 1)

## Assumptions (typical public numbers for nRF9160/nRF9151, SARA-R5, BG77 class; no web search used, verify in stage 2)

- Usable charge: 2x AA Li-FeS2 = 3000 mAh x 0.80 (cold -20 C, pulse load, cut-off) x 0.98 (self-discharge 1 yr) = **2350 mAh**.
- Limits: 1 year = 8760 h -> <= 0.268 mA; 2 months = 1440 h -> <= 1.63 mA.
- LED + buzzer: <= 1 uA each on average (blink 1 ms/10 s at 5 mA = 0.5 uA; buzzer 30 mA x 10 s x 2/month = 0.2 uA).
- Modem runs at 3.3-3.7 V, battery is 3.0 V nominal and sags to about 2 V -> boost converter. Battery-side factor = 3.3 V / 3.0 V / 0.85 = **1.3** on all modem-side currents.
- Sleep floor (modem PSM 3 uA + MCU + boost Iq + accel): 5 uA modem side.
- eDRX paging cost: about 1.5 mC per eDRX cycle (PTW 1.28 s with a few paging occasions + resync). Paging current = 1.5 mC / cycle.
- TAU: 40 mC each. At 12 h -> 1 uA; at the common operator default 54 min -> 12 uA.
- GNSS: 40 mA while tracking. Cold fix no assistance 60 s = 2.4 C. A-GNSS: download 0.3 C + 30 s fix (collar, partial sky) 1.2 C = 1.5 C/request. On nRF91 GNSS and LTE time-share (no simultaneous use).
- Upload position + RRC tail (with RAI): 0.2-0.3 C.
- Requests: 2/day x 1.5 C = 3 C/day = **35 uA**. Lost mode: 1 fix/min for 2 h, once a month: 120 x (hot fix 0.2 C + upload 0.3 C) = 60 C/month = 2 C/day = **23 uA**.

## C1. "On request" vs battery life (resolved, PM round 2, q1-q3)

Decision: eDRX 81.92 s (latency about 2 min), 60-min poll fallback when the network refuses eDRX, 2x AA Li-FeS2, lost mode 1 fix/min for up to 2 h, owner-started (budgeted once a month).

On request means the network must reach a sleeping modem: in plain PSM it is unreachable, so it listens in eDRX paging windows, or it polls the server.

| mode | paging/poll uA | modem side uA (5 floor + pg/poll + TAU + 35 req + 23 lost + 1 LED/buzzer) | battery uA (x1.3) | life 2x AA (2350 mAh) | latency |
|---|---|---|---|---|---|
| **eDRX 81.92 s, TAU 12 h (chosen)** | 18 | 5+18+1+35+23+1 = 83 | 108 | 21800 h = 30 mo | 82 + 30 + 10 = 122 s |
| eDRX 81.92 s, TAU 54 min (operator default) | 18 | 5+18+12+35+23+1 = 94 | 122 | 19300 h = 26 mo | 122 s |
| **eDRX refused -> 60-min poll (chosen fallback)** | 56 | 5+56+35+23+1 = 120 | 156 | 15100 h = 21 mo | <= 60 min |
| eDRX refused, no fallback (DRX 1.28 s, ~600 uA) | 600 | 664 | 863 | 2720 h = 3.7 mo | ~45 s |

Limits: PWR-2 (2 months) <= 1.63 mA, PWR-3 (1 year) <= 0.268 mA. The chosen mode meets both (108 uA); the fallback meets both (156 uA, 21 months). The firmware must detect a refused eDRX grant and switch to the poll, otherwise life drops to 3.7 months (still meets PWR-2, misses PWR-3).

GNSS per request: a cold start without assistance is 2.4 C instead of 1.5 C (+27 uA battery side, 30 -> 25 months); A-GNSS stays required (IF-3).

## C2. Battery mass and voltage vs 60 g and modem peaks (battery resolved PM round 2, q2; brown-out open for stage 2)

- Mass: 2x AA Li-FeS2 29 g + buzzer 2 g -> 29 g left for the IP67 enclosure with gasketed door (~15-20 g), PCB + modem + antennas (~8-10 g). Tight; MECH-1 is checked as a mass sum.
- Voltage: modem minimum is about 3.0 V (nRF91 3.0-5.5 V, SARA-R5 3.0-4.5 V; BG77 about 2.2 V, to verify). 2x Li-FeS2 = 3.0-3.6 V open circuit, about 2.4-2.8 V under load -> boost converter with input down to 1.8 V (PWR-1).
- TX peak (open, architect): 0.5 A at 3.7 V modem side -> battery side 0.5 x 3.7 / (2.2 x 0.85) = ~1.0 A at sag. Internal resistance 2x Li-FeS2 ~0.4 ohm at 25 C (0.4 V drop), ~1.5 ohm at -20 C (1.5 V drop, below the 1.8 V boost input). A battery-side reservoir carrying a 2 ms burst with 0.5 V droop needs C = 1 A x 2 ms / 0.5 V = 4000 uF.
  Options for stage 2: (1) 4000 uF low-ESR bulk (polymer or small EDLC) on the battery side, about 1 USD, some mm of height; (2) reservoir on the boost output with a current-limited boost (input current capped at ~0.3 A), smaller cap at a higher voltage; (3) limit TX power class, costs link margin. PWR-4 decides.

## C3. Antenna size vs board size (open, for stage 2)

LTE-M low bands (B8/B20, 800-900 MHz) need a ground plane of ~80-100 mm for good efficiency; the lead default board is 70 x 35 mm. A shorter board costs a few dB of TRP/TIS (more TX energy, more retries). Resolve in stage 2 with the antenna datasheet: lengthen the board along the collar, use a flex antenna, or accept the efficiency loss.

## C4. FOTA and A-GNSS assistance download have no line in the budget (open, stage 1b)

C1 carries 0.3 C for the A-GNSS assistance download but IF-2's 122 s latency (81.92 + 30 + 10) does not carry its time. FOTA (IF-8) has no budget line: charge = RX/TX current x download time, for an image of unknown size (open question 3). Rule: a FOTA of C coulombs modem side costs C x 1.3 battery side; PWR-3 headroom is 160 uA = 160 uA x 8760 h = 1402 mAh per year, so size is not a problem at one update per year but not stated yet. Needs the FOTA size and the update rate from the PM.

## C5. Request latency has 0.08 s of slack (open, stage 1b)

IF-2: 81.92 s eDRX + 30 s fix + 10 s upload = 121.92 s against 122 s (2 min 2 s). The SW timeouts (fix 30 s, upload 10 s) are the whole allowance. Network attach, RRC setup and assistance download are not carried separately. No margin added to the product number; PM to say whether 30 s + 10 s are caps or typical values.

# Stage 2 (architecture) updates

- C1 numbers checked against nRF9151 PS v1.0: eDRX 81.92 s = 18 uA Cat-M1 / 32 uA NB (incl. PSM floor 2.7 uA and UICC), supply 3.0-5.5 V (3.2 V practical). Recomputed in architecture.md section 4 with a 3.6 V boost (k = 1.41 active, 1.60 sleep) and an external GNSS (10 mA): eDRX 84.5 uA = 38 months; operator TAU 54 min 100.5 uA = 32 mo; NB-IoT 106.9 uA = 30 mo; 60-min poll 137.1 uA = 24 mo; no fallback ~900 uA = 3.6 mo. PWR-2 and PWR-3 met in every fallback-enabled mode.
- C2 resolved (architect): input-current-limited boost (0.8 A) + 1000 uF polymer reservoir on the 3.6 V rail: deficit 0.16 A x 2 ms / 0.32 V. Long CE-mode bursts at cold: firmware drops to power class 5 (20 dBm, 1.2 W <= 1.22 W available). Battery-side 4000 uF and a 53 mF supercap rejected on mass and cost.
- C3 resolved for the 70 mm board, product question left: Ignion NN02-220 chip antenna, 13 mm clearance, ground 57 mm, ~19 % low-band efficiency (-7.2 dB) against 44.7 % at 100 mm. Question to PM: keep 70 mm (accept ~3.7 dB) or lengthen to 90 mm (+1.5 dB, +20 mm housing, ~+1.5 g). GNSS: 15x15 patch on the front face (up when worn).
- C5 open question to PM (number not changed): IF-2 worst case 121.92 s leaves 0.08 s and does not carry the PTW (up to 2.56 s). Options: (a) acceptance worst case 125 s; (b) keep 122 s, fix timeout 27 s (fewer fixes found under canopy); (c) keep as is (fails by the PTW on a late page).
- Lead decisions: external watchdog TPL5010 yes (35 nA, 0.35 USD; a hung modem in TX empties the cells in 2.4 h). Buzzer: AC-coupled piezo drive + watchdog: hardware cap = watchdog period 60 s (buzzer_hw_cap_s). SIM: MFF2 eUICC soldered, not nano-SIM (no holder, no door; ties the product to a provider that ships MFF2/eUICC).

## C6. nRF9151 GNSS is GPS + QZSS only; IF-3 needs Galileo (new, PM)

nRF9151 PS v1.0: "GPS L1 C/A, QZSS L1 C/A". IF-3 (must) asks GPS + Galileo. Chosen: external Quectel LC76G-PA (GPS/Galileo/GLONASS/BDS, 10 mA): +~5 USD, +0.5 g, +~100 mm2 (incl. LDO), but runs in parallel with LTE (latency) and at 10 mA instead of ~40 mA. Option for the PM: drop Galileo from IF-3 and use the nRF9151 GNSS: -5 USD (BOM 24.5 USD), -0.5 g, fix energy x ~4 (40 mA, +~25 uA battery, life 38 -> ~30 months), GNSS and LTE time-shared (latency +10 s, IF-2 fails).

## C7. Mass 64.8 g against MECH-1 60 g (new, PM)

architecture.md section 10: cells 29.0 + electronics 14.7 + enclosure ~21.1 (76 x 41 x 26 mm, 1.2 mm PC shell + compartment ceiling) = 64.8 g. Options: (a) 1.0 mm walls -2.9 g, 12x12 GNSS patch -1.3 g (~+1 dB worse GNSS), 0.8 mm PCB -0.9 g, 0.5 mm compartment film instead of a 1 mm ceiling -1.5 g: ~58.2 g, IP67 and chew resistance at risk (ENV-2, ENV-4); (b) raise MECH-1 to 65 g; (c) 2x AAA Li-FeS2 (2x 7.6 g, 1200 mAh: -13.8 g, life ~15 months eDRX, ~9.5 months poll; PWR-3 missed in poll mode).

## C8. Cost 29.5 USD against COST-1 30 USD (should), nRF9151 not stocked at LCSC (new, info)

0.5 USD margin (architecture.md section 14); LCSC C22397843 out of stock, Future/Symmetry stock: JLCPCB assembly needs global sourcing or consigned parts (+lead time 16 weeks at DigiKey/Avnet).

## C9. Sleep floor 10.0 uA against PWR-5 10 uA (new, lead default number)

(2.7 PSM + 2.9 rest) x 1.60 + 1.0 boost Iq = 10.0 uA. Options: drop the accelerometer (IF-9 could): 8.4 uA; a boost with >= 80 % efficiency at 5 uA load: 9.4 uA; or PWR-5 to 12 uA (lead default, PWR-3 still met with 3x headroom). Stage 3 decides with the boost datasheet.

# PM round 3 / round 4 (applied in stage 2 rework)

Requirement changes recorded (answers in inbox/answers.md under "# PM round 3" / "# PM round 4"; `hwflow` reads only "## Round N" headings, so they are also recorded here for `hwflow check`; the lead runs `hwflow approve --requirements`):
- change: IF-2 (PM round 3, q3: latency <= 125 s; acceptance 81.92 + 2.56 PTW + 30 + 10 = 124.5 s). C5 closed.
- change: MECH-1 (PM round 3, q2: <= 65 g). C7 closed: 64.2 g with Li-FeS2 (architecture.md 10).
- change: PWR-7 (PM round 3, q5: alkaline AA must work; new must).
- change: COST-1 (PM round 4, q1: <= 50 USD). C8: estimate 32.1 USD.
- PWR-5 re-allocated 10 -> 15 uA (derived limit, notes/sleep-floor-reallocation.md). C9 closed.
- C6 closed (PM round 3, q1: keep LC76G). SIM: soldered MFF2 eSIM (PM round 3, q4). Power loss: reset + re-attach, no backup cell (PM round 3, q6).

## C10. Alkaline cells: mass and cold rating (new, info to PM)

2x E91 alkaline = 46.0 g (23.0 g each, E91 datasheet) against 29.0 g Li-FeS2: the device weighs 64.2 - 29.0 + 46.0 = 81.2 g on alkaline. MECH-1 is written for Li-FeS2 (29 g), so it is met as written; no design reaches 65 g with alkaline cells (cells alone 46 g). E91 is rated -18..55 C, so -20 C (ENV-1) is 2 C outside the cell rating.

## C11. eSIM operator delegated to the lead/parts stage (PM round 7, q3)
Question answered: which European IoT operator and MFF2 chip (LTE-M + eDRX + PSM, Italy/EU)? PM delegated the choice. DELEGATED decision: 1NCE IoT SIM Chip Industrial (MFF2, eUICC), EUR 14.50 per SIM incl. 500 MB / 10 years, outside COST-1. Reason: published price, MFF2 eUICC chip, LTE-M in selected countries, stock via shop (batches of 100). Unverified: the Italy row of the 1NCE coverage list (LTE-M/eDRX/PSM); first-article check; fallback emnify or Onomondo with the same MFF2 land. Logged here as delegated, not a PM answer (notes/datasheets/mff2-esim.md).

## C12. Test pads exposed in the battery compartment (stage-5 review #2, lead decision)
ENV-3 (+-8 kV contact) applies to pads the user can touch at a cell swap. Lead decision (no spec or product change, +~0.2 USD inside COST-1 55 USD): 2x 4-channel low-leakage ESD arrays at TP1-7 instead of a sealed plug/label.

## C13. RESOLVED (stage 9 follow-up): IF-2 device allowance has 0.52 s of slack, and nothing carries the assistance download (stage 9, new)
After the eDRX cycle (81.92 s) and the paging window (2.56 s) the device has 125 - 84.48 = 40.52 s; the 30 s fix cap + 10 s upload cap use 40.0 s, slack 0.52 s. The supercap recharge (2.7 s worst case) is hidden by starting the gate check at the page, so it overlaps the fix; if the rail is still below 3.9 V at the end of the budget the firmware sends no TX and closes the request as failed (host test, PWR-7/IF-2). The assistance download (8.5 s x 50 mA assumed, C4) has no time in 125 s: a request that arrives during a download aborts it (host test). RRC set-up from eDRX after the page is inside the 10 s upload cap: unmeasured (bring-up 19).

## C14. RESOLVED (stage 9 follow-up): LC76G fix margin, EPO expiry and lost-mode fix energy
With V_BCKP off every fix is a cold start. LC76G cold TTFF 28 s (parts-rf.md) against the 30 s cap leaves 2 s in open sky; only EPO (5 s) gives margin. EPO validity (assumed 7 days in the sim, 3-day download period `assist_period_h`) and the EPO injection format are not verified; with an expired file the fix cap decides (F5, last known position with age). Architecture.md 4 charges a lost fix at 10 mA x 5 s (hot fix); the host model uses the same 5 s with valid EPO and gets 50.5 C per session (19.5 uA/month each), equal to the budget line; with EPO expired a fix costs 28 s x 14.1 mA = 0.39 C and a session 120 x 0.39 + uploads = 89 C (34.4 uA/month each: 6 sessions then exceed PWR-3 in poll mode). 

## C15. RESOLVED (stage 9 follow-up): buzzer hardware cap is two watchdog periods, not one
interface.yaml buzzer_hw_cap_s = 60 s is "the watchdog period". A TPL5010 resets after the missed DONE, i.e. at the second WAKE after the hang: up to 120 s (datasheet reset timing not re-read). The 10 s limit that matters is met by the firmware starting the buzzer as ONE finite PWM sequence (the peripheral ends it without the CPU), so the cap holds only with that port rule. If the port cannot (loop count max 65535 periods = 16 s at 4 kHz), the claim 60 s is not true. The lead/PM decide whether to restate buzzer_hw_cap_s; firmware keeps 10 s.

### C13 close-out
IF-2 (125 s) is met by the host tests: eDRX 81.92 + PTW 2.56 + fix 30 + upload 10 = 124.5 s, 0.52 s slack (test_latency, "no fix: answered ... <= 40.52 s"; "slow upload cut at the 10 s cap"). The request-aborts-download path is covered twice: test_latency "request during the download aborts it" (assistance, request at 8 s answered in <= 40.52 s) and test_fota "request during FOTA: chunk aborted" (no chunk between the request and its upload, answered <= 40.52 s). The requirement is met; RRC set-up from eDRX stays inside the 10 s upload cap and is a bring-up measurement (partition.md item 29).

### C14 close-out (EPO expiry; source: Quectel LC26G/LC76G/LC86G AGNSS Application Note V1.1, mm.digikey.com .../6504/LC26G-LC26G-T-LC76G-LC86G_Series_AGNSS_Application_Note_V1.1.pdf)
- Format and validity (AN V1.1 sections 2.1.2-2.1.5, table 2 note 1): GPS-only EPO files EPO_GPS_3_X.DAT from wpepodownload.mediatek.com, 3 days each, built of 6 h sets of 2304 B (72 B x 32 SV) = 27648 B per file; module flash keeps up to 14 days (GPS-only, GPS+GLONASS), 7 days Galileo, 3 days BDS; validity is read from the last segment (GPS_Hour + 6); "download the EPO file 12 hours in advance". Galileo/BDS EPO do not speed up TTFF, only GPS (+GLONASS) does (AN note after table 6).
- Policy now in firmware: `epo_valid_h` 72 (3-day file), `epo_refresh_lead_h` 12, `assist_period_h` 60 = 72 - 12 (was 72 = expiry, and the sim assumed 168 h): refreshed before it expires; test_epo: 10 days, 4-5 downloads, the file is never older than 60 h at the hourly check.
- Lost mode with an expired file: the lost session start asks for a download at once when the stored file does not cover the 2 h and `assist_pays_off`: cold fix 0.39 C (28 s x 14.1 mA) minus hot fix 0.07 C (5 s) = 0.32 C saved per fix against a download of 0.59 C (27648 B at 2353 B/s = 11.75 s x 50 mA; the 0.43 C of the brief was a 20 kB file): break-even at 2 fixes, 120 fixes save 38 C. The first lost fix waits for the download (up to `assist_timeout_s` 60 s), a network position request does not wait.
- Numbers (test_epo, test_energy): lost session, expired file: with download 51.1 C, without 90.6 C (cold fixes). Worst month, 6 expired-file sessions + 1 FOTA/yr: poll mode 222.0 uA, eDRX 169.8 uA, both <= 268 (PWR-3). Without the session-start download poll mode would be 313.4 uA, over PWR-3. The 60 h refresh costs 0.59 C / 2.5 days = 2.7 uA, inside the day model (poll day 103.7 uA, was 100.9).
- Remaining (not a number): (a) the Quectel EPO server needs vendor/project/device_id credentials (AN section 2.1.1, forum "request access for assisted GNSS EPO files"): the PM/lead must obtain them or use a self-hosted mirror; (b) the LC76G is configured GPS+Galileo (IF-3) while only GPS EPO speeds up the TTFF: bring-up 21 measures the TTFF with GPS-only EPO and GPS+Galileo tracking; (c) injection needs the UTC time (PAIR590) and a position (PAIR600) after each power-up: from network time and the last fix, in `board_gnss_inject_assist`.

### C15 close-out (TPL5010, TI SNAS651A, ti.com/lit/ds/symlink/tpl5010.pdf)
- When RSTn fires (8.3, Figure 8): WAKE is sent at the start of each interval tIP; DONE must come >= 20 ms before the next WAKE; if it is missed RSTn is asserted tIP after the missed WAKE (the figure shows tRSTn + tIP after the miss). A hang just after a DONE therefore resets after up to 2 x tIP.
- tIP from the DELAY resistor (8.5.3, Eq. 1/3, coefficient set 3 for 10-100 s): R7 = 22.0 kohm gives T = (0.1972 x 220^2 - 19.345 x 220 + 692.12)/100 = 59.8 s (target 60 s met; the exact value for 60.00 s is 22.03 kohm). At 22.22 kohm (+1 %) 61.3 s; with setting +-0.6 %, oscillator +-0.5 %, life 0.24 %, temperature 400 ppm/C over 60 C: 63.6 s. Worst case 2 x 63.6 = 127.2 s.
- `buzzer_hw_cap_s` restated 60 -> 128 s (interface.yaml, with the arithmetic). The 10 s of IF-7 stays a firmware rule: the buzzer is ONE finite PWM sequence the peripheral ends (REFRESH 99 x LOOP 400 periods = 40000 at 4 kHz; LOOP max 65535 x 100 = 1638 s; `buz_pwm_plan`, test_buzzer_plan, board_hal.h and the port stub). The old "65535 periods = 16 s" limit does not apply with REFRESH. The 128 s only bounds a CPU hang while a sequence is still running, and the sequence itself ends within 10 s.
- Side finding: the DELAY resistor 22 k is now confirmed by the datasheet equation (circuit.md open item 1 closed for the value; 1 % tolerance gives 58.3-61.3 s).

## C16. Low-battery report threshold (stage 9 follow-up) - lead default awaiting PM confirmation
The 14-day warning is a product number. Lead default: LOW_BATT when the idle cell voltage is below `low_batt_idle_mv` 2600 mV (1.3 V per cell) or after `low_batt_sag_count` 3 consecutive TX bursts with loaded voltage below `tx_cell_sag_mv` 2400 mV at T >= 0 C (the cold sag of alkaline is by design, PWR-7).
- Charge needed: 14 d x 24 h x 217.7 uA (worst month, poll mode, host model) = 73.2 mAh (222.0 uA now = 74.6 mAh).
- Li-FeS2 (2350 mAh usable): plateau 1.5 V per cell, the voltage falls through 1.3 V in the last few percent, 5 % = 117 mAh >= 73 mAh. Alkaline (1710 mAh usable): 1.3 V per cell idle at light drain is well before the end (E91 curves not in the repo; E91 low-drain class 2850 mAh), many percent left. The higher level (Li-FeS2 needs the earlier warning in volts; 2600 mV is also above the alkaline 14-day point) is used for both. A flat idle voltage hides the end of Li-FeS2, which is why the sag counter (rising internal resistance, 0.4 ohm fresh, higher when depleted) is the second rule.
- Not verified: no Li-FeS2 or alkaline discharge curve is in notes/ (only e91.md internal-resistance points); the two voltages are lead defaults to measure at bring-up 30. Awaiting PM confirmation of "14 days".

## C4 follow-up (FOTA, IF-8; stage 9)
- Image size `fota_image_kb` 300 kB: lead default from architecture.md section 4 (NCS LTE app); `fota_slot_kb` 440 kB: 1 MB internal flash = MCUboot 48 + 2 x 440 + 80 (settings, NVS, EPO staging) + 16 spare = 1024 kB, no external flash. Scheme: MCUboot swap with a test upgrade (`boot_request_upgrade(BOOT_UPGRADE_TEST)`) and `boot_write_img_confirmed()` after the first attach, else the next reset (watchdog or `fota_confirm_s` 3600 s) reverts it (Zephyr include/zephyr/dfu/mcuboot.h). Modem: delta image through `dfu_target_modem_delta`, stored by the modem in its firmware-patch area, then `dfu_target_schedule_update` and a reboot (NCS doc/nrf/libraries/dfu/dfu_target.rst, "Modem delta upgrades"). Nordic doc pages were not readable here (bot check); the two quotes are from the nrfconnect/sdk-nrf and Zephyr repositories.
- Energy: 300 kB at 100 kbit/s (architecture.md) = 24 s + 19 chunk set-ups of 0.5 s = 1.70 C battery side in the host model; **1 update per year is a lead default awaiting PM confirmation** = 0.054 uA. Even 12 updates a year are 0.65 uA.
- fota module: chunks of 16 kB with retained offset, resume after a reset (test), yield to a locate request or an assistance download (IF-2), no start below the low-battery level, a gate check before every chunk (supercap), confirm or revert.
- Open: modem delta size and the nRF9151 modem firmware version policy; server-side offer message (MODEM_EVT kind 12 is the contract); full modem upgrade is not planned.
