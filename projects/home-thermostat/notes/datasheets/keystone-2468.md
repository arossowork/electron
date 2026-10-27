# Keystone 2468 (2 x AAA, PC pins), catalog page M65p28, https://www.keyelco.com/userAssets/file/M65p28.pdf (p.28)

Drawing read (one catalog page; the page has no per-part mm coordinates for the mounting posts).

| Item | Value |
|---|---|
| Body | 2.086 in (53.0 mm) long x 0.968 in (24.6 mm) wide x 0.512 in (13.0 mm) high above board |
| Terminals | PC pins 0.031 in (0.79 mm) brass, nickel plated, on the short end, 0.500 in (12.7 mm) apart; the drawing shows 0.590 in (15.0 mm) from the board edge of the body to the end with the terminals |
| Mounting posts | 0.115 in (2.9 mm) dia, "(3) PLS" (3 places) |
| Contacts | nickel plated spring steel; holder polypropylene |
| Not keyed | cells can be inserted reversed (one reversed cell: about 0 V; two: blocked by M3) |
| Price | DigiKey 36-2468-ND: $1.18 at qty 10, $0.77 at 1,000; stock 3,176 (search result, not the page); Mouser 534-2468; Farnell EUR 1.88 at 10. No LCSC listing found (LCSC C5370863 was the 1 x AA 2460). |

KiCad footprint `Battery:BatteryHolder_Keystone_2468_2xAAA`: pads 1 (square, 2.0 mm, at 0,0) and 2 (round, at 0,12.70), pad spacing matches the 12.7 mm of the drawing. Two NPTH 3.45 mm at (8.64, 4.00) and (38.61, 8.70); bbox -3.09..50.89 x -6.45..19.15 mm (54.0 x 25.6 mm). Differences to the drawing: the catalog shows 3 posts of 2.9 mm, the KiCad footprint has 2 holes of 3.45 mm. Layout stage: check the post positions against the Keystone customer drawing (DigiKey page 36-2468-ND) and the polarity (which pad is +); the footprint comment says pad 1 = + as for the 2460 (not confirmed against the 2468 drawing).
Orientation: long axis of the footprint along x. The terminals are at x = 0, the end the holder body overhangs the board edge by <= 1.2 mm (architecture section 8) is for layout to set.
