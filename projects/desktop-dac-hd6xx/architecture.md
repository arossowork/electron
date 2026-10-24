# Architecture, revision A

## Working choices

Use a preprogrammed USB audio receiver feeding our own DAC, volume control, headphone amplifier and power/protection circuit. This keeps the compiler exercise focused on a mixed-signal PCB. The USB module firmware is supplied by its vendor; configuration and compatibility testing remain our responsibility. No custom USB stack or application firmware is planned in this first architecture.

```text
Computer -- USB-C --> Amanero Combo768 -- I2S + clocks --> ES9039Q2M + output stage
                                                                     |
                                                           stereo volume knob
                                                                     |
                                                                 OPA1622
                                                                     |
                                                            output protection
                                                                     |
                                                         6.35 mm jack / HD6XX
External DC adapter --> protection + regulators --> DAC and amplifier rails
```

Start qualification at stereo PCM up to 384 kHz, with 16/24/32-bit host formats. The preferred DAC is ESS ES9039Q2M in hardware mode 8, with no custom control firmware. Its external analog output stage, sequencing and actual library models still need schematic-level validation (parts.md). 768 kHz remains an extension, not a promised board capability. Word length describes the digital interface, not measured analog resolution. The design will not upsample all music simply to report a high sample rate.

## USB and clock evidence

[Amanero Combo768](https://www.amanero.com/site/combo768.html) offers USB-C or USB-B and lists EUR 79 on 2026-10-24. Its [preliminary Rev A datasheet, dated 04/08/2025](https://www.amanero.com/drivers/Combo768-D.pdf) specifies local 45.1584/49.152 MHz oscillators, master-mode I2S output, mute/rate/DSD signals, and a configurable maximum PCM rate. It lists the header MCLK as 22.5792/24.576 MHz. Verify that actual firmware behavior and every supported sample-rate/clock ratio match the DAC before freezing the interface.

Neither source supplies a numerical measured jitter specification. Record module revision, firmware, clock spectrum/phase noise where measurable, and analog-output sidebands/noise under USB activity. A locally generated clock avoids making USB packet arrival the sample-clock specification; it does not prove zero jitter or immunity to supply/ground noise. Keep the clock path short and referenced to ground. The module is not claimed to be galvanically isolated.

Configure the receiver's advertised rates to the final DAC's limits. The vendor maintenance workflow uses Windows; confirm access or seller preconfiguration. Hold the analog output muted during clock changes and unsupported DSD. USB-C denotes the connector; this remains USB 2.0 audio, without a requirement for USB 3 or USB Power Delivery. Verify operation with both C-to-C and A-to-C cables.

## Power and control partition

Keep host VBUS and the external adapter output separate. The provisional option is USB power for the receiver, an external regulated 5 V adapter for the custom board, clean local DAC rails and approximately +/-9 V amplifier rails. Check all power connection orders, signal back-powering and power-off mute behavior. Module maximum power is still marked TBD by its datasheet, so USB power compliance is not yet verified. Do not power the DAC from the module's auxiliary 50 mA output.

Hardware owns safe default mute, rail sequencing and headphone DC protection. Do not connect headphones until startup/shutdown and sustained-DC fault tests have passed into dummy loads and received independent review; this gate is separate from the priority of a product feature. If the selected DAC requires a controller, explicitly add and test the small control contract before schematic freeze. A generic MCU `interface.yaml` must not be fabricated solely to satisfy a workflow check; firmware checks are not applicable until there is actual custom firmware.

## Physical plan and estimates

Working enclosure: 160 x 100 x 40 mm. Working PCB: 140 x 80 x 1.6 mm, four layers. The 20 mm reduction in each planar dimension reserves 10 mm per edge for case walls, fixing and front/rear hardware; this is an allowance, not a selected enclosure drawing. The 32 mm internal depth budget reserves 8 mm of the outside height for enclosure structure. Proposed allocation: signals/components on the top, continuous reference ground on layer 2, power and remaining signals on layers 3/4, subject to fabrication stack-up and return-path review.

The original placeholder allocations have been replaced by real footprint memberships. Manufacturer STEP data gives the USB-C module a 32 × 69.258 mm assembly envelope, now reserved vertically on the left. Power occupies the rear-right region, with DAC and analog circuits below it. The real-footprint check passes at the agreed outline: 61% estimated front-face fill, 29.6 mm stack within a 32 mm allowance, and zero violations in the quick placement. See `notes/mechanics.md` for measured geometry and provisional assembly details. The actual enclosure, mounting method and panel hardware are not yet selected.

Rear USB and DC entries, front pot and jack establish the mechanical anchors. DAC, amplifier, conversion and protection blocks remain movable within named regions. The first power-to-analog region separation is 5 mm, a placement trial rather than a noise or safety standard. Use one continuous ground reference; do not split a ground plane merely to draw an analog/digital boundary. The floorplan test checks coarse area, geometry and congestion. It does not check actual routing, coupling, jitter, audio quality, or mounting-hole positions.

## Next gates

The circuit is captured in `design.yaml` and independently reviewed. Source checks have zero errors; generated KiCad ERC has zero errors, with four warnings on grounded hardware-mode/bidirectional DAC pins. All 683 assigned source pins match the exported KiCad netlist. The next blocker is the TI-derived OPA1622 model's operating-point convergence; startup/shutdown and DC-protection waveforms also remain unverified. Complete those circuit gates, select exact remaining passives/panel hardware and price the full prototype, then run the frozen-input placement/routing comparisons. Routing, routed DRC, post-layout electrical checks and first-article measurements are NOT CHECKED.
