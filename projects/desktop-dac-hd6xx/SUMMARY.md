# DAC/headphone amplifier: simulation and PCB checkpoint

The **140 × 80 mm, four-layer board** has a component-level circuit for USB-C PCM audio through 384 kHz: Amanero receiver interface, ESS DAC, analog output filter, physical-volume interface, OPA1622 headphone driver, DC protection/relay, and monitored/sequenced supplies. The enclosure allowance remains 160 × 100 × 40 mm.

## Simulation repair

The inherited translated amplifier models changed the meaning of PSpice switches and LIMIT functions. Both libraries now retain TI's original bytes, and the build explicitly selects ngspice's PSpice parser. Sixteen focused OPA1622 cases and thirteen OPA161x/analog-chain cases pass. The source-derived analog chain delivers 3.92–3.94 Vrms into 300 Ω with the defined ideal DAC-current stimulus. Fourteen project amplifier measures and all fifty tolerance trials pass; the operating-point derating audit has no reported issues. [Model evidence and limitations](notes/model-repair.md).

The build also now retains timeout logs, rejects incomplete voltage/current audit data, and reports audit failures instead of an empty success. YAML save/load preserves datasheet URLs containing query parameters. The floorplan now rejects fixed-component region conflicts, and routing deadlines preserve completed copper without caching truncated output. These changes have independent review and regression coverage: **327 passed, 2 skipped, 14 slow tests deselected**. [Build-system repairs](notes/simulation-build-repair.md).

## Power and protection

The power circuit now buffers its supervisor output before charging the hold delays and uses a real logic AND gate for the regulator enables. Seventeen qualified calculations pass, including shutdown enable ordering, interrupted startup, and hold-up above the regulator's 3.6 V regulation threshold. Actual rail discharge order and arbitrary power ramps remain physical measurement gates. [Power analysis](notes/power-validation.md).

The protection circuit now uses lower-leakage sensing/timing parts, corrected comparator supply and package mapping, and revised startup timing. All 24 qualified scenarios pass, with a separate passive ngspice comparison. The combined startup/audio margin is 2.13 mV at the comparator; selected fault-latency corners trip within 1.615 s for ±1 V and 99.1 ms for ±9 V. Relay contact opening still depends on an unverified 20 ms release allowance. Startup takes about 18 s at the model midpoint and up to 37 s at the selected slow corner. [Protection analysis](notes/protection-validation.md).

Source checks and KiCad ERC have zero errors, and all 703 assigned pins match the exported schematic netlist. The corrected floorplan has 61% fill and zero placement or fixed-rule violations at the unchanged dimensions. The actual placed board has 1,666 segments and 238 vias. Local repair connected three ground islands; the retained board still has three open signal nets (DAC_L_P, I2S_BCLK and I2S_LRCLK), with zero other copper DRC errors. A bounded follow-up freed 12 nearby nets but timed out without improving connectivity; the accepted board was preserved. All 238 component positions remain unchanged. Static inspection flags nine long decoupling paths, including C106→U101 at 20.12 mm against the 4 mm screen. Silkscreen cleanup, return-path/signal-integrity checking and a clean final layout verdict remain outstanding. [Layout evidence](notes/layout-validation.md).

## Remaining product gates

| Area | Required evidence |
|---|---|
| Audio | Actual DAC full scale and gain, both-channel 300 Ω measurements, clipping/stability/noise/distortion |
| Power and protection | Capacitor bias/leakage, real startup/shutdown/brownout waveforms, rail currents/hold-up/inrush, relay opening with flyback, fault energy and amplifier enable behavior |
| Layout | Converter loops and local decoupling, fine-pitch escape, clock paths and return plane, complete routed DRC and post-layout checks |
| Mechanics | Actual pot/jack/enclosure, module support/harness, connector openings and assembly clearance |
| Cost | Remaining exact MPNs and a dated delivered BOM/assembly/enclosure/adapter estimate within EUR 250 |
| USB | Vendor firmware/configuration, host enumeration/playback at every accepted format and rate, cable/power-order tests |

All ten whole-product requirements remain unverified. No board has been fabricated, no parts ordered, and no headphone-connected test performed. The circuit models support a layout candidate within their documented assumptions; they do not establish a safe or production-ready physical device.
