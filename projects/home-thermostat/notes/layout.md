# Layout (stage 7)

## Representation: one 130 x 40 mm panel, two boards

hwflow builds one PCB from design.yaml, so the two boards (rear = mains, front = SELV/logic, linked by J4A/J4B) are drawn as one 4-layer panel of 130 x 40 mm:

| Region | Panel x (mm) | Content |
|---|---|---|
| rear board | 0 to 62 | groups rear_*: relays K1/K2, terminals J1/J2/J3/J6, varistors RV1/RV2, ESD D2/D3, J4A. Drawn mirrored: panel x = 62 - board x. |
| gap | 62 to 68 | routed slot (SLOT1, Edge.Cuts) between the boards, 6 mm; tabs at the top (y < 16.5) and bottom (y > 38) hold the panel |
| front board | 68 to 130 | groups front_*: ESP32-C3 U1, power stage, CS1238, BT1, UI, J4B |

Each board is 62 x 40 mm, inside the 503 box budget (<= 62 x 40 mm). Stack depth 28.2 mm (1.0 + 1.6 + 10.0 + 1.0 + 1.6 + 13.0).

Rear parts use `*_Mirror` footprints (X-mirrored, pads on B.Cu, B.CrtYd) because the rear board is built as seen from behind. J4A is a mirrored 2x5 socket at (56.5, 3), J4B a back-side header at (73.5, 3): J4B is mirrored at the J4A XY. Bridge tracks over the top tab join J4A and J4B pins (PROBE1/2, COIL1/2 and others), so hwflow sees every cross-board net as connected.

### Fabrication split
1. Route the slot and cut the tabs (V-score or mouse bites at the bottom tab, top tab removed).
2. Delete the bridge tracks over the top tab (about 12-17 mm long, PROBE1/2, COILx): they model the J4 mating only. They appear in post-layout crosstalk/parasitics as panel artifacts, not as real traces.
3. Fabricate the rear board from the region x 0-62 mirrored about the vertical axis with F/B swapped (all rear parts sit on B.Cu in the panel, so they land on the top side of the real board).
4. Front board: region x 68-130 as drawn, parts on both sides (U4, passives, U2 on B.Cu).

## Mains isolation (R8)
- AC_* nets: `clearance_mm: 2.5` (hwflow applies a net clearance against every other net, there are no class pairs). The KF301 project footprint has 2.3 mm pads so the pad gap is 2.7 mm at 5 mm pitch.
- 4.0 mm clearance / 6 mm creepage to SELV is enforced by zoning: mains pads/tracks only in the rear mains zone (panel y >= 18.9), all SELV copper >= 6 mm away by placement, KO_MAINS (no copper pour on any layer over the zone, panel x 0-55.7, y 13-40), and a custom-rule DRC (`.kicad_dru`, 4.0 mm AC_* to non-AC_*) run on a copy of the board outside build/.
- The panel slot (6 mm) separates mains from the front board; no copper crosses it except the J4 bridge (SELV).
- Mains-to-mains 2.5 mm: the KiCad Default clearance is 0.15, the rule 2.5 mm is applied by the net clearance and checked by the same DRC run.
- Relay contact tracks: 0.8 mm (current_a 2 on AC_COM/OUT, IPC-2221 10 C rise). AC_NC1/2 are intentionally dead-end (`allow_single`).

- Relay floorplan (stage 7 rework, R8): K1 (58.5, 19.6) and K2 (26.6, 19.6) both rot 0, side by side, coil columns A1/A2 toward J4A (K2 was rot 180 at x 10 with the coil at the far board edge, so COIL2_B crossed the mains zone 2.6-3.2 mm from AC copper). Contacts 11/14 in the bottom row (y 27.1), AC_NC pads (dead end) in the top row (y 19.6: K2 x 9.1, K1 x 41). R20 (89, 34.9) sits next to R18/C23 on the front board.
- KO_BAND (tracks and vias not allowed, all layers) now has three zones: y 12.2-18.5 over each NC pad (K2 panel x 1.7-16.5, K1 x 33.6-48.4, 6 mm to the pad edge) and x 49.2-55.9, y 20.5-31 right of K1 pad 11 (keeps COIL1_A 6 mm from it). Pads stay allowed.
- Router sensitivity: a 0.3-0.7 mm shift of K1 or K2 flips the COIL2 routing into the mains zone (B.Cu y = 31); K1 x 58.5 with K2 x 26.6-31 keeps COIL2_A/B north. Do not move K1/K2 without re-checking COIL2 (width_mm 0.3 passes did not help).
- Custom-rule check (outside build/, `.scratch/drc/home_thermostat.kicad_dru`: `A.NetName == 'AC_*' && B.NetName != 'AC_*'` clearance 4.0 mm, run with `kicad-cli pcb drc`). Old result (K2 at x 10, rot 180): COIL2_B 2.56 mm from AC copper. New floorplan: the kicad-cli run was NOT possible in the last session (permission refused), so the numbers come from the routed board coordinates (pads r 1.2, tracks 0.8 / 0.25 mm): no SELV track or via in the mains zone; minimum edge distance 6.18 mm (K2 A1 pad to AC_COM1 In1 diagonal), then 7.2 mm (PROBE1 to K1 NC pad), 8.7 mm (COIL1_A to K1 pad 11). Clearance 4.0 mm met, creepage 6.18 mm (>= 6.0, straight path, no slot needed on the rear board). Rear mains to front board: 18.3 mm across the 6 mm slot. TODO: re-run the .kicad_dru check on a copy to confirm. R8 stays UNVERIFIED (evidence_pending updated).

