# HW/SW co-design

This page describes how the flow decides which functions go into hardware and which into firmware. It also covers how the two sides stay consistent while the board changes, and how firmware is written and tested before the board exists. The worked example is `projects/home-thermostat-3/partition.md`, and the prototype tool is `uv run hwflow fw <proj>`.

## Where the stages sit

```
1 requirements -> 1b PARTITION -> 2 architecture -> 3 parts -> 4 circuit -> 5 review -> 6 optimize -> 7 layout -> 8 sign-off
                                                                                                          |
                                                              JLCPCB fab + shipping (1-2 weeks)   <------+
                                                              9 FIRMWARE (host-tested)  -> 10 bring-up on the board
```

| Stage | Reads | Writes |
|---|---|---|
| 1b partition (`hw-partition`) | `requirements.yaml`, `conflicts.md`, `brief.md` | `partition.md` (the table and its reasons), `interface.yaml` (draft: signals, timings, limits, failure modes), new conflicts in `conflicts.md` |
| 2 architecture | as today + `partition.md` | as today. The GPIO allocation in `architecture.md` follows the signals in `interface.yaml` |
| 3 parts | as today + `partition.md` (which functions have a part at all) | as today. Datasheet tables the firmware needs (address maps, truth tables, timings) go to `notes/datasheets/` |
| 4 circuit | as today + `interface.yaml` | `design.yaml`. The circuit stage fills the contract numbers that come from circuit results (pulse window inputs, divider choice, thresholds) and runs `hwflow fw` as a check |
| 7 layout | as today | pin swaps go into `design.yaml` only. `hwflow fw` must stay at 0 errors |
| 8 sign-off | as today | adds the gate: `hwflow fw` has 0 errors, and every `open` item in `interface.yaml` is either a product question or done |
| 9 firmware (`fw-firmware`) | `interface.yaml`, `build/fw/board.h`, `partition.md`, the firmware rules in `notes/` | `fw/` (portable core, HAL, host tests, port stub), `notes/firmware.md`. When a timing cannot be met, it adds a row to `conflicts.md` |
| 10 bring-up | `partition.md` bring-up checklist, `SUMMARY.md` bench tests | measured values back into `interface.yaml` (status `measured`) |

## Partition criteria

The partition stage goes through the requirements one function at a time. For each function, the first rule in the table below that applies decides it. The stage records the requirement ID and the number behind each decision.

| NFR | Threshold | Goes to | Example (thermostat) |
|---|---|---|---|
| Safety or fail-safe | Must work with the MCU hung, in reset or unpowered | HW. SW may add a secondary check, but the safety case does not rest on it | Reverse guard M1/M2 + R14 (R4). The guard is OFF by default |
| Absolute maximum of a part | An SW mistake could exceed it | HW default state is safe. SW logic gets a host test over several wakes | R35 limiter: M4 OFF by default. Rule 8a gets a host test (P6) |
| Latency or real-time | Response < 10 us, or a timing window narrower than the timer jitter | HW | none |
| Timing window | Window >= 10 x the timer resolution (1 us esp_timer) | SW, with a us timer and never an OS tick | Relay pulse 30.0-31.2 ms (R9) |
| Accuracy | An error budget that SW calibration cannot prove "by design" | HW | ADS1115 against the internal ADC (R17, C8) |
| Power | SW duty cycle and conversion time cost more than the budget headroom | Either. The SW timing becomes a line of the energy budget | ADS1115 data rate (P4), relay pulses per day (P8) |
| Unit cost and BOM | A part that SW can replace at equal verification strength | SW | NTC linearisation in a table, not a network |
| Size and pins | No pin or area is left for the HW option | SW, or a pin swap | Button on IO9, polled (P9) |
| Reliability | Wear-out or stored state the HW cannot see | SW keeps the state and HW gives the default | Relay state re-asserted after a cold boot (rule 6) |
| Certification | Changes certified behaviour (radio, safety) | Keep in HW, or in a certified module | Pre-certified Wi-Fi module (R16) |
| Flexibility and field update | Behaviour the product person may change after launch (schedule, control law, thresholds) | SW, with the number in `interface.yaml` | Set-points, hysteresis, cut-off count |
| Development effort | Only when the rules above leave a tie | the cheaper side for the prototype | none |

