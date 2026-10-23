# Conflicts, round 3

Numbers are first estimates; the architecture stage replaces them with datasheet values and quotes.

## Closed

- C1 fast charge vs host power: RESOLVED (PM round 1, q1). Optional 65 W PD input on the back. Budget update for the studio audio (C10): 2 x 20 W + 2 x 7.5 W + internal ~0.5 A x 5 V = 2.5 W -> 57.5 W; at 92% efficiency 62.5 W <= 65 W (2.5 W left).
- C2 audio noise vs chargers: replaced by C11 (studio tier, 4-layer).
- C3 USB 3 vs USB 2: RESOLVED, USB 3 (PM round 1, q4).
- C4 front connectors: replaced by C7.
- C5 2-layer vs USB 3 vs audio: RESOLVED (PM round 2, q1): 4-layer full board (R34). R23 (2-layer) dropped. 440 mm exceeds JLCPCB economic 4-layer PCBA (350 mm), so standard PCBA (R26).
- C6 audio tier: RESOLVED (PM round 2, q2): studio, about 120 dB. Reference level 1 Vrms (full scale into 32 ohm, R29), as stated in round 2 C6: floor 1 V x 10^(-120/20) = 1.0 uVrms A-weighted (= 126 dB re the 2 Vrms full scale into 300 ohm). Jacks: headphone out + mic in (3.5 mm); line in/out is R36 (could).
- C7 front strip: RESOLVED by limits (R24). Width now 2 x 18 + 2 x 14 + 2 x 12 + 20 (knob, PM round 2 q3) = 108 mm, plus >= 30 mm gap jacks-to-USB-C (R35) = 138 mm of 440 mm. Fits.

## C9. Cost with studio audio and hot-swap (must R17 <= $80, should R25 <= $60): R17 fits, R25 missed

Bottom-up, 100 units, JLCPCB standard PCBA (estimate until quoted):

| block | $/board |
|---|---|
| bare PCB 4-layer 440 x 155 mm | 5.5 |
| assembly: setup + stencil + engineering ~$150/order, ~2100 joints x $0.0017, extended-part fees ~40 x $3/order, hand-solder THT | 6.5 |
| keyboard: MCU + flash + crystal 1.5, 104 diodes 0.3, 104 hot-swap sockets ~10 | 11.8 |
| USB 3 hub, 6 downstream (4 user + 2 internal), crystal | 5.0 |
| power: PD sink 0.5, 2 PD source + buck 4.0, USB-A buck + BC1.2 1.5, current limits 1.5, L/C 2.0 | 9.5 |
| connectors: 3 x USB-C 24-pin, 1 x USB-C power, 2 x USB-A 3.0, 2 jacks, encoder | 4.4 |
| ESD (R15) | 2.5 |
| general passives | 2.0 |
| studio audio (PM round 2 q2 range $20-30) | 25.0 |
| total | ~72 (range ~67-80 with the audio range and +-$5 on the rest) |

- Cross-check against the round 2 estimate: hi-fi without hot-swap was $50-60; + hot-swap 10 + studio delta (25 - 10 = 15) + knob 1 = $76-86 top-down. Bottom-up gives $72 because the round 2 figure carried ~$5 of rounding up. Central estimate $72-76, $4-8 under R17.
- R17 (<= $80 at 100 units): expected to hold; the architecture BOM confirms it. Risk: galvanic isolation of the audio (I2S isolator + isolated DC-DC, ~$6-8) would reach ~$80-82; it is a fallback only if R22 fails with plain partitioning (C11), and would then come back as a question.
- R25 (should <= $60): missed by ~$12-16. Does not block; the PM chose the studio tier and hot-swap with their stated prices. The optimizer still weighs cost 2.
- At 10 units: PCB ~$18, order fees ~$15/board, parts +~10%: ~$100-105 per board (reported, no limit).

## C10. Bus-powered fallback with studio audio (must R19): RESOLVED, PM round 3 q1 chose option 1 (keyboard + full audio always on, ports share the rest, >= 100 mA each; R21 now must: 1.5 A host >= 250 mA/port, 3 A host >= 500 mA/port)

Host budget without the PD input: USB 3 port 5 V x 900 mA. A bus-powered USB 3 hub must give each port one unit load (150 mA): 4 x 150 = 600 mA, so internal loads <= 300 mA.

Studio audio at the 5 V bus (estimate):
- quiescent: USB audio bridge 40-60, DAC 10-20, ADC 10-15, clocks 10, headphone amp bias 10-20 (with low-noise LDOs and the +-rail generator): 80-125 mA
- signal, full scale 1 Vrms into 32 ohm both channels, class AB on +-4.5 V: Ipk = 1.414 / 32 = 44 mA; P = 2 ch x 2 x 4.5 V x 44 mA / pi = 0.25 W; / 0.85 rail efficiency / 5 V = 60 mA
- audio total 140-185 mA (round 1/2 estimate was 60-100 mA)

