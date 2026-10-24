# Output-protection validation

> Layout-only revision: `verification/circuit-revision-equivalence.json` compares the validated electrical checkpoint with the later connector-region padding and two pad-specific clearance rules. All other source fields, including every component value, pin assignment, model, net and analysis, are identical; the original electrical results remain evidence for those unchanged inputs. This is an equivalence check, not a new electrical simulation run.

Status: **qualified model PASS: 24/24 checks plus independent passive ngspice comparison. Hardware qualification NOT CHECKED.** The evidence is conditional on the component and hardware qualification limits below; it is not proof of physical headphone safety. Placement/routing may use this validated candidate within the stated assumption envelope. Do not connect headphones before the dummy-load acceptance tests.

## Reproduction and source fidelity

From the worktree root, using the pinned offline image:

```sh
docker run --rm --name hwflow-protection-final --network none --userns keep-id \
  -v "$PWD":/workspace -w /workspace hwflow-audio:ca10fa8e \
  timeout 240s python projects/desktop-dac-hd6xx/verification/protection_checks.py --spice
```

Final canonical source SHA-256: `556b89df64223c63651a788429b29bce4f98e7e82559a325971bd939ba9d63c1`. Model SHA-256: `a3cc1610165d57ebb306608e2c7dcb53753948e9e80e075cf4075e91632acff1`. The final run exited 0, used no candidate override, and both hashes were rechecked against the saved files. No protection containers remained running after completion.

`verification/protection-results.json` records the source and model SHA-256, checks, events, corner bounds and limitations. The final run must have `candidate_override: false`. `protection-passive.cir` is generated from the actual YAML resistor/capacitor connections and independently run in ngspice 42. Its waveform file can be regenerated and need not be committed. The circuit changes are recorded in the Git diff of `design.yaml`; temporary integration patches are not alternate circuit sources.

The model stamps the actual unbuffered reference, both sensor networks, thresholds and capacitors into conductance/capacitance matrices. It solves the linear transient with exact zero-order-hold steps and eliminates algebraic nodes. NAND feedback, AND gates and comparator polarities come from actual pin nets; the latch is not a desired-behavior truth table. RC delay wiring and relay/contact/OPA1622 enable paths are asserted against the source. The comparator, Schmitt logic, transistor and relay semantics are qualified engineering models, not vendor transistor-level models. The passive comparison is limited to startup/audio where clamps are inactive; large-fault timing is evaluated before clamp conduction, and the subsequent latch retains the fault.

## Corrected circuit defects

- R244/R259 were 100 kΩ. A logic output's 10 µA power-off leakage could produce 1 V at OPA1622 enable or the relay base. Their 10 kΩ values bound that contribution to 0.1 V. OPA1622 guarantees disable at no more than 0.65 V.
- BAT54WS leakage was incompatible with megohm DC sensing. Its 25 °C limit also could not support a temperature-wide detector claim. BAS116 has a specified 80 nA reverse-leakage maximum at 150 °C. The 100 kΩ input network, 33 µF grounded filters, 49.9 kΩ/10 kΩ reference-offset attenuators and reference-derived window now include leakage and loading explicitly. The powered negative-fault comparator inputs remain positive; a diode's negative clamp voltage is not treated as a valid comparator common-mode voltage.
- LM339B's full-temperature offset specification is given from 5 V supply. U203/C245 now use VPOS, with a 25 V-rated local decoupler. Comparison is qualified only while the upstream AUDIO_READY contract implies VPOS is valid and at least 5 V.
- The fast-arm corner of the original LVC buffer plus timing-diode leakage could arm before the loaded detector settled. U206 is now SN74AUP1G17DBVR, D200 is BAS116, R256 is 120 kΩ, and C243 is 100 µF. R251/R253 are 6.81 kΩ/2.87 kΩ, providing margin for normal low-frequency audio while retaining a sub-1 V DC trip window.
- U204's original DCTR order code specified a 0.65 mm-pitch package inconsistent with the 0.5 mm VSSOP footprint. DCUR matches the footprint.
- BAS116 uses physical pin 1=A, 2=NC, 3=K. Its local symbol and SOT-23 footprint mapping were verified. D200's cathode is AUDIO_READY; its anode is DC_ARM_DISCHARGE. D203/D205 have anodes at ground. The existing KiCad `74AUP1G17` symbol supplies the same U206 pin function mapping.