### 3D stack check (R8 4.0 mm in the stacked assembly)
Mapping (checked on J4A (56.5, 3) / J4B (73.5, 3): board-local x 5.5 on both boards, same y): a rear panel point x_r lies under front panel x_f = 130 - x_r. Stack: rear board, 10 mm tall parts, 1.0 mm air, front board (11 mm board gap). Heights are catalogue values (KF301 10 mm, PE014 10 mm, MOV disc about 10 mm incl. leads, assumed). Front copper is on all 4 layers and the B.Cu face is the one 1 mm above the parts.

| Rear part | Height | Gap to front board | Front panel footprint (x_f, y) | Nearest front copper in plan | 3D distance |
|---|---|---|---|---|---|
| J1 screw terminal | 10 mm | 1.0 mm | x 81-92, y 29.8-40 | 4.5 mm (L1 pad, R17 pad); keep-out edge 4.0 | >= 4.12 mm |
| J3 screw terminal | 10 mm | 1.0 mm | x 105-116, y 29.8-40 | 4.5 mm (U2 B2 stub); keep-out edge 4.0 | >= 4.12 mm |
| RV1, RV2 (DNP, do not fit in this stack) | about 10 mm | 1.0 mm | x 93-124, y 33-38 | not fitted | n/a |
| K1 relay (sealed housing) | 10 mm | 1.0 mm | x 70-90, y 18.1-28.6 | 0 (front parts and pour above it) | 1.0 mm |
| K2 relay (sealed housing) | 10 mm | 1.0 mm | x 102-122, y 18.1-28.6 | 0 (U2, C4, R1, U1 module area above it) | 1.0 mm |

- Fix: footprint KO_STACK (lib Keepout_Front_MainsStack) = two all-layer keep-outs (tracks, vias, pads, pour, footprints) on the front board at x 77-96 and x 101-120, y 25.8-40: the J1 and J3 courtyards plus 4.0 mm in plan. Front x 96-101 lies 4.0 mm or more from both terminals and holds C5. Pads, tracks, vias and the GND pour (all layers) stop at the keep-out edge, so the closest copper is 4.0 mm in plan from the terminals. Plan distance 4.0 mm with 1.0 mm height gives sqrt(4.0^2 + 1.0^2) = 4.12 mm >= 4.0 mm.
- Moved out of the keep-out: M3, C21, R19, Q1, R18, R20, C23 (to x 75-83, y 16.8-24.3 above the BT1 pads; M3 and C22 below them at x 70-73, y 36), the sense group M4, M1, R17, R3, R4 (strip x 70-76, y 26-39 beside the BT1 pads), L1 (up 1.0 mm, left 1.85 mm), C5 (next to U2 B2 in the x 96-101 pocket), TP2 (to x 100.8, y 19.7: it keeps the B.Cu GND pour near U2 connected). The TestPoint courtyard was reduced from r 1.0 to 0.7 mm (pad r 0.5).
- The relays do NOT meet 4.0 mm in 3D: their housing top is 1.0 mm under front copper, which covers about all of the front board. They have no exposed live metal (sealed housing, contact-coil 4 kV, basic insulation accepted by PM round 3 q1, R8 evidence). Not fixable in 62 x 40 mm and 4 layers (the keep-out would cover the U2/U1 area). Open for the safety review: insulating sheet between the boards over K1/K2 or a taller gap (needs the product person).

## Antenna and EMC (R9/R10)
- KO_ANT: all-layer keep-out (no tracks, vias, pads, pour) at the U1 antenna, front board local x 48.6-62, y 0-3.9 (panel x 115.7-130), plus its projection on the rear board (panel x 0-13.4, y 0-10).
- Mains copper is >= 15 mm in plan from the antenna rectangle; J1 and relays sit at panel y >= 18.9.
- CS1238 and its inputs are on the front board, far from the boost inductor (U2 side) and relay coils (rear board); SW (switching node) is 0.4 mm class `switching`.

## Router and pour handling
- 4 layers (F, In1 GND, In2 GND, B): 2 layers could not route and left 8 isolated GND pour islands. GND is excluded from routing (`gnd_pour`), so GND connectivity is the pour and the inner planes.
- A B.Cu pour piece inside the BT1/VBAT track loop (M3.2, C21.2 GND) is joined to the inner plane by one thermal via (C21 pad 2).
- DSBGA-6 U2 (0.4 mm pitch) uses the `DSBGA-6_Back` project footprint with copper-only fan-out stubs (same pad number, no mask opening) on B.Cu.
- RLY1_IN1 has `width_mm: 0.15`: at 0.25 the escape from U4 pad 6 (under the ESP32 module) is boxed in.
- D_SOD-523_Mirror pad numbers must be quoted (`"1"`): unquoted numbers made the router ignore D2/D3.

## Assumptions to verify in 3D
- Terminal wire entry is +y at rotation 0; terminal bodies overhang the bottom edge by about 0.6 mm.
- BT1 (Keystone 2469, wire leads) overhangs the front board edge by 1.95 mm; the holder is glued on the front face, leads soldered to the pads.
- CS1238 pin map confirmed against the Chipsea datasheet Rev.1.1 (notes/datasheets/cs1238.md).
