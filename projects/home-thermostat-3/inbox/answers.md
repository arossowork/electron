
## Round 1
q1: (recommended) Add a tiny voltage converter after the cells: still only two AAA cells, about +1.5 USD and no visible size change, uses about 85 % of the battery
q2: Within 60 minutes: more than 1 year even with ordinary firmware
q3: (recommended) Pull off the cover plate; the cells face the user and all 230 V parts are hidden behind the board, so no electrician is needed. Also: a screw-fixed battery cover, insulated probe tips, low relays, and an instruction to switch off the heating breaker before changing the battery.
q4: (recommended) Board with relays, battery holder and the two temperature probes with 2 m cable; the cover plate and frame come from the wall-plate series already installed. Estimate 21-26 USD, mid about 23

## Round 2
q1: (recommended) Require fast-reconnect firmware (remembers the network, resumes the secure session): about 15 months, no extra cost, measured on the first prototype
q2: (recommended) Shortly before the batteries are empty, the device switches both rooms OFF, sends a 'battery empty' message and stops. If someone pulls the batteries while a room is heating, that room stays ON until new batteries go in (the label already says to switch off the heating breaker first). No extra cost
q3: (recommended) Within 0.3 C, guaranteed by design: adds a small precision measuring chip, about +1.5 USD; we confirm the total stays under 25 USD when parts are priced
q4: (recommended) Yes: keep the full switching pulse for reliable switching even with old or cold relays; about 1 day less battery life per year, no cost

## Round 3
q1: (recommended) No: guarantee full operation down to the 2.0 V cut-off, with Wi-Fi bursts of up to 350 mA (the chip's real maximum is 335 mA). Keeps the small capacitors and the 1-year battery budget
q2: Support lithium cells too: needs an extra voltage-limiting part, about +0.3 USD and a little board space
q3: (recommended) Order probes with 300/500 V rated cable insulation (or a sleeve over the probe cable inside the box), and use a small screw terminal the installer can wire instead of a tiny crimp connector: about +0.5 USD per unit, a few mm more board edge
q4: (recommended) Accept about 33-38 USD for the 10 prototypes; the one-off costs fall with volume and 25 USD stays the production target

## Round 4
q1: (recommended) 88 x 46 mm with the two screw-post notches: approved, it still fits inside the 503 box (inner 96 x 50 mm). Also approved: fix the positions of the large parts (J1, F1, K1, K2, C1, C2, BT1, BT2, U1) from the architecture floorplan, 230 V zone at one end with the 6 mm gap, antenna on the edge away from it. Keep mains nets out of the SELV area on every layer: if design.yaml can express it (keep-out areas or net class rules), do it; if it needs a new hwflow feature, say so. Fix the mirrored-text and silkscreen errors in the back-side footprints. Then one --auto build; if it still fails, escalate with the numbers, no tuning loops.
