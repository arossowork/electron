# Panasonic DW relay (ADW12xx 2-coil latching, ADW11xx 1-coil latching), 1 Form A

Source: Panasonic "DW RELAYS Product Catalog" 2024.3 (ASCTB8E 202403), PDF at https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/8811/mech_eng_dw.pdf (read pages as images; text extraction was blocked). Page numbers below are the catalog's printed page numbers (PDF page = printed + 1). Product page: https://industry.panasonic.com/ap/en/products/control/relay/power/number/adw1203tw

## Part numbers (p.2)
- Standard 8 A, reflow compatible: ADW1203W (2-coil, 3 V). Standard 8 A, IEC60335-1 compliant: ADW1203TW. Inrush 16 A (100 A inrush): ADW1203HTW (18.8 mm), ADW1203HLW (low profile 15.8 mm). The suffix W is only on the packaging.
- Packing 100 pcs inner carton, 500 pcs outer.

## 2-coil latching 3 V coil data (p.3)
| Fact | Value |
|---|---|
| Coil resistance (set and reset coil) | 22.5 ohm +/-10 % at 20 C |
| Rated current | 133.3 mA +/-10 % each coil |
| Power | 400 mW per coil |
| Set / reset voltage (initial) | max 80 % of rated = 2.4 V |
| Max allowable voltage | 110 % = 3.3 V at 20 C |
| Note | "use the relay within +/-5 % of rated coil voltage" (2.85-3.15 V); square pulse drive |

## Specifications (p.4)
| Fact | Value |
|---|---|
| Contact | 1 Form A, AgSnO2, 8 A 250 VAC resistive, max 2000 VA, min load 100 mA 5 VDC, contact resistance max 100 mohm |
| Dielectric: contact-coil | 5000 Vrms 1 min; open contacts 1000 Vrms |
| Surge contact-coil | 12 kV (1.2 x 50 us) |
| Set / reset time | max 15 ms at rated voltage, 20 C, without bounce (p.5 graphs: max about 5.5 ms at 80 % voltage) |
| Mechanical life | min 1e6 operations at 180/min |
| Electrical life (20 /min) | Standard (W): 8 A 250 VAC min 50e3 ops. IEC60335-1 type (TW): 5 A 250 VAC min 100e3 ops |
| Ambient | -40 to +85 C (standard type), weight about 8 g |
| Inductive rating | NOT given. VDE: 8 A 250 VAC cos phi = 1.0, 50e3 ops; UL E43149: 8 A 250 VAC resistive. No cos phi 0.4/0.6 rating anywhere in the catalog |
| Creepage / clearance coil-contact | NOT stated in the catalog (eeNews article claims 6 mm), "reinforced" not stated; IEC60335-1 type has PTI 325 V (p.1) |
| Approvals | UL/C-UL E43149, VDE 40032254, CQC |

## Dimensions and pad pattern (p.6, bottom view)
Body 24 x 10 mm, height 18.8 mm (standard and TW type), pin length 3.2 mm. PC board pattern: holes 1.2 mm dia, tolerance +/-0.1: coil pins at x=0 (pin 1 y=0, pin 7 y=3.75 [2-coil only], pin 6 y=7.5); contact pin 3 at (17.5, 0), pin 5 at (21, 7.5). The 3.75 mm middle position of pin 7 is read from the drawing (small image), confirm with the Panasonic CAD/STEP file before the layout is frozen. KiCad Relay_THT:Relay_SPST_Panasonic_ADW11 (1-coil) has pads A1 (0,0), A2 (0,7.5), 13 (17.5,0), 14 (21,7.5): same geometry.
Schematic (bottom view, 2-coil): -1 top left, +7 left middle (common of both coils), -6 bottom left, 3 top right, 5 bottom right. Which of coil 1 or coil 6 sets the relay is not readable at the image resolution: bench-test on the first article (firmware table).

Rejected alternative: TE PE series latching (20 x 10 x 10 mm, 3 V, 200 mW, 4 kV) coil-contact clearance/creepage 3.2 / 4 mm, below the R9 reinforced requirement.
