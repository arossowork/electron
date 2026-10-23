# HW/SW partition: home-thermostat-3 (retroactive)

The method is in `docs/hwsw-codesign.md`. This file applies it after the fact to the round-8 board: `requirements.yaml`, `design.yaml`, `conflicts.md`, `notes/circuit.md` (firmware rules 1-12) and `SUMMARY.md`. The contract that follows from it is `interface.yaml`. `uv run hwflow fw projects/home-thermostat-3` turns that contract into `build/fw/board.h`, and `make -C fw test` runs the host tests against it.

Verdict: the method agrees with every hardware choice on the board. It finds four firmware-side gaps that no SPICE run or layout check could see: P4, P6, P8 and F2. Fixing them needs no hardware change. Two of them are product questions (Q1, Q3).

## Partition table

| # | Function | Deciding requirement and number | HW or SW | Today | Method |
|---|---|---|---|---|---|
| P1 | Relay pulse timing | R9: pulse >= 2 x 15 ms set time = 30.0 ms; <= 9 mJ at 3.418 V and 40.5 ohm = 31.2 ms. The window is 1.2 ms wide | SW (timer) | SW, rule 3: 31 ms + 5 ms brake | Agrees. An RC one-shot (C +-10 %) cannot hold a 1.2 ms window, and it costs parts against R30 (1.76 USD left). Adds: a us timer, never a 100 Hz OS tick (board.h warning) |
| P2 | Low-battery cut-off | R23 (should): at <= 2.0 V, reset both relays OFF. MCU still runs at 2.0 V (R3). A HW reset needs 19.4 mF (C7) | SW | SW, rule 7 | Agrees. The function is not safety-related: valve heads are normally closed and R34 covers a pulled cell. Adds: 2 consecutive readings (one read is +-30 mV, R22) |
| P3 | NTC linearisation | R17: +-0.3 C, of which the ADC chain takes 0.067 C (C8). Software share set to 0.05 C | SW | SW, rule 10 | Agrees. A 1 C table gives 0.005 C error with ADC quantization included, and 0.005 C resolution (host test) |
| P4 | NTC conversion rate | Rule 12: ADS1115 at 8-16 SPS. That is 3 x 62.5 ms = 188 ms of sensor power per minute at 16 SPS. R6 has 0.47 uA headroom (25.53 / 26 uA) | SW, inside a HW budget | `sleep_floor` models 10 ms of SENS_EN | **Differs.** See P4 below |
| P5 | ADS1115 against the internal ADC | R17 "guaranteed by design, no per-unit calibration". Internal ADC +-10 mV = +-0.95 C. GPIO budget: IO0/IO1 hold the crystal (R20), so no free ADC1 pins | HW | HW (U3, +1.07 USD) | Agrees. For production (25 USD target), the SW alternative is a per-unit 2-point calibration stored in flash, which saves about 1.07 USD. It contradicts R17 as written, so it is a production question, not a prototype one |
| P6 | Guard and R35 limiter decision | R35: +3V3 <= 3.45 V with lithium (abs max 3.6 V). Rule 8a decides from the last guard-ON reading against 3.35 V | SW over a fail-safe HW default | SW, rule 8 | **Differs.** See P6 below |
| P7 | Reverse-polarity protection | R4: reversed cells cause no damage and no current. The MCU is unpowered then, so this must be HW | HW | HW: M1/M2 + R14, guard OFF by default | Agrees. Rule 8b (SW reverse check) is a secondary measure. The safety case rests on R14 and brown-out, not on SW |
| P8 | Relay switching rate | R5: the energy budget carries 20 pulses per day (C6). That allows one change per relay every 2.4 h | SW (control law) | Not stated anywhere | **Differs.** See P8 below |
| P9 | Setup button | R32: button read at the 60 s control wake. IO9 cannot wake the C3 from deep sleep (only GPIO0-5 can), and GPIO0-5 are all used | SW polling | SW | Agrees on HW. A swap onto a wake pin moves a strap problem: VBAT_EN on IO9 would turn the 2.5 V strap caveat into download mode. The UX consequence is a product question: a press can take up to 60 s to register (Q4) |
| P10 | 32 kHz timebase | R20: <= +-30 ppm offline. The RC oscillator has no datasheet figure | HW (Y1) | HW | Agrees |
| P11 | Wi-Fi TX against relay pulse | R3: verified for one 350 mA burst, with no coil load at the same time | SW scheduling | SW, rule 4 | Agrees. Host test: no pulse while TX is busy |
| P12 | State after a cold boot | R9 / R23: the contact state is unknown after a battery change | SW | SW, rule 6 | Agrees. Host test: both relays re-asserted |