## Acceptance envelope and measurements

The product's normal stimulus is 4 Vrms into 300 Ω: 5.657 V peak at 20 Hz, including arbitrary independent stereo phases and abrupt starts. The engineering checks require at least ±150 mV DC clearance, detection of sustained ±1 V on either channel, nominal command-off within 1.5 s for 1 V and 0.2 s for 9 V, and selected latency corners within 2 s/0.2 s. These are design criteria, not physiological or headphone-damage thresholds.

The DC interval calculation covers all 14 independent ±1% resistor endpoints, 3.0/3.6 V reference supply endpoints, independent 0–50 nA currents at all eight comparator inputs, ±5.5 mV comparator offsets, independent ±580 nA per filtered node, and 0–100 nA reference-cap leakage. The 580 nA envelope includes 80 nA diode leakage and a conservative 500 nA filter-cap allowance.

The audio calculation covers 131,072 combinations of those independent resistor endpoints and eight 50%/120% effective-capacitance corners. Complex sin/cos superposition bounds every left/right starting phase. A second-derivative interpolation bound covers the time grid; modal bounds cover the infinite tail. Startup adds the worst signed settling residual on each comparator, including bounded bias/leakage, at the fastest arm time. It conservatively allows audio to start at any time, including immediately before arming. Resistor endpoint sampling and capacitor endpoints do not constitute formal interval proof for every continuous interior component value.

Final canonical-source measurements:

| Measurement | Result |
| --- | --- |
| Worst negative DC threshold interval | −0.9114 to −0.2433 V at amplifier output |
| Worst positive DC threshold interval | +0.2571 to +0.8354 V |
| Minimum all-phase audio margin | 2.1495 mV at comparator |
| Minimum simultaneous startup/audio margin | 2.1312 mV at comparator |
| Conservative earliest arm used by startup bound | 4.6189 s after rail step |
| Model midpoint startup/contact close | 17.934 s |
| Modeled early/late startup contact close | 6.307 / 36.9995 s |
| 64 selected latency corners: ±1 V / ±9 V | 1.6147 s / 99.10 ms to detector trip |
| Nominal +1 V fault, command off / assumed contacts open | 779 / 799.5 ms |
| AUDIO_READY drop, command off / assumed contacts open | within 0.5 / 20.5 ms |
| Fault clears at 23 s, latch reset after READY falls at 26 s | latch retained until DC_ARM falls at 26.124 s |
| READY returns at 29 s; contacts reclose | 44.869 s |
| Exact passive model versus ngspice maximum discrepancy | 0.436 mV |

Startup is intentionally slow: approximately 18 seconds at the modeled midpoint and 37 seconds at the slow selected corner. U206 has no vendor typical threshold in the cited table; the midpoint is a modeling choice. Reducing that wait needs a new timing design and renewed startup verification. The 64 latency cases are selected opposite uniform-resistor endpoints with maximum C, leakage and offset; they do not exhaustively prove worst transient latency at every independently mixed resistor/current corner.

## Component limits and unresolved hardware gates

The intended desktop environment is 0–50 °C ambient, with comparator/logic/filter local temperatures no greater than 85 °C and the relay within its 70 °C operating ambient limit. Verify this thermally. BAS116's leakage allowance uses its 150 °C limit, without assuming its 25 °C forward-voltage limit is temperature independent.

