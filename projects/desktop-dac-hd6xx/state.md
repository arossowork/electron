stage: 3-parts
status: running

Product: USB DAC + headphone amp for HD6XX, external adapter, EUR 150-250 prototype target.
Baseline engine: ca10fa8e0b9d67745787734b979e784f4422a3ec, unchanged.
Branch: design/desktop-dac-headphone-amp.
Architecture: USB-C receiver module, custom DAC/amp/power/protection board.
Working dimensions: 140 x 80 mm PCB; 160 x 100 x 40 mm enclosure, unverified.
Format target: 16/24/32-bit stereo PCM through 384 kHz; 768 kHz is not promised.
Current artifacts: requirements, architecture, analog calculations, compiler protocol, placeholder floorplan.
Preferred parts: Amanero Combo768 USB-C, ESS ES9039Q2M, OPA1622 (implementation pending).
Open: delivered BOM cost, USB module CAD/configuration/power, real parts/pins, protection circuit.
Checks: toolchain build PASS; placeholder floorplan PASS (4.9 s); impossible-fit fixture correctly FAILs.
Trace: 10 UNVERIFIED, 0 covered. Circuit, simulation, routing, DRC and bench NOT CHECKED.
Next: real mechanical models, priced BOM and verified symbols/footprints before circuit capture.
No parts ordered. No custom firmware or engine changes; container setup release pin corrected.
Evidence: notes/floorplan-baseline.md. All builds and checks have finished.
