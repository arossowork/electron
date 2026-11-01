# CAP-XX product guide v6.6 (2023) + GZ115/GZ215 datasheet rev 4.5 (2020)
Sources: https://cap-xx-assets.s3.eu-west-2.amazonaws.com/CAP_XX_Product_Guide_V6_6_7ba17eb07a.pdf (pp.3-6, 12) ; https://www.cap-xx.com/wp-content/uploads/datasheets/CAP-XX-GZ115-GZ215-Datasheet.pdf (pp.1,5-7)
- **GW209F is in the 2023 guide v6.6 (G series, 5.0 V nominal / 5.5 V peak, 160 mF +-20 %, 55 mOhm nom, max = nom +20 % = 66, 2.1 mm, 28.5 x 17.0, MOQ 300, "select products ex-stock, rest available to order").** Distributors flag it obsolete/EOL: get a written CAP-XX confirmation (production, lead time, price at 1000).
- G series life: load life at 80 % Vn and 50 C (4.0 V for the module): C loss < 2 % typ / < 6 % max per 1000 h, ESR +4/+9 %. At 4.0 V rail the cells see 2.0 V (80 % of 2.5 V Vn): in the derated regime. Cycle life 500 k cycles < 5 % C loss.
- Mass: not published. Pgrav = Vn^2/(4 ESR)/W = 83-121 kW/kg (guide p.6) -> for GW209F (25/(4x0.055)=114 W) W = 0.94-1.37 g; TME gross 1.08 g incl. packaging. Use 1.4 g worst (was 2.0 g budget).
- Soldering (p.12): NOT for IR/hot-air reflow or wave; manual iron <70 W <=5 s at 400 C, hot bar, conductive adhesive, ultrasonic or laser welding. Same for all CAP-XX.
- Leakage: GW209F 1 uA max per module (2.3 V/cell, 23 C, 120 h); GZ215 table lists 1 / 2 uA (typ / max). Equilibrium typ 0.5 uA at 23 C, ~5 uA at 70 C (GZ datasheet Fig 10).
- ESR vs temperature: 2x at -40 C, 0.85x at 70 C (GZ datasheet Fig 7); C ~constant with temperature (Fig 6).
- GZ215F: 90 mF +-20 % (72 min), ESR 110 / 132 max, 20.0 x 15.0 x 2.5 mm, 5.0/5.5 V, IRMS 4.5 A, peak 30 A; Digikey price $10.33 @1000 (US, 2026-11), in production (datasheet 2020, guide v6.6). Two in parallel = 180 mF nominal (144 min), 55/66 mOhm, 40 x 15 mm or two 20x15 sites.
- GS203F 270 mF 35 mOhm 39 x 17 x 2.1 MOQ 2000; GA209F 90 mF 95 mOhm 20 x 18 x 2.1 MOQ 2000.
