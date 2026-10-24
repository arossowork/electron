# Analog target and initial calculations

Current circuit capture and validation status are in `notes/analog-circuit.md` and `notes/circuit-baseline.md`. The calculations below explain the design target; they do not represent measured performance.

## User decisions and provisional inputs

- The intended load is Sennheiser HD 6XX, nominally 300 Ω. The electrical goal below does not set a listening-level or SPL target.
- An external DC adapter is approved. The one-prototype budget is €150–250 including board, parts, assembly, enclosure and PSU. Target enclosure is about 160 × 100 × 40 mm; the roughly 140 × 80 mm PCB is provisional.
- Amanero Combo768 with USB-C is the current USB-interface candidate at about €79, the preferred receiver, pending configuration/mechanical checks. This leaves €71–171 for the DAC, analog stage, PSU, enclosure and assembly.
- The first system target is 32-bit / 384 kHz, delegated as an engineering choice. ESS ES9039Q2M is now the preferred DAC (parts.md). Its external output stage and resulting full-scale analog voltage are not yet frozen.

## Proposed analog path

USB interface → ES9039Q2M plus its required analog output stage → dual-gang volume control → OPA1622 stereo headphone stage → 6.35 mm TRS jack. Use an external regulated 5 V DC adapter as the provisional supply source. A TPS65131 split-rail converter can generate approximately +9 V and −9 V for the OPA1622 from 5 V; its specified input range is 2.7–5.5 V. Keep the switching converter and return-current loop away from the DAC inputs and headphone output, then verify whether rail filtering is needed. Final power sharing with the USB interface and DAC must avoid tying the host’s USB VBUS to the adapter output.

OPA1622 is the selected revision-A headphone driver. TI specifies a dual audio amplifier, ±2 V to ±18 V operation, 10 V/µs typical slew rate, and low noise. Its VSON exposed thermal pad must connect to VEE (the negative rail), so package mapping and layout need explicit verification.

## Electrical target and gain

Engineering target: 4.0 Vrms maximum clean sine into 300 Ω, measured at the jack with both channels loaded. This is 53.3 mW/channel (P = V²/R), 13.3 mArms/channel, and 18.9 mA peak; the output peak is 5.66 V. ±9 V rails leave about 3.34 V of nominal peak headroom.

Set gain from the selected DAC’s measured/specified full-scale output: G = 4.0 V / V_DAC,rms. For a 2.00 Vrms DAC candidate, G = 2.00 V/V; a non-inverting 10 kΩ / 10 kΩ feedback pair is a starting point. If the selected DAC is 2.1 Vrms, reduce gain to about 1.90 V/V. These are conditional examples, not a PCM5102A selection. Put the stereo volume pot before the fixed-gain stage; measure channel tracking through its range, especially near minimum.

## Mute and DC protection

Hold the OPA1622 disabled by default. Enable only after the rails and DAC output have settled, coordinated with the DAC’s mute control. For jack isolation, consider a normally-open stereo relay with delayed close and DC detection on both amplifier outputs; a sustained offset beyond a chosen threshold should open the relay and disable the amplifier. Set the threshold and delay after checking startup transients and normal output offset. The amplifier shutdown function helps with transitions but is not by itself protection against a failed stage.

## Open validation before schematic freeze

Confirm the chosen DAC’s full-scale voltage, output impedance, power and interface compatibility; confirm the USB/adapter power and ground arrangement; verify OPA1622 symbol, footprint, pin mapping and VEE thermal-pad connection from TI and KiCad libraries; verify TPS65131 reference topology, output capability and switching-noise filtering; choose and verify the jack, pot and relay footprints; and price the assembled enclosure/PSU/BOM against the prototype budget. Simulate the amplifier at 300 Ω and build/measure both channels at 20 Hz, 1 kHz and 20 kHz, including clipping, distortion, startup transient, DC fault response and temperature. The component-level circuit has now been captured, pin/footprint mappings checked, and its generated schematic passed ERC with zero errors. The amplifier models now converge with explicit PSpice parsing, and the focused and source-derived analog-chain checks pass; see `notes/model-repair.md`. No physical circuit or measured hardware output result exists.

## Primary references

- [TI OPA1622 datasheet](https://www.ti.com/lit/ds/symlink/opa1622.pdf)
- [TI TPS65131 datasheet](https://www.ti.com/lit/ds/symlink/tps65131.pdf)
- [ESS ES9039Q2M v0.2.3 datasheet](https://www.esstech.com/wp-content/uploads/2026/05/ES9039Q2M_Datasheet_v0.2.3.pdf): Figure 33 output stage is transcribed in the current circuit; electrical performance remains unverified.