## P4. The NTC data rate is not in the energy budget (new conflict)

- Rule 12 was added after the energy budget was closed. It asks for 8 or 16 SPS, so that the sinc filter averages the ~45 mV ULPM ripple.
- The ADS1115 converts one channel per data period. The core reads three channels per control wake: REF, NTC1 and NTC2. That is 187.5 ms at 16 SPS, or 375 ms at 8 SPS. The `sleep_floor` testbench (R6) holds SENS_EN high for 10 ms.
- Load while SENS_EN is high:
  - U3: 0.15 mA.
  - NTC dividers: 2 x 0.165 mA.
  - R8: 0.033 mA.
  - The C3 in light sleep: 0.13 mA.
  - Total: 0.64 mA on +3V3.
- Extra battery current at 2.6 V and 88 % efficiency (host test `test_wake_energy`): **+2.7 uA at 16 SPS** and +5.6 uA at 8 SPS.
- Effect on the budgets:
  - R6 typical becomes about 28.2 uA, against the 26 uA limit.
  - R5 becomes 75.3 + 2.7 = 78.0 uA, against 80 uA, so it still holds at 16 SPS.
  - At 8 SPS, R5 fails (80.9 uA).
- Options for Q1:
  - **(a, recommended)** Keep 16 SPS and read every minute. Re-allocate R6 to 28.5 uA; R5 still holds. Cost 0 USD. This uses 2.7 of the 4.7 uA of R5 headroom, which P8 also wants.
  - **(b)** Read the probes every 5 minutes: +0.55 uA. Room temperature moves slowly, but R21 does not state a read interval.
  - **(c)** 860 SPS with 8 samples averaged in SW: +0.3 uA. Whether this averages the ripple well enough is unproven. It needs a run of the `sw_ulpm_*` testbench with the sample timing, then a bench check.
- The guard is ON for the whole wake on alkaline cells, which keeps the M3 divider on (about 100 uA). If the guard is released before the conversions, unless a pulse is pending, this saves about 0.3 uA. This is a SW change with no product impact, and the circuit stage confirms it in `sleep_floor`.

## P6. Rule 8a alternates the guard on lithium cells (SW defect against R35)

- Rule 8d stores the 50 us read as "the guard-ON reading". With 3.7 V lithium cells that read is 3.26 / 3.32 / 3.36 V at 0 / 25 / 45 C (notes/circuit.md, `dbg_vbat_read_w37`).
- At 0 C and 25 C that value is under 3.35 V. So on the next wake, rule 8a turns the guard ON, and M4 conducts 1-3 ms later. For the rest of the 20 ms recharge, C1/C2 at 3.45-3.52 V, and then the cells, feed the boost input. The notes give about 3.5 V for this case and up to 3.66 V for a lithium cell behind a closed guard.
- The guard-ON reading (about 3.7 V) then ends the guard, and the next wake stores a 50 us read again. The guard goes ON every second minute.
- Each SPICE case decides the guard from its own first wake. Only a test that steps the decision across wakes shows this. The host test `test_guard` reproduces it with `bat_guard_rule8a`.
- The fix, in SW only and implemented in `fw/lib/thermo_lib.c` (`bat_guard_wanted`) and used by `fw/modules/power.c`: a guard-OFF decision latches "not alkaline", and later wakes keep the cold-boot criterion (VBAT_P(off) < 2.85 V).
  - Fresh alkaline cells at >= 3.35 V then stay guard-OFF until VBAT_P(off) falls under 2.85 V, about 3.3 V at the cells. The cost is a few hours of body-diode loss.
  - Host test: lithium over 10 wakes holds IO2 low for about 40 us at most (M4 needs 1 ms).
- The circuit stage should add a two-wake SPICE case (lithium, guard decision from a stored 50 us read) to confirm. It needs no product decision.
- A related point: rule 8a says "VBAT_P read before IO2 goes low". While IO2 is high, M3 is off and the divider reads 0 V. The contract defines VBAT_P(off) as the first sample in the 50 us window, which C1/C2 hold for milliseconds.

