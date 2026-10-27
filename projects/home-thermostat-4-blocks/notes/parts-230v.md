# Parts: 230 V switching group (K1, K2, U4, U5, J1, F1)

Tool limits this session: `pdftotext`/`curl` were blocked by the sandbox and WebFetch cannot parse PDFs, so the Panasonic DW datasheet PDF was NOT read. Relay numbers come from Panasonic product pages and distributor pages (see Sources). Items marked **(unverified)** must be checked against the PDF (ds_dw_yl_relay_en.pdf) before layout.

## Table

| Ref | Role | Part / MPN | LCSC | Price qty 10 (USD) | KiCad symbol | KiCad footprint | Height | Key numbers |
|---|---|---|---|---|---|---|---|---|
| K1, K2 | Latching relay, 1 coil, 1 Form A | Panasonic ADW1103HLW | none (not in the hwflow index; LCSC search not confirmed) | 4.55 Mouser (450 pcs), 5.11 Digi-Key (226 pcs), 4.06 Newark; consigned / hand-solder | `Relay:Relay_SPST_Latching_1coil` (generic, EN50005; its pin numbers are alphanumeric, `hwflow lib show` crashes on it, read them from the .kicad_sym) | none. Project footprint needed (see below) | 15.8 mm (HL low profile; footprint 24 x 10 mm) | coil 3 V, 45 ohm +-10 %, 66.7 mA, 200 mW; set/reset voltage max 80 % of rated = 2.4 V (20 C); set/reset time max 15 ms; contact 16 A 277 VAC resistive, AgSnO2, 100 mohm max; dielectric coil-contact 5000 Vrms 1 min, contact-contact 1000 Vrms; surge 12 kV; insulation resistance >= 1000 Mohm; life >= 1e6 mech; -40..+70 C at 16 A, -40..+85 C at <= 8 A; 7.5-8 g. Creepage/clearance coil-contact >= 6 mm **(unverified, search summary)** |
| U4, U5 | H-bridge, 1 per relay | TI DRV8837DSGR | C39159 | 0.26 (LCSC page 0.2575 at qty 5+; another listing 0.147; use 0.26) | `Driver_Motor:DRV8837` | `Package_SON:WSON-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm` (pads 1-8 + EP 9, matches the symbol) | ~0.8 mm | VCC 1.8-7 V, VM 0-11 V; Rds(on) HS+LS 280 mohm; 1.8 A (1.9 A peak); sleep current max 120 nA (typ 30 nA); -40..+85 C; PSpice model SLVM958A.ZIP on ti.com (vendor) |
| J1 | 230 V terminal, 3 poles | Degson DG2EDGRC-5.0-03P-14-00A or equivalent 5.0 mm pluggable right-angle block | not confirmed (lookup failed) | ~0.5-0.8 est. | `Connector:Screw_Terminal_01x03` (pins 1-3) | `TerminalBlock:TerminalBlock_Altech_AK300-3_P5.00mm` (pads 1-3, 5.00 mm pitch, 1.98x3.96) as placeholder; check the real pad drawing | <= 15.8 target | 5.0 mm pitch, 300 V 15 A (search summary, unverified), wire 0.5-1.5 mm2 typical. IP2X needs a shrouded pluggable header + plug; a plain open KF301/KF128 block is NOT finger-safe |
| F1 | Series fuse on L-in | Walter Elec 2010T3.15A250V (T 3.15 A time-lag, 250 VAC, 100-130 A breaking, I2t 98 A2s at 10 In) | C354904 | 0.035 | `Device:Fuse` | `thermo:Fuse_Littelfuse_395Series_back` (KiCad `Fuse:Fuse_Littelfuse_395Series` = TE5, mirrored by lib/mkback.py; pads 1-2 at 5.08 mm, drill 1.0) | 8.1 | body 8.2 x 4.0 x 8.1 mm, leads 0.6 mm (datasheet 6-1); stock 281k; UL/cUL, VDE, CCC, JET marks for 1-6.3 A; see "F1 footprint and notch fit (2026-10-22)" |
| RV1, RV2 | Varistor across each output (optional, DNP) | 275 VAC S07K275-class | not searched | ~0.05 | `Device:Varistor` | `Varistor:RV_Disc_D12mm_W4.7mm_P7.5mm` (too big? use D7 mm if the library has it) | n/a | fit footprint, leave DNP |

