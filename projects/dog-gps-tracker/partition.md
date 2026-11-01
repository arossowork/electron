# HW/SW partition (stage 1b)

Parts are not picked. The MCU may sit inside the cellular SiP (nRF9151 class); the contract keeps signal names generic and `mcu.ref` is null until the circuit stage. All currents below are battery side unless marked "modem side" (factor 1.3, conflicts.md assumptions). No design.yaml yet, so `hwflow fw` has not run.

## Table

| Function | Deciding requirement and number | HW / SW | Reason |
|---|---|---|---|
| Reverse-cell protection | CERT-2 (cells replaced by user), PWR-4 1 A battery peak; a wrong cell must not exceed any part's abs max | HW | Safety rule: works unpowered and with the MCU in reset. Keyed holder plus series element; drop at 1 A is a stage 2 number |
| Boost UVLO and enable default | PWR-1: input 1.8 V min; sagging cell must not chatter the modem | HW | Fail-safe: boost state set by its own UVLO, not by firmware. MCU may only request shutdown |
| Brown-out / reset supervisor on the modem rail | PWR-4: modem rail >= modem minimum (about 3.0 V, C2, datasheet to confirm) during 0.5 A, 2 ms burst, 1.5 ohm source | HW | Must act with MCU hung or in reset. SW adds only a reset-cause log |
| Hung-MCU watchdog | PWR-3 0.268 mA: a hung modem in TX (about 1 A) empties the cells in 2350 mAh / 1 A = 2.4 h | SW first (internal WDT of the SiP) + HW option (open Q2) | SiP WDT is free (cost) but dies with the MCU; external window WDT costs Iq against the 3.5 uA headroom (10 - 6.5 uA, PWR-5). PM question |
| Buzzer default off and stuck-GPIO safety | IF-7: 80 dBA at 10 cm; 30 mA x 10 s x 2/month = 0.2 uA budget (C1) | HW default + SW sequence | Pull-down gives off at reset. A stuck-high GPIO would draw 30 mA = 3.3 days and sound continuously: AC-coupled or time-limited drive is requested from circuit stage (F3). SW sets pattern and duration (10 s) |
| Buzzer on/off command, duration, pattern | IF-7 remote-triggered, not time critical | SW | Flexibility: duration is a field-tunable number, window far above 10x timer resolution |
| Status LED blink | IF-6: LED <= 2 uA; 1 ms per 10 s at 5 mA = 0.5 uA | SW | 0.5 uA < 2 uA with 1.5 uA slack. Timing window is 1 ms wide (>= 10 x 1 us timer, ok) |
| Button wake and debounce | IF-6; wake from sleep at 0 uA polling | HW wake pin + SW debounce | Needs a wake-capable pin (need: wake MCU from sleep on press). ESD TVS is HW (ENV-3: 8 kV) |
| eDRX 81.92 s request and fallback | IF-2: 81.92 + 30 + 10 = 121.92 s <= 122 s (0.08 s slack) | SW (modem AT/NAS config) | Flexibility: network grants eDRX or not, only SW can see it. Fallback to 60 min poll when no eDRX grant, else 3.7 months instead of 30 (C1) |
| Modem PSM/eDRX sleep | PWR-5: 10 uA board floor, chosen mode 108 uA (PWR-3 limit 268) | SW policy, HW floor | Floor is a sum of datasheet Iq values (HW choice); paging is a SW line of the budget |
| GNSS fix per request with A-GNSS | IF-3: TTFF with assistance <= 30 s; 1.5 C per request = 35 uA | SW (timeout 30 s) | Time-shared with LTE on integrated SiP; SW must serialise. Fix timeout is IF-2 latency number |
| Lost mode 1 fix/min, <= 2 h | PWR-3: 120 x 0.5 C = 60 C/month = 30 uA battery | SW | Owner-started, counter-limited. At 30 uA per session, at most 6 sessions/month fit PWR-3: 78 + 30 n <= 268 -> n <= 6 |
| Position upload / FOTA | IF-8 FOTA; no budget line yet | SW | See conflict C4 |
| Battery level | PWR-6: error <= 50 mV; PWR-5 headroom 3.5 uA | HW divider + SW sample | Always-on divider needs R_total >= 3.6 V / 3.5 uA = 1.03 Mohm; otherwise switched by an MCU pin (default off in HW). Accuracy is HW (divider tolerance, ADC), SW only samples during load |
| Accelerometer wake-on-motion (could) | IF-9: Iq <= 2 uA | HW interrupt, SW policy | Motion gating saves energy only if SW lets lost-mode idle; optional |
| SWD and UART pads | IF-8 | HW | Pads reachable in battery compartment; SW has the UART log |

## Energy budget lines (battery side, uA, x1.3 from C1)