## P8. The relay pulse budget sets the control law (missing requirement)

- C6 budgets 20 pulses per day (0.73-0.84 uA). One pulse at the battery is 8.25 mJ / (2.6 V x 0.88) = 1.0 uAh.
- 20 pulses per day over 2 relays is one state change per relay every 2.4 h (board.h `BOARD_RELAY_MIN_DWELL_S` 8640 s). An on/off heating loop with that dwell lets rooms swing by degrees.
- No requirement states a control behaviour: no hysteresis, no minimum cycle time, no comfort band.
- R5 headroom converted into pulses, at about 0.042 uA per daily pulse:
  - 4.7 uA without P4 gives about 130 pulses per day, a 22 min dwell.
  - 2.0 uA after P4 (a) gives about 68 pulses per day, a 42 min dwell.
- Q3: comfort (minimum cycle time) against battery margin. The firmware keeps the 2.4 h dwell from the budget until the product person decides.

## Firmware-side requirements the method adds (failure modes, interface.yaml)

- **F2. Watchdog.** No rule covers a hang with a coil driven: 3.3 V / 45 ohm = 0.24 W until reset. The ADW1 maximum continuous coil voltage is unverified (bench test 8). The contract asks for an RTC watchdog. The 1 s value is a placeholder until Q3 and the coil datasheet answer. After a reset the HW defaults are safe: DRV8837 input pull-downs give coast, and R8 holds SENS_EN low.
- **R22 warning threshold.** "A voltage that leaves >= 30 days" has no number. 30 days at about 78 uA is 56 mAh, about the last 6 % of an alkaline AAA. The voltage there must come from a discharge curve at this drain. The circuit stage should derive it from the cell datasheet; it is not guessed here. `board.h` leaves `BOARD_VBAT_WARN_MV` out and `hwflow fw` reports it as open.
- **Back-power.** U3 is powered from SENS_EN and its I2C pull-ups go to SENS_EN. SDA/SCL must be hi-Z or low whenever SENS_EN is low (host test: no I2C access to an unpowered U3).

## Open questions for the product person

Answered by the PM on 2026-10-23: see `conflicts.md` D17 (Q1: probes every 5 min), D18 (Q2), D19 (Q3: 40 min dwell, watchdog = pulse + Wi-Fi wake), D20 (Q4: the button must wake; HW pin swap pending). The text below is the record of the questions as asked.


- Q1 (P4): read the probes every minute at 16 SPS and accept a sleep floor of about 28.5 uA, with R5 still met (recommended). The alternative is reading every 5 min.
- Q2 (R22): approve deriving the low-battery warning voltage from the alkaline discharge curve. The circuit stage does the derivation.
- Q3 (P8, F2): the minimum relay cycle time. 2.4 h is what the budget allows today. About 40 min fits R5 after Q1 (a); about 20 min fits R5 without P4.
- Q4 (P9): accept that the setup button registers within 60 s (press and hold until the LED blinks).

## Bring-up checklist (waits for the board)

The SUMMARY.md bench tests, plus the firmware ones:
1. Measure the relay pulse width and brake on a scope at IN1/IN2 and A1/A2: 31.0 ms, inside 30.0-31.2 ms. Confirm the A1/A2 set polarity (bench test 8). If it is reversed, change `set:` in interface.yaml, not the code.
2. Check the guard sequence on lithium and alkaline over 10 wakes. IO2 low for < 1 ms on lithium. Measure the +3V3 peak (P6).
3. Measure the control-wake current profile against `sleep_floor`: the SENS_EN-on time and the energy per wake (P4, R6).
4. Run the cut-off on a bench supply ramp from 2.1 V down to 1.95 V: two wakes, both relays reset, then no pulse (R23).
5. Cut, then short, each probe lead: flagged within one wake, and that room goes OFF (R19).
6. Hang the firmware during a pulse (debug build): the coil current stops at the end of the RMT-timed pulse (31 ms), and the watchdog resets the chip within `BOARD_WDT_TIMEOUT_MS` (F2, D19).
7. Read the ADC at three bench voltages and check the VBAT error against +-50 mV (R22, adc_cali).