A function that ends up split between the two sides (a HW default with a SW override) gets one failure-mode row in the contract: what HW guarantees, what SW must do, and the requirement involved.

## The contract file: `interface.yaml`, not new `design.yaml` fields

Every number in the contract that `design.yaml` already holds is derived from it, and the contract never copies one. The contract holds only what `design.yaml` cannot express.

Why the contract is a separate file:
- **Build cost.** `hwflow` hashes the whole parsed `design.yaml` into every stage hash (`_design_hash`). A firmware field there, such as a timing or a threshold, would invalidate the SPICE, Monte Carlo and layout caches on every edit. That is 10 to 30 minutes per rebuild for a change that cannot move a single copper track. `interface.yaml` is outside the hash.
- **Ownership.** One writer per file. The circuit and layout agents own `design.yaml`. The partition and firmware agents own `interface.yaml`, so the firmware stage can run while layout is still being edited.
- **Schema cost.** New `design.yaml` fields need `docs/design-yaml.md`, `hwflow check` rules and tests. `interface.yaml` needs only `hwflow fw` and its own short reference below.
- **Pros of a `design.yaml` section** (rejected): one file to read, and no risk of two files drifting. `hwflow fw` removes the drift risk: it cross-checks the two files and fails on any mismatch.

Format (the thermostat file is the full example):

```yaml
version: 1
mcu: {ref: U1, gpio_from_pin_name: '^IO(\d+)', power_nets: [+3V3], adc1_channel_of_gpio: {3: 3}, adc1_source: "..."}
signals:                    # exactly the nets that reach the MCU; hwflow fails on a missing or extra one
  VBAT_EN: {dir: out, active: low, pull: R14, strap: "...", sleep: "hi-Z", req: [R4, R35], source: "rule 8"}
dividers:  [{name: VBAT, net: VBAT_ADC, top: R15, bottom: R16, req: R22}]          # ratio from part values
i2c:       [{name: sensors, sda: SDA, scl: SCL, pullups: [R6, R7],
             devices: [{ref: U3, addr_pin: ADDR, addr_map: {GND: 0x48, VDD: 0x49}, mux_nets: {AIN_REF: REF}}]}]
ntc:       {r25_ohm: 10000, beta: 3950, excitation_net: SENS_EN, channels: [{name: NTC1, node: NTC1_A, fixed: R9}]}
relays:    [{name: K1, relay: K1, driver: U4, in1: K1_IN1, in2: K1_IN2, enable: SENS_EN, set: in1}]
relay_coil: {set_time_max_ms: 15, set_time_factor: 2, energy_max_mj: 9, coil_min_ohm: 40.5, rail_max_v: 3.418}
timing:    {relay_pulse_us: {value: 31000, min: 30000, unit: us, req: R9, source: "rule 3"}}
power:     {vbat_cutoff_mv: {value: 2000, req: R23}, vbat_warn_mv: {value: null, req: R22, source: "open"}}
failure_modes: [{id: F1, what: "...", hw: "...", sw: "...", req: [...]}]
```

What `hwflow fw` derives, and the check behind each value:

| Value | Derived from | Error when |
|---|---|---|
| GPIO number | `design.yaml` U1 pin → symbol pin name (`IO7`) | a net reaches the MCU with no signal entry, or a signal entry has no net on the MCU |
| ADC channel | GPIO + `adc1_channel_of_gpio` | an `adc` signal on a pin without an ADC1 channel |
| Divider ratio and tolerance | part values and tolerances of `top` and `bottom` | `bottom` does not go to ground, or a resistor is not on the net |
| I2C address | the net on the device's ADDR pin + `addr_map` | the strap net is not in the map, or SDA/SCL are on other nets |
| ADC mux input | the device pin name (`AIN1`) of the net | the net is not on an `AINn` pin |
| Relay drive | driver symbol pins `IN1`/`IN2`/`~{SLEEP}` | a binding that does not match the driver's pins |
| Relay pulse window | `relay_coil` inputs | `relay_pulse_us` falls outside the window (a SW timing that HW cannot meet) |
| NTC table | `ntc` + the fixed resistor value | the fixed resistor does not join the excitation net and the probe node |
| Timing and power limits | `value`, `min`, `max` | a value outside its own range. A `null` value is reported as `open` and not emitted |