Subtotal for the group (qty 10 prices): K1 + K2 = 9.10, U4 + U5 = 0.52, J1 ~0.7 (est.), F1 = 0.04 -> about **10.4 USD**. With Mouser 450-pcs relay price. Relays are 87 % of it and use most of the C5/R30 headroom.

## Verification against requirements

- R8: 16 A 277 VAC resistive, TV/tungsten 1200 W at 240 VAC: >= 3 A and >= 5 A inrush OK. Passes.
- R11: coil-contact 5000 Vrms, 12 kV surge, creepage >= 6 mm (unverified): passes if the PDF confirms 6 mm. "Reinforced" is not stated in text I could read; the numbers (>= 4 kVrms and >= 6 mm) meet the alternative branch of R11.
- R2: 15.8 mm HL type, equals the stack allowance. Zero margin; J1 must be <= 15.8.
- R9: coil 3.3 V / 45 ohm x 30 ms = 7.26 mJ (bridge drop 280 mohm x 73 mA = 20 mV, negligible). Worst case 3.4 V, coil 40.5 ohm, 30 ms = 8.56 mJ <= 9 mJ. OK. Margin: min rail 3.2 V less 0.02 V = 3.18 V = 133 % of the 2.4 V max set voltage (110 % target needs 2.64 V). Hot coil: 50 C coil 50.3 ohm (+ 10 % tol = 55 ohm), set voltage rises ~ +0.4 %/K, so about 2.7 V at 50 C; 3.18 V is still 118 % of that. At the R23 cut-off (loaded VBAT 2.0 V) the boost rail is still 3.3 V, so OK. Note: 3.3-3.4 V is 110-113 % of the 3 V rated coil voltage; the datasheet continuous max coil voltage is **unverified**, for 30 ms pulses it is acceptable.
- R12: terminal only meets IP2X in a shrouded pluggable form; choose the pluggable header (fixed right-angle header with a finger-safe plug) and confirm with its datasheet.
- R6: DRV8837 sleep 120 nA max each x 2 = 0.24 uA max (typ 0.06 uA). Architecture assumed 0.1 uA each, so +0.04 uA at worst. Negligible. nSLEEP is driven by SENS_EN.
- R29: relay 0..+70 C at 16 A, +85 C at <= 8 A, and DRV8837 -40..+85 C. OK.

## Relay footprint: missing in KiCad

KiCad has no Panasonic DW/ADW1 footprint (`lib fp` found only Schrack RYII, Omron, Finder, etc.). A project footprint must be made from the datasheet "PC board pattern" drawing (ds_dw_yl_relay_en.pdf, dimensions page). Facts I could confirm: body 24 x 10 mm, height 15.8 mm, through-hole pins. **Pitch, hole size and pin positions were not retrievable: (unverified), read them from the PDF** before building the footprint. Generic symbol `Relay:Relay_SPST_Latching_1coil` can be used (set/reset by coil polarity); pin mapping coil A1/A2 and contact 11/14 must be checked against the datasheet table (the coil pins sit at one end and the contact pins at the other end, which is what gives the 6 mm barrier).

## Fuse, snubber, varistor

- Loads: thermal valve heads are PTC/wax heaters, about 1-2 W each at 230 V (about 10 mA hold); cold inrush about 1 A for under 1 s (requirement says up to 1.5 A each). Contact 16 A with 100 mohm and AgSnO2: no contact-wear problem and no snubber needed for a resistive load.
- Fuse: the upstream heating breaker is typically 10-16 A and does not protect a 1 mm x 35 um trace, which fuses at well above 10 A, or the relay. A board-level fuse costs 0.035 USD and 8.5 x 8.5 mm. Recommendation for the prototype: fit F1 = T 3.15 A 250 V in L-in (3 A worst case with both heads at 1.5 A; slow-blow tolerates the 1 A cold inrush). It is a THT part on the 230 V side; its body size sets the placement, not the height (about 8.5 mm). 2 A would nuisance-trip at 2 x 1.5 A = 3 A, so it is not recommended.
- Varistor/snubber: heads are not inductive. A 275 VAC varistor adds about 0.05 USD plus 12 mm of board; recommended for prototype: footprint only, not fitted, unless valve heads with a coil are used. Total fuse + optional parts cost: 0.04 USD (varistors not fitted).

## Alternatives

