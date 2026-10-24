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

The initial floorplan reserves 75 x 35 x 15 mm for the USB module assembly, 24 x 22 mm for DAC and local support, 20 x 22 mm for power conversion, 24 x 20 mm for the amplifier, and 22 x 18 mm for output protection. Pot and jack reserves are 22 x 18 mm and 16 x 28 mm. These are deliberately explicit engineering estimates; they are not verified package dimensions. The module CAD, connector drawings and chosen enclosure must replace them before layout. The four-layer choice and block margins can change before the measured circuit baseline is frozen.

Rear USB and DC entries, front pot and jack establish the mechanical anchors. DAC, amplifier, conversion and protection blocks remain movable within named regions. The first power-to-analog region separation is 5 mm, a placement trial rather than a noise or safety standard. Use one continuous ground reference; do not split a ground plane merely to draw an analog/digital boundary. The floorplan test checks coarse area, geometry and congestion. It does not check actual routing, coupling, jitter, audio quality, or mounting-hole positions.

## Next gates

Verify the preferred ESS DAC implementation against the public reference circuit, footprint/assembly feasibility and total cost. Verify actual library symbols/pads, replace the placeholder envelopes, then capture the circuit and simulations. Capture the unchanged-engine circuit build before changing placement/routing code. A complete board requires independent electrical review, ERC/DRC, requirements evidence and first-article measurement; none has been completed.