Every line in `board.h` carries a comment with its source: the ref, the pin, the requirement, or the note.

## How changes flow both ways

**HW to SW.** `build/fw/board.h` is generated from the sources, never edited, and `make test` regenerates it whenever `design.yaml` or `interface.yaml` changes.
- If layout swaps two MCU pins, only `design.yaml` changes. The GPIO numbers in `board.h` follow on the next `make test` (tests: `test_pin_moved_in_layout_follows`).
- If a net is added to the MCU without a contract entry, the result is an error, and `board.h` gets an `#error`, so the firmware cannot build against a stale contract.
- `BOARD_CONTRACT_ID` is a hash of both files. A firmware image can report it over MQTT, so a board and an image can be matched.

**SW to HW.**
- A timing that HW cannot meet is a `relay-window` or `limit` error. It goes to the circuit stage, which changes the coil, the rail or the limit inputs, or to the product person when the limit is a requirement.
- A SW cost that a HW budget must carry goes to `conflicts.md` with the arithmetic, as a new conflict. The thermostat's ADS1115 data rate (P4) is an example.
- A SW requirement that HW must support goes to the circuit stage as a request. Examples are a wake-capable button pin (P9) and a separate guard gate.
- The firmware agent never edits `design.yaml`.

**Measured values.** Bring-up writes measured numbers into `interface.yaml` with `status: measured` and the bench record as `source`, for example the real relay set time or the A1/A2 polarity. A changed number re-runs `hwflow fw` and the host tests.

## Firmware stage

Firmware is written while JLCPCB makes and ships the board (about 1-2 weeks).

What it generates:
- `build/fw/board.h`, from `hwflow fw`.
- `fw/core/`: portable C11 with no SDK includes. It holds the control loop, conversions, state machines and limit logic, and it uses only `fw/include/hal.h` (about a dozen functions).
- `fw/test/`: a simulated HAL (`sim_hal.c`) that models the board at the level the firmware sees: cell voltage and guard behaviour, the divider, the I2C device registers, the bridge truth table and relay contacts. The host tests check the requirements: pulse length against R9, the cut-off sequence for R23, the NTC error budget for R17, and the guard decisions over several wakes for R35.
- `fw/port/<sdk>/`: the SDK port. Here it is a stub `main.c` that lists what the ESP-IDF port needs.
- `notes/firmware.md`: the firmware rules, now traced to `interface.yaml` entries.

Verification without hardware:
- **Host unit tests** (`make -C projects/<p>/fw test`, gcc). One test per `failure_modes` `sw` line and per contract number that a requirement limits.
- **Simulated HAL.** Its electrical numbers come from `notes/`. It is a behaviour model, and the electrical truth stays in SPICE.
- **SPICE as test vectors** (optional, next step). The measures tagged with `req` in `build/status.json`, such as `dbg_vbat_read_w37` (the voltage the ADC sees at 50 us), can be exported as test inputs. The host test then feeds the firmware the voltages the circuit really produces. Here these values are copied into `sim_hal` by hand.
- **Multi-wake scenarios.** SPICE runs one wake per analysis. Logic that carries state between wakes, such as rule 8a, is checked only by host tests (thermostat P6).

What waits for the board: the bring-up checklist in `partition.md`. It covers timings on a scope, current profiles against the energy budget, the boot and strap behaviour, the radio, and every contract number marked `assumed`.

## Gates

- After 1b: every requirement has a HW or SW owner in `partition.md`. Every safety function is HW. `interface.yaml` lists the failure modes.
- After 4 and 7: `uv run hwflow fw <proj>` gives 0 errors.
- After 9: `make -C projects/<p>/fw test` passes, and every `open` contract item is a product question in `inbox/` or `conflicts.md`.
