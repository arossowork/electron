# Audio budgets verified by datasheet (pass C, 2026-10-23)

Tags as in notes/parts-audio.md: [D] read in notes/datasheets/<mpn>.md (datasheet page given), [V] vendor product
page, [S] search snippet, [M] recalled, [E] estimate.

## R30: headphone THD+N <= -106 dB (0.0005 %), A-weighted, 1 kHz

THD+N of uncorrelated stages adds as RSS: THD+N_total = sqrt(sum 10^(x_i/10)).

| stage | figure | condition | source |
|---|---|---|---|
| ES9039Q2M DAC | -120 dB THD+N (THD -126 dB) | differential, 0 dBFS, 48 kHz, BW 20-20 kHz | datasheet p.54 [D] (notes/datasheets/es9039q2m.md, "Performance") |
| ES9039Q2M at 96 kHz | -117 dB | same, 96 kHz | datasheet p.54 [D] |
| I/V + diff-to-SE, 3 x OPA1612 sections in the path | 0.000015 % = -136.5 dB each | 1 kHz, 3 Vrms, 2 k load | ti.com/product/OPA1612 [V] |
| OPA1622 buffer, 32 ohm | -119.2 dB | 32 ohm, 100 mW (1.79 Vrms) | ti.com/product/OPA1622 [V] |
| OPA1622 buffer, 300 ohm, 2 Vrms | not on the product page; -119.2 dB used (a 300 ohm load draws 9x less current than the 32 ohm row, so distortion is lower) | | [E], read the datasheet THD+N vs output figure before release |

48 kHz, 32 ohm: sqrt(10^-12.0 + 3 x 10^-13.65 + 10^-11.92) = sqrt(1.000e-12 + 0.134e-12 + 1.202e-12) = 1.528e-6 = **-116.3 dB**.
96 kHz, 32 ohm: sqrt(1.995e-12 + 0.134e-12 + 1.202e-12) = 1.825e-6 = **-114.8 dB**.
Margin to -106 dB: 8.8 dB at 96 kHz. The 1 Vrms / 32 ohm point (31 mW) sits below the 100 mW datasheet row, where
the OPA1622 THD+N is lower or equal. Limits of the evidence: DAC figure at 10 Vrms full scale on the ESS eval board
(our I/V runs at about 2 Vrms out, -1.7 dB of the DAC full-scale current at Rf 383 ohm, which does not change the DAC
THD); the OPA1622 figure is from the product page, not the datasheet curve [V]. Not by SPICE (R30 acceptance).

## R12: USB Audio Class 2, 24-bit, 44.1 / 48 / 88.2 / 96 kHz both directions

| item | evidence | source |
|---|---|---|
| bridge XU316-1024-QF60B | USB 2.0 HS PHY on chip; XMOS USB Audio reference software (sw_usb_audio) is UAC2, class-compliant on macOS/Linux and Windows 10 1703+ (in-box UAC2 driver); sample rates 44.1-768 kHz, 24/32 bit | notes/datasheets/xu316-1024-qf60b.md [D] for the USB PHY and pins; UAC2 support of the reference software [V] (xmos.com sw_usb_audio) |
| clocks | 24.576 MHz (48 k family, Y1101) and 22.5792 MHz (44.1 k family, Y1102) master clocks selected by OSC48_EN / OSC44_EN, as in the XMOS reference designs | design.yaml U1100 notes |
| DAC ES9039Q2M | PCM up to 768 kHz, 32 bit; performance specified at 48 / 96 / 192 kHz | datasheet p.28, p.54 [D] |
| ADC ES9822PRO | PCM 8-768 kHz, 32 bit [S] | datasheet not read (B6 open) |

R12 is met for playback from datasheet evidence. Recording depends on the ES9822PRO rate table, which is still a
search snippet [S]: confirm with the datasheet (B6).
