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

The schema is project-agnostic: nothing in it, or in `hwflow fw`, knows about thermostats, relays or NTCs as such. `projects/home-thermostat-3/interface.yaml` is a full example; `tests/test_fw.py` has a tiny one (an ESP32-C3, an LED, a wake button, a battery divider and an SHT40).

```yaml
version: 1
mcu: {ref: U1, power_nets: [+3V3], family: esp32c3}   # family optional: detected from value/mpn/symbol
signals:            # exactly the nets that reach the MCU (minus ground and power_nets); hwflow fails on a missing or extra one
  BTN:     {dir: in, active: low, pull: R2, wake: low, hw_change: D20, strap: "...", sleep: "...", req: R32, source: "..."}
  VBAT_EN: {dir: out, active: low, pull: R14}           # dir: out | in | adc | i2c | spi | uart | usb | xtal | reset
dividers: [{name: VBAT, net: VBAT_ADC, top: R15, bottom: R16, req: R22}]
buses:
  - {kind: i2c, name: env, sda: SDA, scl: SCL, pullups: [R5, R6],
     devices: [{ref: U2, name: SHT, addr: 0x44},                          # fixed address
               {ref: U3, name: ADS, addr_pin: ADDR, addr_map: {GND: 0x48, VDD: 0x49}, supply_net: SENS_EN,
                mux_nets: {AIN_REF: REF}, mux_pin_pattern: '^AIN(\d+)$'}]}  # address from the strap net
bindings:           # device pins the firmware relies on, checked against the device symbol
  - {name: K1_DRV, ref: U4, pins: {IN1: K1_IN1, IN2: K1_IN2}, defines: {set_in: 1}}
tables: [{name: NTC, kind: ntc_beta, r25_ohm: 10000, beta: 3950, excitation_net: SENS_EN, fixed: [R9], nodes: [NTC1_A],
          t_min_c: -20, t_max_c: 80, step_c: 1}]
params:   {energy_max_mj: {value: 9, unit: mJ, req: R9}}         # inputs of the limit expressions
limits:             # value, min, max: numbers or expressions over params and earlier limits (+ - * / ** min max ceil floor sqrt)
  relay_pulse_us: {value: 31000, min: "set_time_factor * set_time_max_ms * 1000", max: "...", window_warn: 2000}
  vbat_warn_mv:   {value: null, req: R22}                          # open: reported, never emitted
events:             # typed events between modules; a/b name the two int32 payload fields
  TEMP:       {a: channel, b: milli_c, req: R17}
  PULSE_DONE: {a: relay, isr: true}                                # posted by an interrupt handler, no module emits it
modules:            # one task each; `owns` = the only module that may drive those signals
  sensors:   {doc: "...", emits: [TEMP], consumes: [HOLD_READY]}
  actuators: {doc: "...", emits: [RELAY_STATE], consumes: [RELAY_CMD, PULSE_DONE], owns: [K1_IN1, K1_IN2]}
failure_modes: [{id: F1, what: "...", hw: "...", sw: "...", req: [...]}]
```

MCU knowledge lives in one small table per family in `src/hwflow/fw.py` (`FAMILIES`): how a symbol pin name gives the GPIO number (`^IO(\d+)` for the ESP32-C3-MINI-1), the ADC channel per GPIO, the deep-sleep wake-capable GPIOs and the strapping pins, with the datasheet as source. ESP32-C3 is the first entry; a new family is a new entry, not new code.

What `hwflow fw` derives, and the check behind each value:

| Value | Derived from | Error when |
|---|---|---|
| GPIO number | `design.yaml` MCU pin → symbol pin name → family pattern | a net reaches the MCU with no signal entry, or a signal has no net on the MCU |
| ADC channel | GPIO + family `adc` table | an `adc` signal on a pin without a usable channel |
| Wake level | GPIO + family `deep_sleep_wake` | a `wake` signal on a pin that cannot wake (a warning while `hw_change` names the pending decision) |
| Strap note | GPIO + family `strapping` | warning when a signal on a strapping pin does not state its reset level |
| Divider ratio and tolerance | part values and tolerances of `top` and `bottom` | `bottom` does not go to ground, or a resistor is not on the net |
| I2C address | `addr`, or the net on the ADDR pin + `addr_map` | the strap net is not in the map, SDA/SCL on other nets, VDD not on `supply_net` |
| Mux input | the device pin name of the net (`mux_pin_pattern`) | the net is not on a matching pin |
| Binding | device symbol pin names | a pin that is not on the named net |
| Table | `kind` (ntc_beta) + the fixed resistor value | the fixed resistor does not join the excitation net and the node |
| Limits | `value`, `min`, `max` expressions | a value outside its own range (a SW timing the HW cannot meet). `null` is reported as `open` |
| Events, modules | `events`, `modules` | an unknown event, an event nobody emits (and not `isr`), a signal owned by two modules, more than 61 events |

Outputs in `build/fw/`: `board.h` (constants), `events.h` (the event enum `EV_*`, one constructor `ev_x(a, b)` per event, `EV_NAMES`, one subscription mask `SUBS_<MODULE>` per module) and `modules.h` (one `int mod_<m>_task(fwk_task_t *, const fwk_event_t *)` per module). `uv run hwflow fw <proj> --scaffold` also writes, only where no file exists yet, `fw/modules/<m>.c` (a handler with one `case` per consumed event), `fw/app.c` (the task table), `fw/Makefile` and `fw/test/test_app.c`.

Every line in `board.h` carries a comment with its source: the ref, the pin, the requirement, or the note.

## How changes flow both ways

