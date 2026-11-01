# PM round 1 (2026-10-31, interactive)

Original brief: "localizzatore GPS per cani, durata batteria lunga, idealmente 1 anno ma anche un paio di mesi è ok, batterie tampone"

- q1 Communication: cellular LTE-M/NB-IoT to the owner's phone (IoT SIM, ~1-3 EUR/month).
- q2 Update rate: on request only (collar sleeps until the owner asks for the position).
- q3 Batteries: replaceable primary lithium cells (e.g. 2x AA Li-FeS2 or a CR123A).
- q4 Size: medium-large dog collar, device up to ~60 g.
- Battery life: ideally 1 year, a couple of months acceptable (original brief).

# PM round 2 (2026-10-31, interactive)

- q1 Request latency: about 2 minutes (eDRX ~81.92 s); fallback to a 60-min poll if the network refuses eDRX.
- q2 Cells: 2x AA Li-FeS2.
- q3 Lost mode: one fix per minute for up to 2 h, started by the owner.
- q4 Enclosure/feedback/cost: IP67, status LED + button, plus a remote-triggered find-the-dog buzzer/bright LED (+~1 USD, +2 g); BOM ~30 USD at 1000 units (without enclosure, cells, SIM) accepted.

# PM round 3 (2026-10-31, interactive)

- q1 (C6) Keep a separate GNSS receiver with Galileo (+5 USD, BOM ~29.5 USD).
- q2 (C7) Raise MECH-1 mass limit to 65 g; board stays 70 mm.
- q3 (C5) IF-2 latency limit 125 s ("about 2 minutes").
- q4 SIM: soldered eSIM (MFF2/eUICC).
- q5 Alkaline AA fitted by the user: support them properly (bigger reserve capacitor or supercap; offered cost +1-2 USD, +2-4 g).
- q6 Cell removal/brown-out: reset and network re-attach without stored position on the device is OK; no backup cell.

# PM round 4 (2026-10-31, interactive, unprompted)

- q1 Cost: BOM up to 50 USD is OK (COST-1 limit becomes 50 USD at 1000 units, same scope: without enclosure, cells, SIM).

# PM round 5 (2026-10-31, interactive)

- q1 Supercap on the back face accepted (MECH-3 deviation; two-sided assembly).
- q2 Device thickness ~27 mm accepted (cells under the board).

# PM round 6 (2026-11-01, interactive)

- q1 Alkaline at -20 C: keep full support; find an in-production supercap (or two in parallel) and raise the rail to 4 V for margin (+3-6 USD, +2-4 g offered).
- q2 Buzzer: 80 dB typical at 10 cm with the membrane, two-GPIO antiphase drive, measured on the first prototype in the housing.
- q3 CE/RED: prototypes only for now; no RED test round in this phase (not sellable until certified).

# PM round 7 (2026-11-01, interactive)

- q1 Supercap: use 2x CAP-XX GZ215F (in production) now; BOM limit raised to ~55 USD (COST-1 <= 55 USD @1000, same scope).
- q2 GNSS assistance: download assistance data (EPO or similar) over LTE every few days; LC76G backup supply off in sleep.
- q3 eSIM operator: delegated to the lead (pick a European IoT operator with LTE-M + eDRX in Italy/EU).
- q4 ~15 s first power-up (supercap charge) and hand-soldered supercap accepted.

# PM round 8 (2026-11-01, interactive)

- q1 Layout density: board grows to 75 x 35 mm (MECH-2), 4 layers kept; collar +5 mm, mass ~64.5 g (MECH-1 65 g).
