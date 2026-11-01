# Dog GPS tracker: sign-off summary

On request, the dog's position over LTE-M (eDRX 81.92 s, response ≤125 s), GPS+Galileo, find-the-dog buzzer, 2xAA lithium cells (alkaline supported down to -20 C), IP67 enclosure (to be designed).

## Board

- 75 x 35 mm, 4 layers, 0.8 mm (JLC04081H-3313), 0.127/0.15 mm rules, 0.3/0.5 mm vias.
- Routed with 0 unrouted, 0 DRC errors, 141 vias. Post-layout SPICE 10/10. Monte Carlo 100 runs, 100 % yield on 6 testbenches.
- Fab: `build/fab/` (gerbers, drill, bom.csv, cpl.csv, positions.csv, schematic.pdf, renders).
- Cost: BOM $47.34 with prices + about $1.4 of unpriced passives (estimate) = about $48.8 per board at 1000 units (limit 55). Mass 63.7 g with cells (limit 65).

## Requirements (hwflow trace: 23/26 verified, 0 failing)

| status | requirements |
|---|---|
| simulated / computed | PWR-1, PWR-2, PWR-3, PWR-4, PWR-5, PWR-6, PWR-7, IF-2, IF-6, IF-7, IF-9 |
| layout check | IF-5 (LTE and GNSS antenna keep-outs), MECH-2 (75x35), MECH-3 (front/back sides) |
| datasheet / analysis / inspection record (notes/evidence/) | IF-1, IF-3, IF-4, IF-8, ENV-1, ENV-3, ENV-4, MECH-1, COST-1 |
| **unverified** | ENV-2 IP67 (must), CERT-2 tool-free battery door (should): both are enclosure items, outside the PCB; CERT-1 CE/RED (could, deferred by the PM) |

Build verdict: FAIL only for "requirements not all verified" (ENV-2 is open). Everything else passes.

## Key decisions

- PM rounds 1-8: inbox/answers.md.
- Delegated to me: 1NCE operator; the conflicts.md assumptions marked "lead".
- Sign-off review (stage 8), 3 musts fixed:
  1. RF grounds: 9 → 20 GND vias. GND taps on the RF parts give a via 0.63-0.65 mm from every LTE shunt and 0.9-1.1 mm from C15 and C24.
  2. U10/U11 ESD arrays: the `_back` footprint was not mirrored, so the protection was not connected to the right pins. They are now on the front with the standard footprint.
  3. Fab files generated.
- Also fixed:
  - C4/C5 supercaps fixed in place, no vias within 1 mm of their pads (0.6-0.85 mm at three pads near U1 and the boost block; vias tented).
  - No vias under the GNSS patch.

## First-article risks

1. The FL1 SAW filter and U8 LC76G inner GND pads are still about 10 mm from a GND via (FL1's pads are too small for a tap). Measure GNSS C/N0 against a reference receiver; if it is low, add a via by hand at the next spin.
2. nRF9151 LGA land pattern: checked against 2 open-hardware drawings, not against the Nordic PS (not downloadable). Reduce paste on the central GND pads 105-113.
3. LTE match (7 elements) and GZ215F supercaps: tune on the VNA with housing, screws and cells fitted. The +/-/BAL pin assignment of the GZ215F comes from a 110 dpi drawing: confirm on the first sample.
4. Decoupling: C1 4.5 mm (limit 4.0), C8 5.8 mm, C11 42.5 mm of copper path (TPL5010, 35 nA: not critical), C13 4.1 mm.
5. cpl.csv lists C4, C5 and J1 on the bottom: they are hand-soldered, so remove those rows (or mark "do not place") when ordering from JLC.
6. About 50 passives have no LCSC code (the parts search was down): JLC matches them from the BOM.
7. Firmware (stage 9 closed: 178 host checks, 0 failed; conflicts.md C13-C16):
   - IF-2 0.52 s of slack, request-aborts-download tested.
   - EPO refreshed 12 h before its 3-day expiry, downloaded at the start of a lost session if needed: worst month 222 uA poll / 170 uA eDRX against 268.
   - Buzzer: finite PWM sequence of 10 s, hardware cap restated to 128 s (TPL5010, 2 periods).
   - FOTA: MCUboot dual slot 440 kB in internal flash + modem delta.
   - Brown-out counter retained across resets.
   - Lead defaults awaiting your confirmation: low-battery warning with about 14 days left (idle below 2.6 V or 3 sagging bursts), 1 FOTA per year.
   - Bring-up: Quectel EPO server credentials, nRF9151 PWM/RAM retention to confirm, RRC set-up time.

## Time (hwflow time)

- 24.1 h total, of which 16.9 h waiting for answers. Work 7.3 h: requirements 0.7, parts 0.7, circuit 0.3, review 0.5, layout 3.5, sign-off 1.2.
- 78 builds, 27 with layout, 3 h 22 min of build time (96 % layout).