**HW to SW.** `build/fw/board.h` is generated from the sources, never edited, and `make test` regenerates it whenever `design.yaml` or `interface.yaml` changes.
- If layout swaps two MCU pins, only `design.yaml` changes. The GPIO numbers in `board.h` follow on the next `make test` (tests: `test_pin_moved_in_layout_follows`).
- If a net is added to the MCU without a contract entry, the result is an error, and `board.h` gets an `#error`, so the firmware cannot build against a stale contract.
- `BOARD_CONTRACT_ID` is a hash of both files. A firmware image can report it over MQTT, so a board and an image can be matched.

**SW to HW.**
- A timing that HW cannot meet is a `limit` error. It goes to the circuit stage, which changes the coil, the rail or the limit inputs, or to the product person when the limit is a requirement.
- A SW cost that a HW budget must carry goes to `conflicts.md` with the arithmetic, as a new conflict. The thermostat's ADS1115 data rate (P4) is an example.
- A SW requirement that HW must support goes to the circuit stage as a request. Examples are a wake-capable button pin (P9) and a separate guard gate.
- The firmware agent never edits `design.yaml`.

**Measured values.** Bring-up writes measured numbers into `interface.yaml` with `status: measured` and the bench record as `source`, for example the real relay set time or the A1/A2 polarity. A changed number re-runs `hwflow fw` and the host tests.

## Firmware stage

Firmware is written while JLCPCB makes and ships the board (about 1-2 weeks). Every project uses the same framework, `fw/framework/` at the repository root.

### The framework: a modular monolith on an event queue

One binary. A product is a set of modules; each module is one task with a private context (`t->ctx`), and modules talk only through typed events. There are no shared globals and no module calls another module's functions.

- **Events.** `fwk_event_t {type, src, dst, a, b}`, two int32 payload fields named in the contract. `fwk_post()` puts an event in one fixed ring queue (`FWK_QUEUE_LEN`, overflow counted by `fwk_dropped()`). A broadcast event goes to every task whose `SUBS_<MODULE>` mask has the type bit; a `dst` event goes to one task. `FWK_EV_INIT` reaches every task once at start.
- **Interrupts only post.** An ISR calls `fwk_post_isr()` and returns; all work runs in tasks.
- **Timers are events.** `fwk_timer_start(task, id, delay_us, period_us)` makes the scheduler post `FWK_EV_TIMER(a = id)` to the owner. There is no blocking delay above a few microseconds.
- **Cooperative stackless coroutines.** A task that must wait mid-sequence uses `CO_BEGIN`/`CO_END` (protothread style, a `switch` on the resume line). `CO_AWAIT(t, cond)` gives up the current event and resumes on a later one that satisfies `cond`; `CO_WAIT_UNTIL(t, cond)` also tests now (for state conditions). Locals do not survive a wait: state lives in the context. A task that never waits is a plain `switch` on the event type.
- **Scheduler.** `fwk_run_once()` feeds the watchdog, fires due timers, and delivers one event. A task that does not return blocks every other task and starves the watchdog, which is the intended failure detector.
- **Sleep hook.** With an empty queue, `fwk_idle()` sleeps until the next timer: deep sleep when no task has called `fwk_stay_awake(t, 1)` and the wait is at least `FWK_DEEP_MIN_US`, otherwise light sleep. An interrupt (a wake pin) ends it early.
- **HAL boundary.** The framework needs five functions (`fwk_hal.h`: time, irq save/restore, watchdog feed, idle). Device access goes through a project HAL (`board_hal.h`) of the same size. Both are portable C11 with no SDK include; `fw/port/<sdk>/` implements them.
- **Host harness** (`fw/framework/test/fwk_sim.*`): simulated time, event injection as interrupts at a given time, a trace of every posted event with its time, idle and deep-sleep totals, a watchdog model (largest feed gap, reset flag), and `fwk_sim_advance()` to model a busy or hung module. Tests assert on emitted events, not on internal state.

### Generated against hand-written

| Generated by `hwflow fw` (never edited) | Scaffolded once, then hand-written | Hand-written |
|---|---|---|
| `board.h`, `events.h`, `modules.h` | `modules/<m>.c`, `app.c`, `Makefile`, `test/test_app.c` | module logic, `lib/` pure helpers, `test/sim_board.c`, the host tests, `port/<sdk>/` |

A contract change regenerates the headers on every `make test`, so a renamed event, a moved pin or a changed limit breaks the build or a test instead of silently diverging.

### Verification without hardware

- **Host tests** (`make -C projects/<p>/fw test`, gcc). One test per `failure_modes` `sw` line and per contract number that a requirement limits, plus a simulated day (event counts, sensor-on time, no dropped event, watchdog gap). The framework has its own self-tests (`make -C fw/framework test`).
- **Simulated board.** `test/sim_board.c` models what the firmware sees: GPIO levels with timing, the cell through the divider, the I2C device registers, peripheral-timed outputs. Its numbers come from `notes/`; the electrical truth stays in SPICE.
- **SPICE as test vectors** (next step). Measures tagged with `req` in `build/status.json` can be exported as sim inputs; here they are copied by hand.
- **Multi-wake scenarios.** SPICE runs one wake per analysis, unless a testbench chains wakes (thermostat `r35_two_wake_*`). Logic that carries state across wakes is checked by host tests.

What waits for the board: the bring-up checklist in `partition.md`. It covers timings on a scope, current profiles against the energy budget, the boot and strap behaviour, the radio, and every contract number marked `assumed`.

## Gates

- After 1b: every requirement has a HW or SW owner in `partition.md`. Every safety function is HW. `interface.yaml` lists the failure modes.
- After 4 and 7: `uv run hwflow fw <proj>` gives 0 errors.
- After 9: `make -C projects/<p>/fw test` passes (it also runs the framework self-tests), and every `open` contract item is a product question in `inbox/` or `conflicts.md`.