Internal total: hub 120-250 + MCU 20-30 + internal port device 0-50 + audio 140-185 = 280-515 mA against 300 mA. Only the best case of every part fits; the middle (~400 mA) misses by ~100 mA. Capping the headphone level in bus mode saves at most 45 mA (signal part), not the quiescent part, so engineering alone cannot guarantee it. The PM promise (round 1 q1, "everything else works") then needs a decision:

1. (recommended, audio first) Keyboard and full-quality audio always powered; the 4 ports share what is left of 900 mA: 900 - (280..515) = 385-620 mA in total, any one device can take it all; >= 100 mA per port when all four are loaded (needs internal <= 500 mA, checked at architecture). On a USB-C host that advertises 1.5 A or 3 A (R21 becomes must), every port gets >= 150 mA. Cost $0. Not USB-spec for a bus-powered hub (no USB-IF logo pursued, R18). A device that needs its full 150 mA may fail on an old 900 mA port when all ports are loaded.
2. Strict 150 mA on every port from any 900 mA port: lowest-power hub and audio parts, headphone level capped at 0.5 Vrms into 32 ohm without the adapter (7.8 mW, ~109 dB SPL on a 100 dB/mW headphone). +$1-3; fits only if datasheets land at the low end (235-420 mA estimate vs 300 mA); if not, back to the PM after architecture.
3. User ports powered only with the adapter or a USB-C host offering >= 1.5 A; on a 900 mA port, keyboard + audio only (ports data-only or off). Cost $0, simplest; breaks "everything else works" on older computers.

## C11. 120 dB audio (1.0 uVrms) next to a 65 W charger and a USB 3 hub (must R10/R22): feasible with isolation, no question

Coupled-noise limit (R22): "EM does not affect audio" = coupled noise raises the floor by <= 0.5 dB. sqrt(Vi^2 + Vc^2) <= 1.0 uV and Vc <= Vi x sqrt(10^0.05 - 1) = 0.349 Vi -> Vi <= 0.944 uV, Vc <= 0.33 uVrms A-weighted (-9.6 dB under the floor).

Isolation needed (go to the architecture and layout):
- Supply rejection: host VBUS audio-band noise up to ~100 mV; 100 mV / 0.33 uV = 110 dB needed. Chain: CLC/ferrite pre-filter (>= 20 dB above 10 kHz) + low-noise LDO (PSRR >= 70 dB at 10 kHz, noise <= 1 uVrms 10 Hz-100 kHz) + amplifier PSRR (>= 60 dB at 20 kHz) = >= 150 dB. Separate LDOs for audio analog, DAC/ADC reference, and the bridge digital supply; the bridge's 1 kHz/8 kHz USB frame current must not share the analog LDO.
- Converters: fixed frequency >= 400 kHz, forced PWM (no burst/PFM at light load, which puts tones in the audio band), shielded inductors.
- Ground: charge currents are 2 x 3 A + 2 x 1.5 A = 9 A. Any shared ground impedance Z with a 1 A audio-band charge-current swing must satisfy Z <= 0.33 uV / 1 A = 0.33 uOhm, which no copper meets (1 oz plane ~0.5 mOhm/sq). So zero charge return current through the audio ground: audio ground region joined to the system ground at one point; headphone sleeve and mic return to the audio region only. Ground-loop current between the PD adapter and the computer stays at the back edge, because both sockets sit together there (PM round 2 q3).
- Key matrix: a 3.3 V, 1 kHz scan edge has a 2.1 V fundamental; into a 1 kOhm audio node the coupling capacitance must be <= 0.33 uV / (2 pi x 1 kHz x 1 kOhm x 2.1 V) = 0.025 fF. Only a solid L2 ground under both plus distance reaches that: no matrix trace inside or within 5 mm of the audio region on any layer.
- Distance: audio analog parts and jacks >= 30 mm from any switching inductor or switch node and from the nearest USB-C charge port (magnetic and radiated coupling that hwflow does not simulate) (R35).
- Galvanic isolation (I2S isolator + isolated DC-DC, ~$6-8) is held back as a fallback if post-layout R22 fails.
Nothing here is physically ruled out on a 4-layer board.

## C12. Bus-mode internal loads vs the per-port floor (must R19 >= 100 mA/port, must R21 >= 250 mA/port at 1.5 A): RESOLVED, PM round 4 q2: floor >= 75 mA per port (900 mA host), >= 225 mA (1.5 A host); internal budget <= 600 mA vs ~585 mA worst after stage 3 (15 mA margin, audio/MCU currents still to confirm)

