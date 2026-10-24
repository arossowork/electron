# Revision-A hardware and firmware partition

The Amanero Combo768 supplies vendor USB-audio firmware. Its version, supported-rate configuration, I²S word framing, MCLK ratios, cable behavior and MUTE/DSDOE transitions must be qualified. No custom MCU or application firmware is present, so an invented MCU pin-allocation file or firmware scaffold would not validate this design.

The ES9039Q2M uses hardware mode 8 straps. Supply sequencing, reset/mute qualification, DC-fault detection, fault latch and normally-open headphone relay are hardware functions. They require explicit startup/shutdown/fault verification; the schematic alone does not establish the timing contract. `notes/dac-circuit.md`, `notes/power-circuit.md` and `notes/analog-circuit.md` record the corresponding signal names and assumptions.

Custom-firmware compilation and `interface.yaml` pin checks are not applicable to this revision. Vendor receiver configuration and real-host audio tests remain product gates.
