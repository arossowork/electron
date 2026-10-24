# Desktop DAC and headphone amplifier

A USB DAC and desktop headphone amp for Luca's Sennheiser HD6XX, and a real reference board for improving the code-to-PCB build pipeline.

The agreed product is USB audio plus an external low-voltage power adapter, with a target of €150–250 for one working prototype including assembly, enclosure and supply. No parts have been ordered. Start with an approximately 160 × 100 × 40 mm enclosure and 140 × 80 mm four-layer PCB; these are proposed working dimensions, not a verified enclosure fit.

The first interface target is USB-C, stereo PCM with 16/24/32-bit host formats up to 384 kHz. The preferred path is an Amanero Combo768 USB-C receiver feeding an ESS ES9039Q2M DAC and OPA1622 headphone amplifier. The documented receiver clock output supports the proposed DAC configuration through 384 kHz; 768 kHz needs a different validated clock/firmware arrangement. The complete signal chain, its configuration and bench tests determine supported formats; a receiver capability alone is insufficient. Prioritize analog noise, distortion, clean output into 300 Ω and reliable startup over the maximum advertised sample rate.

`design.yaml` is an executable **architecture floorplan with placeholder blocks**, not a schematic or fabrication design. Every size reservation is identified as an estimate. Real parts, pins, courtyards, routing, electrical verification and cost remain to be completed. Do not send this project for manufacture.

- `requirements.yaml`: user decisions and separate engineering targets.
- `architecture.md`: signal path, clocking, power boundaries and physical assumptions.
- `parts.md`: receiver/DAC selection and clock compatibility.
- `analog-design.md`: amplifier calculations and protection proposal.
- `compiler-experiments.md`: baseline and controlled compiler experiments.
- `notes/floorplan-baseline.md`: measured initial check and setup findings.
- `state.md`: current stage and remaining work.

Keep the source circuit and the compiler changes separately reviewable. First measure unchanged main at `ca10fa8e0b9d67745787734b979e784f4422a3ec`. Fix only mechanically constrained items; let the compiler place ordinary components within explicit constraints. Preserve inspectable failure explanations and distinguish PASS, FAILED and NOT CHECKED.

Initial architecture check: placeholder floorplan PASS in 4.9 s; the deliberately undersized DAC region failed with a named fit diagnostic. All ten product requirements remain unverified. The circuit and routing baseline still require actual components and footprints.