| Block | Alternative | Why not chosen |
|---|---|---|
| Relay | Panasonic ADW1103W / ADW1103HT (standard height 18.8 mm) | taller, fails R2 |
| Relay | TE Schrack PE014F03 (3 V, 41 ohm, 5 A, 20 x 10 x 10 mm) | creepage 4 mm / clearance 3.2 mm, fails R11; "not currently available" |
| Relay | Cheap LCSC 250 VAC latching relays (e.g. Hongfa HFE60 / Songle SLA) | not found in the hwflow index; coil-contact creepage and 3 V latching versions unconfirmed; usually 5 V/12 V |
| H-bridge | DRV8838DSGR (C86667), PH/EN input | same chip family, PH/EN control instead of IN1/IN2; same cost; DRV8837 chosen since IN1/IN2 allows coast/brake and pulse in either direction with 2 GPIOs |
| H-bridge | DRV8837CDSGR (C191000) | 1 A version, same footprint, similar price; also usable |
| Terminal | Phoenix PT 1.5/3-PH-5.0 (10 A, 400 V) | better brand, ~2 USD, not at LCSC |
| Terminal | Same Sky TBP01P1-508-03BE (5.08 mm) | pitch 5.08 mm, also fine for R12 |

## Architecture numbers that changed

- Relay price: 4.55 USD (Mouser) each, 9.1 USD for two, not "4-6 USD" total; cost is consigned and hand-soldered (not JLCPCB-assembled), plus extra shipping.
- H-bridge sleep current: 120 nA max, typ 30 nA (architecture assumed 0.1 uA, i.e. 0.2 uA for two; worst case now 0.24 uA).
- Pulse energy 7.26 mJ nominal / 8.56 mJ worst case (architecture had 7.2 / 8.5), unchanged within rounding.
- J1: no longer "no on-board fuse": add F1 T 3.15 A. Fuse body is 8.2 x 4.0 x 8.1 mm THT (TE5 outline; the earlier "8.5 x 8.5" was length x height), needs space on the HV side at x <= 20.
- DRV8837 is an extended-library part at JLCPCB (probable extra setup fee); LCSC price differs between listings (0.15 to 0.26 USD).

## Sources

- https://industry.panasonic.com/global/en/products/control/relay/power/number/adw1103hlw
- https://na.industrial.panasonic.com/products/relays-contactors/mechanical-power-relays/lineup/general/series/1818/model/1844 (dielectric, surge)
- Datasheet PDF (not read): https://industry.panasonic.eu/storage/custom-upload/Components/Relays/Power/ds_dw_yl_relay_en.pdf
- https://www.ti.com/product/DRV8837 (PSpice SLVM958A.ZIP), https://www.lcsc.com/product-detail/C39159.html
- Prices: search summaries from Mouser, Digi-Key, Newark (web search, 2026-10-20)

## Cheaper relay search

Limits: `hwflow parts` has no relay category; Bash network (curl) was blocked; WebFetch cannot read PDFs or LCSC pages (404/403). Prices and stock at LCSC were NOT retrievable. Everything below is from search summaries (JLCPCB part pages, LCSC datasheet titles) and must be checked against the PDF.

| Candidate | LCSC | Coil | Set V | Fit to 3.3 V H-bridge | Verdict |
|---|---|---|---|---|---|
| Hongfa HF115F-L, HF3F-L (also HF32FV-L, HF46F-L) | C190453 (HF115F-LS-12), C190594 (HF3F-L/5) etc. | lowest voltage found is 5 V, 62-63 ohm | about 75 % of 5 V = 3.75 V (unverified) | needs > 3.3 V: set voltage above the rail | Rejected, no 3 V coil found in any listing |
| Fanhar FH42L-1AT-L2-DC3V/16A (dual coil) | C467034 | 2 x 18 ohm, 500 mW | <= 2.4 V | 3.3 V / 18 ohm x 30 ms = 18 mJ | Rejected, R9 (9 mJ) fails by 2x |
| Fanhar FH44L-1AT-L2-DC3V (dual coil, 20.2 mm long) | C498276 | 2 x 18 ohm, 400 mW | not found | same energy problem | Rejected |
| Fanhar FH42L-1AT-L1-DC3V (single coil) | not found in search (L1 DC5V is C5378120, so a 3 V L1 listing may exist; check lcsc.com) | 36 ohm, 83.3 mA, 250 mW | <= 2.4 V, reset <= 2.4 V | 3.3 V -> 3.28 V at coil = 137 % of 2.4 V. Energy 3.28^2/36 x 30 ms = 8.96 mJ nominal; at 3.4 V and 32.4 ohm (-10 %) = 10.7 mJ | Best candidate, R9 marginal: nominal 8.96 mJ passes 9 mJ by 0.4 %, worst case fails. Needs a PM decision or a 27 ms pulse (operate time 15 ms max -> 1.8x) |
| TE V23079B1208B301 (2-form-C, dual coil) | C1560946, 10.17 USD | n/a | n/a | 2 A only, 10 USD | Rejected, fails R8 and cost |
| Panasonic DE1A-L-3V, Omron G5RL-U 3 V | not at LCSC (search) | n/a | n/a | n/a | Not cheaper than ADW1103HLW |

