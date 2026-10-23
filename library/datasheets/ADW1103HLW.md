# Panasonic DW relay ADW1103HLW (1 coil latching, 3 V) and TE Schrack PE014F03 (rejected)

Sources: industry.panasonic.com/global/en/products/control/relay/power/number/adw1103hlw (fetched 2026-10-20); web search summaries (RS / Panasonic) for dimensions and insulation distances; te.com/en/product-1415390-1.html. Stage 3 must confirm dimensions, pinout (coil pins vs contact pins), 2-coil (ADW12) coil power, and LCSC price/stock.

## ADW1103HLW
- 1 Form A, 1-coil latching, nominal 3 V DC
- Coil resistance 45 ohm +-10 % (20 C); nominal operating power 200 mW; rated current 66.7 mA
- Set / reset voltage: max 80 % of rated (2.4 V)
- Set / reset time: max 15 ms at rated voltage
- Contact 16 A 277 VAC resistive; TV/tungsten 1200 W at 240 VAC
- Dielectric coil-contact 5000 Vrms 1 min; surge 12 kV coil-contact
- Insulation distance coil-contact: clearance >= 6 mm, creepage >= 6 mm (search summary of Panasonic data)
- Operating temp -40 to +70 C (8-16 A), -40 to +85 C (<= 8 A)
- Size: 24 x 10 mm footprint; height 15.8 mm for the low-profile (HL) type, 18.8 mm for the standard type (search summary; confirm)

## Stage 3 close-out (2026-10-20)
- Panasonic product page (vendor): max allowable coil voltage at 20 C = 110 % of rated = 3.3 V; weight about 7.5 g; PC board terminal. The page has no pin pitch, hole size, pattern, or creepage numbers.
- Pin pitch and hole sizes were NOT retrievable (PDF unreadable; SnapEDA and distributor pages empty). thermo:Relay_Panasonic_ADW1_1coil has PLACEHOLDER pad positions: contact pins 13/14 at the -x end 7.5 mm apart, coil pins A1/A2 at the +x end 5 mm apart, drill 1.7 / 1.4. Replace from the Panasonic "PC board pattern (bottom view)" before layout.
- Symbol Relay:Relay_SPST_Latching_1coil: 13, 14 = the two contact terminals (Form A contact is symmetric: one is COM, the other NO; wire L in to one and the room output to the other); A1, A2 = coil. Which coil pin is + for set is not confirmed; the H-bridge can drive either polarity, so firmware resolves it on the first prototype and the hardware does not depend on it.

## TE Schrack PE014F03 (1415390-1), rejected
- 1-coil latching 3 V, 0.22 W, 41 ohm, 5 A 250 VAC, 20 x 10 x 10 mm
- Coil-contact 4000 Vrms but creepage 4 mm, clearance 3.2 mm -> not reinforced, fails R11
- Product page: "not currently available"
