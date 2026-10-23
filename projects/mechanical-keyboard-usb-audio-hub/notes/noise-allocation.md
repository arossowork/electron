# Noise allocation R10 / R11 / R22 (re-allocation, pass C, 2026-10-23)

Product numbers: R10 headphone floor <= 1.0 uVrms A-weighted (intrinsic plus coupled); R11 mic input-referred noise
<= 1.0 uVrms A-weighted at minimum gain. R22 (its own must) coupled noise <= 0.33 uVrms at both points.

## Old split (derived, conflicts.md C11)

C11 split the 1.0 uV before any simulation existed: coupled may raise the floor by 0.5 dB, so
Vi <= 1.0 / sqrt(1 + (10^0.05 - 1)) = 0.944 uV intrinsic and Vc <= 0.349 x 0.944 = 0.33 uV coupled.
The 0.944 is not a product number. It is a budget that assumes the coupled noise uses its whole 0.33 uV.

## Simulated coupled noise (R22 measures, pre-layout)

Method (design.yaml r22_coupled, hp_total_*): AC transfer. Both R22 generators are white in 20 Hz to 20 kHz, so
coupled_rms = V_gen,rms x sqrt(mean of |H_A(f)|^2 over a 1000-point linear sweep from 20 Hz to 20 kHz), where H_A is
the transfer from the generator to the A-weighted output. Generators: host VBUS noise 100 mV rms on every audio supply
entry (series E sources in FB950/951/952, the TPS65131 fit has no line rejection, so 0 dB is assumed there), and a
0.5 mV rms ground drop (0.5 mohm x 1 A). The two results add as RSS (R22 acceptance).

| point | VBUS part | ground part | coupled worst (0/25/40 C) |
|---|---|---|---|
| headphone out (32 / 300 ohm) | 1.5525e-4 uV | 7.76e-7 uV | **1.553e-4 uVrms** (hp_coupled) |
| mic input, minimum gain | | | **1.194e-5 uVrms** (mic_coupled_min_gain) |

Both are more than 2000 times below the 0.33 uV R22 limit. Earlier runs reported 142.9 uV for hp_total_32. That was a
testbench error, not a coupling path: the old method multiplied a 1 kohm resistor's thermal noise by an E-source gain
of 1.7e5 to 1.7e9. That gain was also in the operating-point matrix, so SPARSE lost its pivots and the op converged
to a false solution ("out of range for pow", singular at xu950.a2).

## Re-allocation

intrinsic_max = sqrt(1.0^2 - coupled_sim_worst^2)

- headphone: sqrt(1 - (1.553e-4)^2) = 0.99999999 uV, rounded down to **0.9999 uVrms** (hp_intrinsic_32, hp_intrinsic_300)
- mic: sqrt(1 - (1.194e-5)^2) = 1.0000000 uV, rounded down to **0.9999 uVrms** (mic_in_noise_min)

No margin is added below the product number. The total measures (hp_total_32 / hp_total_300, limit 1.0 uV) check
the product number directly as sqrt(intrinsic^2 + VBUS part^2 + ground part^2).

## Results against the limits (intrinsic, uVrms A-weighted)

| measure | 0 C | 25 C | 40 C | limit |
|---|---|---|---|---|
| hp_intrinsic_32 | 0.8912 | 0.9311 | 0.9542 | 0.9999 |
| hp_intrinsic_300 | 0.9029 | 0.9433 | 0.9667 | 0.9999 |
| hp_total_32 / _300 (worst) | | | 0.9542 / 0.9667 | 1.0 |
| mic_in_noise_min | 0.8849 | 0.9245 | 0.9475 | 0.9999 |

## What this depends on (open)

- **The ground drop is a placeholder.** Post-layout extraction replaces the 0.5 mohm x 1 A drop with the real shared
  ground impedance (R22 acceptance). At 40 C with 300 ohm loads the headphone total leaves
  sqrt(1.0^2 - 0.9667^2) = 0.256 uVrms for coupled noise. If post-layout coupled noise goes above 0.256 uV, R10
  fails even though R22 (0.33 uV) still passes. In that case the allocation goes back to the product person.
- The models are optimistic in two places. (1) The op-amp PSRR fit uses (V+ - V-) only, so a common-mode rail
  shift is rejected completely. (2) The DAC model has no AVCC rejection of its own: 3V3_A reaches the I/V stage only
  through the AVCC/2 bias, in common mode. A 60 dB model error would still leave the coupled noise at 0.16 uV,
  under the 0.256 uV that is left.
- Converter tones (400 kHz, 1.2 MHz, 1.38 MHz) are out of band and add nothing to a linear A-weighted figure
  (r22_tones debug probes).