Fanhar FH42L facts (search summaries of the Fanhar/LCSC datasheet, unverified): AgSnO2, 16 A 250 VAC (277 VAC max, 20 A max), body 24.0 x 10.0 x 15.8 mm (same as Panasonic DW: footprint and R2 identical), 5 pins, coil-contact breakdown 4000 VAC (one JLCPCB summary) or 5 kV (Fanhar text), operate/release 15 ms, -40..+85 C. Creepage coil-contact: not found. Pin pitch: not found. No KiCad footprint (same gap as the Panasonic DW; `Relay:Relay_SPST_Latching_1coil` as a symbol).

Recommendation: no Hongfa part works from 3.3 V. The only plausible cheaper part is Fanhar FH42L-1AT-L1-DC3V, a Panasonic DW clone, but its price (not found, probably about 1.5-2.5 USD) and stock are unverified, and R9 holds only at nominal rail with a 30 ms pulse (8.96 mJ). Next step for the product person or parent agent: open lcsc.com, search "FH42L-1AT-L1-DC3V", read price/stock/creepage from the PDF, and decide whether R9 may be relaxed to about 10.7 mJ worst case (or the pulse shortened to 1.8x the operate time). If not, keep ADW1103HLW and find savings elsewhere.

## J1 shrouded terminal at LCSC

Not found with an LCSC number. Candidates (search only, unverified): Degson 2EDGRC-5.08-03P-14-00AH (3-way, 5.08 mm, right angle, 15 A 300 V UL, PA66, header + plug, IP2X-capable with the shrouded header), Degson 2EDGVC-5.08-03P-14-00AH (vertical, taller), Kaifeng KF2EDG-5.08-3P (DigiKey 27470750). Height of the right-angle pair is typically 10-11 mm, below 15.8 mm. Check LCSC for "2EDGRC-5.08-03P" or "KF2EDGR-5.08-3P"; price expected 0.5-1 USD for header + plug.

## F1 footprint and notch fit (2026-10-22)

Problem: conflicts.md "F1 230 V pad against the left screw notch". F1 used the placeholder footprint
`TerminalBlock_bornier-2_P5.08mm_back` (courtyard 10.5 x 8.0 mm, 3 mm pads); pad 2 (AC_L) was 3.12 mm (pad box,
`hwflow floorplan`) from the notch_l edge against 6.0 mm (R10, architecture 6.3).