Architecture estimate (architecture.md 4.1; hub from GL3523S 0.86 W, the rest class estimates +-20 %): internal loads 549 mA typ, 706 mA peak (hub1 172, hub2 100, MCU 25, bridge 68, DAC 18, ADC 20, op-amp quiescent 67, headphone signal 73 at 1 Vrms/32 ohm, misc 6). R19 needs <= 500 mA (900 - 4 x 100).
- 900 mA host: ports share 351 mA typ / 194 mA peak -> 88 / 49 mA per port with all four loaded (R19 >= 100: miss 12 / 51 mA).
- 1.5 A host: 238 / 199 mA per port (R21 >= 250: miss 12 / 51 mA). 3 A host: 500 mA per port, passes.
- With a single-gain 2 Vrms output into 32 ohm the signal part doubles (146 mA): internal 622 / 779 mA.
Options:
1. Lower the bus-mode per-port floor to what is left: >= 50 mA per port on a 900 mA host, >= 190 mA on a 1.5 A host (all four loaded; one device alone gets 190-350 mA). $0. Low-power devices (mice, keys, flash readers at idle) work; a 100 mA device may not with all ports busy.
2. Bus-mode headphone level limit: firmware caps 0 dBFS at -6 dB (0.5 Vrms into 32 ohm, 1 Vrms into 300 ohm) without the PD input: saves ~55 mA typ (signal part), reaches ~495 mA typ, still misses at peak. $0, noise floor unchanged (R10 holds re 1 Vrms), but "full audio always" (PM round 3 q1) is reduced in bus mode.
3. Keep the spec, wait for parts-stage datasheet currents: if hub2 idles low (SS port unused) and the bridge is < 50 mA the typ case fits; the peak case (706 mA) does not. Decide after the parts stage with datasheet numbers.
Recommendation: 3 then 1 (decide on datasheet numbers; the peak case needs option 1 or 2).

Update after stage 3 (hub from the GL3523 datasheet; audio and MCU partly estimates): hubs 128 (no devices) .. 280 (4 SS devices), audio 249 typ / 291 peak (364 at 2 Vrms/32 ohm), MCU 6-12 at 48 MHz, power-group quiescent ~1.4 -> internal ~530 mA typ, ~585 mA worst with all ports busy.
- 900 mA host: (900 - 530) / 4 = 92 mA typ, (900 - 585) / 4 = 79 mA worst per port (R19 needs 100).
- 1.5 A host: (1500 - 530) / 4 = 242 typ, 229 worst (R21 needs 250). 3 A host: 617 / 604, passes.
- Engineering savings taken without a product change: MCU at 48 MHz with sleep between scans, ADC off when the mic is unused (~10 mA), headphone amp off with no plug (13 mA, needs a jack with a detect switch). They do not close the worst case. A cheaper audio tier (C13) closes it. Round 4 q2.

## C14. Bus mode with verified audio/MCU currents (must R19 >= 75 mA/port) and bridge supply: RESOLVED, PM round 5: q1 heavy case >= 60 mA per port, eFuse fault trip <= 1.0 A max; q2 replace XU316 with an available bridge keeping ES9039Q2M/ES9822PRO (back to the PM if none meets the studio spec)

Stage 3 iteration 2 (notes/parts-audio.md, parts-keyboard.md, parts-power.md): audio 209 typ / 291 worst listening, 254 / 346 listening + mic recording; RP2040 + flash 11-13 typ / 18-20 max at 5 V (datasheet Table 637); hubs 128-280; power quiescent 3.4.
- Worst (4 SS devices, recording, loud): 280 + 346 + 20 + 3.4 = 649 mA -> (900 - 649) / 4 = 63 mA per port (R19 needs 75).
- Listening only, worst: 280 + 291 + 20 + 3.4 = 594 -> 76 mA per port (passes).
- eFuse TPS25940 +-8 %: a trip that never cuts 885 mA of legitimate load has a maximum near 1.0 A, so "trip <= 900 mA" and the 75 mA floor cannot both hold with tolerance.
- Fixes: headphone -6 dB while recording in bus mode (-20 mA), +-5.0 V audio pre-rails (-14 mA, dropout risk, engineering), lower-power bridge (-20..25 mA).
Bridge: XU316-1024-QF60B 0 stock at DigiKey, 416 due 2027-01-07 (~12 weeks).

## C15. Available USB audio bridge vs ready firmware: round 6 q1