| SW function | modem side | battery | note |
|---|---|---|---|
| sleep floor | 5 | 6.5 | PWR-5 limit 10 |
| eDRX paging 81.92 s | 18 | 23.4 | 1.5 mC per cycle |
| TAU 12 h | 1 | 1.3 | operator may force 54 min: 12 -> 15.6 |
| 2 requests/day A-GNSS | 35 | 45.5 | 1.5 C each |
| lost mode, once a month | 23 | 29.9 | 10.8 mA battery average during the 2 h |
| LED + buzzer | 1 | 1.3 | 0.5 + 0.2 uA used, IF-6/IF-7 limit 2 + 2 |
| total eDRX | 83 | 108 | PWR-3 limit 268, headroom 160; PWR-2 limit 1630 |
| total 60-min poll fallback | 120 | 156 | PWR-2 and PWR-3 both met |

## Conflicts found (also in conflicts.md)

- C4: FOTA download and A-GNSS assistance download time have no line in the budget or in the 122 s latency.
- C5: IF-2 latency 121.92 s vs 122 s leaves 0.08 s; any attach or RRC setup time beyond the 10 s upload breaks it.

## Open questions

1. PM: is the 0.08 s latency slack in IF-2 acceptable, or is the 30 s fix / 10 s upload the intended allowance? (C5)
2. PM (cost against safety): external watchdog or supervisor with Iq about 0.1 to 1 uA and about 0.3 USD, or SiP WDT only? Hung modem in TX empties cells in 2.4 h.
3. FOTA image size and frequency (C4): `null` in the contract.
4. Is the buzzer allowed to be limited to a hardware time cap (for example 30 s)? Needed for dog hearing safety. PM.
5. Modem minimum supply voltage and brown-out threshold: datasheet, stage 3 (C2 says about 3.0 V, unconfirmed).
6. External GNSS LNA or SAW enable pin needed by the chosen module: stage 3.

## Bring-up checklist

1. Power on from a bench supply at 1.8, 2.4, 3.0, 3.6 V with 1.5 ohm in series: boost starts, UVLO releases, no reset loop.
2. Reverse the cell polarity (keyed holder): board must survive and draw 0.
3. Sleep current at 3.0 V with modem in PSM: <= 22 uA at 50 C (PWR-5, interface.yaml sleep_floor_ua_max 22; 10.4 uA typ at 23 C), source meter.
4. TX burst at -20 C chamber: scope the modem rail, minimum >= modem minimum (PWR-4); record supervisor trip level.
5. Current profile for one eDRX cycle, one A-GNSS request, one lost-mode minute; integrate against the table above (assumed values become measured).
6. Network refuses eDRX (test SIM profile): firmware switches to 60-min poll; log contract flag.
7. Position request latency end to end <= 122 s over 10 requests; record worst.
8. MCU hung (halt via SWD): buzzer silent, LED off, modem state per F1/F2, watchdog reset time.
9. Button wake from sleep, ESD hit on the button, contact discharge 8 kV.
10. Buzzer SPL at 10 cm >= 80 dBA with membrane fitted; stuck-GPIO test (F3).
11. Battery voltage readout against meter at 2.0, 3.0, 3.6 V and during TX: error <= 50 mV (PWR-6).
12. FOTA update of a test image, current and time measured.
13. IP67 immersion 1 m, 30 min, then repeat 3.

## Firmware bring-up items (stage 9; host tests cannot prove these: fw/test/sim_board.c numbers are assumptions)