Datasheet (Walter "2010 series (Time-Lag)", MS-DE-031-016 rev 23, read 2026-10-22, https://www.lcsc.com/datasheet/C354904.pdf):
time-lag (2.1 In > 2 min, 2.75 In 0.4-10 s, 10 In 20-150 ms); 3.15 A: cold resistance 18.25 mohm, max drop 100 mV,
I2t 98 A2s at 10 In; breaking capacity 100 A / 250 VAC (UL, PSE, SEMKO), 130 A / 250 VAC (VDE 3.15-6.3 A, CCC, KC);
approval marks for 1-6.3 A: cUL-us, VDE, CCC, PSE/JET; body PA66 UL94-V0, 8.2 +-0.3 x 4.0 +-0.2 x 8.1 +-0.3 mm,
leads 0.6 +-0.1 mm at 5.08 +-0.2 mm. This is the TE5 outline: KiCad `Fuse:Fuse_Littelfuse_395Series` (Fab 8.5 x 4.0,
courtyard 9.0 x 4.5, pads 1-2 round 2.0 mm / drill 1.0 at 5.08 mm, `hwflow lib pads`) fits it. `Device:Fuse` pins 1-2
map to pads 1-2 (non-polar).

Decision (product person, 2026-10-22: "smaller fuse"): keep the same fuse (same rating, I2t, breaking capacity,
approvals, price) and give it its real footprint, which is 4.5 mm instead of 8.0 mm across. No part change, cost delta 0.

Candidates considered (prices of the non-LCSC parts are search summaries: https://www.tme.com/ca/en/details/39213150000/fuses-te5/littelfuse/, https://novapart.co/products/39213150440./fuse-time-delay-315a-250vac-radial, https://www.futureelectronics.com/p/electromechanical--circuit-protection--electronic-fuses/39213150440-littelfuse-4807863):

| Candidate | Rating / breaking / approvals | LCSC, price | Footprint | Fit (back face, script below) | Verdict |
|---|---|---|---|---|---|
| Walter 2010T3.15A250V on the TE5 footprint (chosen) | T 3.15 A, 250 VAC, 100-130 A, I2t 98 A2s, UL/VDE/CCC | C354904, 0.035 USD, stock 281k | `Fuse:Fuse_Littelfuse_395Series` (mirrored _back) | 13 positions on face B (y 13.25, between K1 and J1), 609 on face A | chosen, same part |
| Littelfuse 392 TE5 3.15 A (39213150000 / ...440) | T 3.15 A, 250 VAC, breaking capacity and approvals not read (datasheet not opened) | not at LCSC; 0.37 EUR (500 pcs) - 0.66 USD (Future) | same TE5 footprint | same 13 positions | rejected: 10-19x the cost |
| Schurter UMT 250 3.15 A SMD | T, 250 VAC, 100 A, IEC 60127-4 / UL 248-14 | not in the hwflow index; distributor price about 1 USD (not verified) | `Fuse:Fuse_Schurter_UMT250` (11.4 x 4.3) | 0 positions | rejected: does not fit, costs more |
| Littelfuse Nano2 451/453 SMD (geometry only) | 125 V only | - | `Fuse:Fuse_Littelfuse-NANO2-451_453` | 38 back positions | rejected: not 250 VAC |
| Old placeholder footprint (bornier-2) | - | - | `thermo:TerminalBlock_bornier-2_P5.08mm_back` | 0 on face B (14 on face A only) | the conflict |

Fit proof: `notes/f1_fit.py` (run: `uv run python projects/home-thermostat-3/notes/f1_fit.py projects/home-thermostat-3 thermo:Fuse_Littelfuse_395Series_back`) loads the design (`Design`, `Libs`, `placer.build_parts`), places
the footprint on a 0.25 mm grid, rot 0/90/180/270, face B and mirrored on face A, and accepts a position only if each
AC pad box is <= x 20 (mains region), >= 1 mm inside the board, >= 6.0 mm from both notch circles (r 4 at (2.25, 23)
and (85.75, 23), 32-segment polygon as `hwflow floorplan`), >= 6.0 mm from every SELV pad of every fixed part (K1/K2
coil pads included) and from the SELV region x >= 26, the F1 courtyard on its face does not overlap a fixed courtyard
(real courtyards, as `hwflow floorplan`) or the notch rule areas, and no F1 through-pad sits in a fixed courtyard on
either face. Face A is excluded by R26 (230 V parts on the box-facing side), so only face B counts.
Face B: rot 0 at x 13.25-14.75, y 13.25 (7) and rot 180 at x 8.25-9.5, y 13.25 (6).
Chosen: rot 0 at (14.0, 13.25): pad 1 AC_L_IN (14.00, 13.25) toward J1.1, pad 2 AC_L (8.92, 13.26) toward the
K1/K2 contacts; notch_l 6.42 mm, nearest SELV pad K1.A1 6.73 mm, SELV region 11.0 mm, courtyard gaps to K1 0.15 mm
and to J1 0.22 mm (the best balance of the two isolation distances).
Placer note: `hwflow` boxes add 0.25 mm per courtyard, so the quick placement lists "courtyard overlap K1/F1, J1/F1"
(3.32 / 0.58 mm2); the old position had the same two (5.11 / 1.14 mm2). Real courtyards do not overlap (KiCad DRC
courtyard test passes). The slot between K1 and J1 is 4.87 mm and a 4.5 mm courtyard cannot keep 0.5 mm on both sides.