Stage 3 iteration 3 (notes/parts-audio.md): no off-the-shelf UAC2 bridge with record, >= 24/96, driverless and in stock was found (SA9227, CM6631A, CT7601: no stock or readable datasheet; XU208: 12 weeks). The available option is STM32F723VET6 (on-chip HS PHY, UAC2 192/24 both ways, 3V3 only: no 1.8 V/0.9 V rails, no flash) with project-written TinyUSB firmware (4-8 weeks, async feedback and record risk), not stocked at LCSC (consigned). Bridge group current 44/56 mA vs 79/104 with XU316. Audio BOM ~$59 vs ~$57.6.
XU316 QF60B: 416 due 2027-01-07. Design, review and fab of the first board take weeks, so ordering now largely overlaps.
Fallback: RP2040 full-speed UAC1 24/96, -$9.

## C16. R19 floor at zero margin; chargers < 65 W (stage 5 review): RESOLVED, PM round 7: q1 keep 75/60 mA, eFuse upper tolerance up to ~1.13 A (typical kept near 0.99 A), manual note; q2 65 W label only (notes/manual-text.md), revisit charger detection if post-layout cost leaves >= $0.40 under R17

R19 floor test: internal design-worst 600 mA + 4 x 75 mA = 900 mA = budget. Stage 5 fixes add M657 (back-feed blocking P-FET, ~30 mOhm) -> bus_floor 0.902 A, bus_heavy (657 + 4 x 60 = 897 mA) 0.911 A vs 0.900 A. Overload protection (firmware + hardware shed on IMON/PGOOD) needs a threshold between legitimate load and the TPS25940A minimum trip 0.90 A; IMON gain +-9 % (specified 1-5 A only), offset ~15 mA: uncalibrated the floor that never sheds is ~17 mA per port, calibrated ~55 mA per port (notes/circuit.md "Stage 5 fixes, delta 1"). 3 cycles spent on this measure.
Options: floor 55 mA with calibration; eFuse ILIM raised (max ~1.13 A); no shedding (overload -> brown-out).
S8: CH224D requests 20 V from any adapter and cannot report its rating; design load 62.6 W. A 45 W / 30 W adapter is overloaded. Options: CH224Q (I2C contract read) + I/O expander (no free RP2040 GPIO) + firmware ~$0.40; label only.

## C17. eFuse trip on 1.5 A / 3 A hosts vs R21: round 8 q1

TPS25940A trip window -9 % / +8.5 % (min/max = 0.91 / 1.085 = 0.839). Legit R21 loads: 1.5 A host 4 x 225 + 600 = 1.5 A (bus_1a5 sim 1.553 A at the port limits), 3 A host 2.766 A.
- Today: 1.5 A host 1.511 / 1.668 / 1.818 A (min below the 1.553 A legit load: a min unit current-limits); 3 A host 3.018 / 3.341 / 3.652 A.
- Keep R21: R655 <= 124 k -> 1.5 A host min >= 1.553 A, max ~1.85 A; 3 A host unchanged (max 3.65 A).
- Max <= advertised: min 1.258 A -> (1.258 - 0.600) / 4 = 164 mA per port (heavy (1.258 - 0.650) / 4 = 152); 3 A: min 2.516 A -> 479 mA (heavy 466).

## C13. Cost after stage 3 vs R17 (must <= $80 per board at 100 units): RESOLVED, PM round 4: studio sound kept, R17 raised to <= $125 (q1); hot-swap sockets consigned by the PM (q3); knob upright on top of the strip or facing the user, either valid (q4)

Group parts cost at 100 units (notes/parts-*.md; many prices are estimates +-30 %): hub/USB data $13.8; power/PD $28 (~$18 with integrated PD-source bucks, an engineering cost-down); studio audio $51 ($39 with CM6631A bridge, ES9038Q2M, ADP LDOs); keyboard $11.6-14.8 (104 sockets $7.4-9.3). Plus 4-layer 440 x 152 mm PCB, standard PCBA and passives ~$15-20 (estimate).
- Studio audio (120 dB): 13.8 + 18..28 + 39..51 + 13 + 15..20 = $99-127, ~$110-125 expected.
- Hi-fi audio (110 dB, audio ~$14-18): 13.8 + 18..28 + 14..18 + 13 + 15..20 = $74-93, ~$80-90 expected.
- Gaming audio (90-95 dB codec, ~$3): 13.8 + 18..28 + 3 + 13 + 15..20 = $63-78.
The round 2 estimate ($72) under-counted power (~$15 more) and studio audio (~$15-25 more).
Studio chips (XU316 bridge, ES9039Q2M DAC, ES9822PRO ADC, OPA1622) are not stocked by JLCPCB/LCSC: global sourcing or consignment, +1-3 weeks.
Sourcing: 104 hot-swap sockets per board = 10,400 for 100 boards; LCSC confirms 385 in stock (one listing claims 131k but is mislabeled). Round 4 q3.
R28 shaft facing the user needs a right-angle encoder; none in stock at LCSC (vertical ones are, $0.9-1.6). Round 4 q4.