14. `AT%XVBAT` before the first attach (CFUN=4): returns VDD_4V0 within 5 mV of a meter; if it needs CFUN=1, use the SAADC VDD channel for the boot gate. Instrument: bench supply + DMM. Pass: boot gate holds the attach until VDD_4V0 >= 3.9 V on a drained supercap (scope VDD_4V0, first attach after >= 3.9 V).
15. Supercap recharge time between bursts: scope VDD_4V0 after an upload burst at -20 C and on alkaline; pass: back to >= 3.9 V within gate_retry_s x 1 (3 s), else `gate_retry_s` and the 125 s budget change (conflicts.md C13).
16. Loaded VBAT sample point: scope VDD_4V0 and VBAT_SENSE with a TX burst; pass: the 20 ms `tx_sample_delay_ms` sample falls inside a PA burst (cell voltage within 50 mV of the DMM under load, PWR-6, F7); else sample on a modem TX-start notification.
17. TX back-off: cold chamber (-20 C), alkaline cells, 100 ms CE burst; scope VDD_4V0 min >= 3.2 V and no RESET_N pulse at 20 dBm; record whether 23 dBm resets below 0 C (PWR-7).
18. eDRX grant on the 1NCE SIM in Italy (LTE-M, then NB-IoT): AT+CEDRXRDP shows 81.92 s; with a SIM profile that refuses eDRX the device logs `LINK_STATE mode=2` and attaches once per 60 min. Instrument: UART log, current profiler. Pass: both paths, and eDRX retried on each poll attach (F2).
19. End-to-end request latency from page to server receipt over 10 requests, outdoors, canopy and open sky: worst <= 40.5 s after the page (125 s total, IF-2); log the GNSS fix time with and without assistance (28 s cold vs 5 s EPO, IF-2 margin 2 s).
20. LC76G power gating: scope GNSS_RX, GNSS_RST_N, VDD_GNSS at GNSS_EN off/on; pass: GNSS_RX low and VDD_GNSS leakage < 5 uA while off, no back-power (> 0.3 V on VDD_GNSS with EN low), `gnss_boot_ms` 100 ms enough for the first NMEA sentence (IF-3).
21. EPO/assistance download over LTE: bytes, time and charge with a current profiler (replace the 8.5 s x 50 mA assumption, C4); LC76G accepts the injected file and reports TTFF <= 30 s on day 3 of file age.
22. Sleep current with the firmware running (LED 10 s tick, WDT WAKE each 60 s, hourly VBAT sample): <= 22 uA at 3.0 V (PWR-5) and add the wake cost (CPU wakes ~10000/day not in the host energy model, ~0.35 uA at 1 ms x 3 mA each).
23. TPL5010: measure WAKE period (R7 DELAY, 60 s target), DONE pulse width accepted, and the delay from a halted CPU (SWD halt) to the RESET_N pulse; pass: <= 128 s (buzzer_hw_cap_s, C15; WAKE period 59.8 s nominal for R7 22 k), buzzer silent after <= 10 s of a halted CPU with the buzzer on (PWM sequence finite, IF-7, F3).
24. Buzzer: SPL >= 80 dBA at 10 cm at the 4 kHz resonance, current 1 mA typ, both legs low after the 10 s (scope BUZ_P/BUZ_N): sequence ends without CPU.
25. Button: wake from System ON sleep on press, bounce with a scope, 30 ms debounce enough; hold 5 s cancels lost mode; ESD 8 kV does not reset (ENV-3).
26. Lost mode session: log 120 fixes + uploads over 2 h on the bench; integrate battery charge, pass <= 50.5 C per session (host model) and the 6-per-month limit survives a battery pull (quota in retained storage).
27. Brown-out path: force VDD_4V0 below 2.99 V; the supervisor reset is logged as RESET_BROWNOUT, the first attach waits 60 s at 20 dBm and LOW_BATT is reported (F4).
28. Brown-out counter retention: force VDD_4V0 below 2.99 V three times in a row (supercap drained between): RESET_N pulses, `board_brownout_load` returns 1, 2, 3 and the first attach waits 60, 120, 240 s; after a good attach it returns 0; a cell pull (power-on reset) also returns 0. If the retained RAM is lost on the pin reset, switch `board_brownout_*` to NVS. Instrument: UART log, bench supply, scope on RESET_N.
29. RRC set-up time from the eDRX paging window to the first uploaded byte (conflicts.md C13): time from the page (modem log) to the server receipt, 10 requests at -20 C, 20 C and 50 C; pass: RRC set-up + upload <= 10 s (`upload_timeout_s`); the IF-2 slack is 0.52 s. Instrument: modem trace (nRF Monitor), server timestamps.
30. Low-battery thresholds (conflicts.md C16): discharge a fresh Li-FeS2 pair and an alkaline pair at the worst-month average (222 uA mean, pulse profile) and log idle voltage and remaining charge; pass: LOW_BATT (idle < 2600 mV or 3 sagging bursts at T >= 0 C) arrives with >= 73.2 mAh (14 days at 217.7 uA) left on both; else move `low_batt_idle_mv`. PM confirms the 14 days.
31. FOTA end to end (IF-8, C4): offer a 300 kB test image, measure time and battery charge (expected 1.7 C, 24 s + 19 x 0.5 s), pull the cells at 50 %: it resumes at the retained offset; send a locate request at 50 %: it is answered within 40.5 s of the page and the download resumes; flash an image that never attaches: MCUboot reverts after `fota_confirm_s` or the next reset. Modem delta: apply one and check the modem version. Instrument: current profiler, UART log.
32. PWM buzzer sequence: scope BUZ_P/BUZ_N while the CPU is halted (SWD) at the start of a 10 s sequence: it stops by itself at 10.000 s +- 1 ms, both legs low (confirm REFRESH 99 x LOOP 400 on the nRF9151 PWM, widths of the REFRESH and LOOP fields, LOOPSDONE -> STOP shortcut).
33. LC76G EPO: obtain the Quectel/MediaTek EPO server credentials (vendor, project, device_id), download EPO_GPS_3_1.DAT (27648 B), inject (Flash EPO) and measure the TTFF on file ages 1 h, 36 h, 60 h and 72 h with the nRF9151 GPS+Galileo configuration; pass: <= 30 s at 60 h (IF-2), and the cold fix with an expired file still <= 28 s.