1. **Effective capacitance and leakage.** C241/C242 are TDK C3225X5R1A336M200AC, 33 µF ±20%, 10 V, X5R, 1210, maximum height 2.2 mm. Its specified 3 MΩ insulation resistance supports 0.5 µA at 1.5 V; that voltage envelope covers normal operation and trip onset, not arbitrary post-trip rail faults. Demonstrate at least 16.5 µF effective C over bias, temperature, tolerance and aging. C243 is Murata GRM32ER61A107ME20L, 100 µF, 10 V, 1210; demonstrate at least 50 µF effective and no more than 1 µA leakage. Also qualify C240/C244 at ≥50% nominal, reference-cap leakage ≤0.1 µA and C244 leakage ≤1 µA. Those effective-C floors are requirements, not manufacturer guarantees established by this work.
2. **Schmitt thresholds and discharge.** TI guarantees AUP VT+ 1.88–2.29 V and VT− 0.88–1.24 V at 3 V, with input leakage ≤0.5 µA through 85 °C. The cross-supply acceptance envelope is VT+≥1.88 V over 3.0–3.6 V and VT+≤2.29×VCC/3; those intermediate-supply bounds require characterization. The latest timing model scales thresholds, while the earliest analytical bound uses the absolute 1.88 V floor. U209 remains LVC: its 5 µA input current and D202's 30 µA full-temperature reverse leakage are independent of U206/D200. Delay discharge uses an approximate 0.6 V diode drop: measure hot/cold reset and rearm, including short READY interruptions, rather than treating its simulated milliseconds as guarantees.
3. **Output drive and power sequencing.** AUP's ≥VCC−0.1 V / ≤0.1 V limits at 20 µA cover the two LVC inputs driven by DC_ARM (up to 10 µA total input leakage). It operates at 0.8–3.6 V and has ≤0.6 µA Ioff. All LVC logic requires ≥1.65 V. At exactly zero supply the external 10 kΩ output pulldowns bound leakage; behavior while 0<VCC<1.65 V remains unqualified. The tests deassert AUDIO_READY before rail decay. Validate the actual upstream supervisor/mute timing during slow ramps, fast removal and brownouts; no ideal logic operation on an unpowered rail is assumed as a hardware guarantee.
4. **Clamps and relay.** Qualify the BAS116 negative clamp at no worse than −1.5 V over the operating envelope and check comparator pins during ±rail faults and unpowered faults. Omron lists 237 Ω/21.1 mA for the 5 V coil, ±10% coil resistance, and 3 ms bare-relay operate/release times. The SS14 flyback diode changes release behavior. The model's 20 ms release allowance is a bench acceptance limit, not a manufacturer maximum with that diode. Measure coil decay, contact separation/bounce, amplifier shutdown residue and headphone-jack DC energy into dummy loads before connecting headphones. A welded/stuck contact, component open/short, ESD event and arbitrary supply-fault ordering are outside this model.

Primary sources: [TI LM339B](https://www.ti.com/lit/ds/symlink/lm339b.pdf), [TI OPA1622](https://www.ti.com/lit/ds/symlink/opa1622.pdf), [TI SN74AUP1G17 electrical limits](https://www.ti.com/document-viewer/SN74AUP1G17/datasheet/specifications), [TI AUP pin functions](https://www.ti.com/document-viewer/SN74AUP1G17/datasheet/pin-configuration-and-functions), [TI SN74LVC1G17](https://www.ti.com/lit/ds/symlink/sn74lvc1g17.pdf), [TI SN74LVC1G08](https://www.ti.com/lit/ds/symlink/sn74lvc1g08.pdf), [TI SN74LVC2G00](https://www.ti.com/lit/ds/symlink/sn74lvc2g00.pdf), [Nexperia BAS116](https://assets.nexperia.com/documents/data-sheet/BAS116.pdf), [Nexperia BAS316](https://assets.nexperia.com/documents/data-sheet/BAS316.pdf), [Omron G6K](https://omronfs.omron.com/en_US/ecb/products/pdf/en-g6k.pdf), [TDK exact 33 µF part](https://product.tdk.com/en/search/capacitor/ceramic/mlcc/info?part_no=C3225X5R1A336M200AC), [Murata component list](https://www.murata.com/-/media/webrenewal/tool/library/common-pdf/dynamic-model/component-list-d-mlcc-2504.ashx?cvid=20250523010405000000&la=en).
