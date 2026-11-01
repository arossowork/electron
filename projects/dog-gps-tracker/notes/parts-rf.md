# Parts group B: modem, GNSS, RF, SIM (stage 3, dog-gps-tracker)

Reference designs: `hwflow ref search nRF9151` and `LC76G`: 0 hits (local, blocks, external). `block match`: no library block for these parts. Index `hwflow parts` returns HTTP 404 (down) for crystals/MCU: stock from web (below). Budget at end: ok.

| ref | part | mpn / lcsc | price_usd @1000 | stock | footprint (height) | symbol | side/method |
|---|---|---|---|---|---|---|---|
| U_SIP | Nordic nRF9151 SiP | NRF9151-LACA-R7 (reel) / C22397843 | 13.75 (Symmetry); 15.37-15.38 (Mouser/DigiKey reel MOQ 2000); LCSC 15.83 | LCSC 0; Mouser 27441, DigiKey 19285 (reel), Newark 10246 | **missing in KiCad**: LGA-113 12.1 x 11.1 x 1.2 -> project footprint (gap) | **missing** (MCU_Nordic has only nRF51/52): project symbol (gap) | front, SMT reflow |
| U_GNSS | Quectel LC76G-PA | LC76GPANR12A?? (full MPN to confirm on Quectel/distributor) / LCSC no | ~5 (estimate, not verified; no price found) | not confirmed | RF_GPS:Quectel_L76 has 18 of 28 pads: LCC pads usable, LGA 19-28 missing (gap, project fp advised) 10.1 x 9.7 x 2.4 | none for LC76G (RF_GPS has L70-R, L80-R only): project symbol, 18 LCC pins + D_SEL | front, SMT |
| ANT_LTE | Ignion ALL mXTEND NN02-220 | NN02-220 / lcsc unknown (Mouser/DigiKey carry it) | ~1.5 (estimate) | not checked | 24 x 12 x 2 mm, ignion datasheet land pattern (project fp) | generic Antenna | front, SMT |
| ANT_GNSS | 12 x 12 x 4 mm GNSS patch 1575.42 MHz, pin feed | class part, not chosen (e.g. a Taoglas/Partron/Yageo 12x12 patch: choose with lcsc search) | ~1.5 (estimate) | - | THT pin feed per items entry | generic Antenna | front, hand/THT |
| FL_GNSS | Murata SAFFB1G56AC0F7F SAW (Quectel-recommended) | SAFFB1G56AC0F7F | ~0.3 (estimate) | - | 1.1 x 0.9 class | generic | front SMT |
| U_ESIM | MFF2 eUICC: Truphone SIM-S-IO3-MFF2-2 (ST33G1M2M, 10 profiles) | SIM-S-IO3-MFF2-2 / C5122390 | not shown (LCSC page has no price) | not shown | DFN-8 5x6 x 0.9 (MFF2, ETSI TS 102 671): KiCad has WSON-8-1EP 6x5 P1.27 with EP, MFF2 has NO exposed pad: need project fp; kicad item in design.yaml uses EP version = wrong, flag | generic 8-pin | front SMT |

Alternatives (max 2 each): eSIM: Hologram SIM-E-MFF2-GL, 1NCE IoT SIM Chip Industrial (sold with a data plan; no stand-alone price found), sysmocom sysmoEUICC1 (consumer RSP, wrong profile type for IoT). GNSS: Quectel LC76G-AB (28.3 mA vs 9.7: rejected) and PB (1.8 V VCC, 1.75-1.98 V: no, our rail is 3.3 V); u-blox/other not surveyed. Modem: no alternative (Quectel BG77 rejected in architecture section 1).

## LTE match (Ignion AN, 60 mm column, for our 57 mm)
Z1 4.7 nH LQW15AN4N7B80, Z2 13 nH LQW15AN13NG00, Z3 1.2 pF GJM1555C1H1R2WB01, Z4 1.1 pF GJM1555C1H1R1WB01, Z5 9.1 nH LQW15AN9N1G00, Z6 2.5 pF GJM1555C1H2R5WB01, Z7 2.2 nH LQW15AN2N2B80 (all Murata, 0402). Risk: the role (series/shunt) of Z1..Z7 is in the image of Fig 6, read it before capture; values are for a 60 mm wide board (ours 35 mm), so VNA tuning is expected: keep all seven pads, 0402.

## Passives (circuit stage, from datasheets read; unverified ones marked)
- LC76G VCC: TVS + 10 uF + 100 nF + 33 pF (Quectel p.25); V_BCKP: same style 4.7 uF + 100 nF + 33 pF; RESET_N: no external pull-up (internal 10k to 1.8 V), drive open-drain from GPIO or leave N/C; D_SEL N/C (UART); RESERVED 5,7,15,18,19 floating.
- nRF9151: DEC0 4.7 uF (Nordic, fixed); VDD 10 uF + 100 nF class (unverified, nwp_056 unreadable); 32.768 kHz crystal: believed inside the SiP (unverified); antenna pad -> pi/L match to 50 ohm CPWG then Z1..Z7 -> NN02-220 feed; SIM lines 3 + supply direct to the soldered eSIM, 100 nF on SIM_VDD (ESD array not needed, soldered).
- GNSS RF: patch -> SAW (Murata above) -> RF_IN pin 11; passive patch needs gain > 0 dBi RHCP, VSWR < 2; a 12 x 12 patch is typically -1 .. -2 dBi on a 35 mm ground: borderline (risk).

## limits (YAML for the circuit stage)
```
U_SIP:  limits: {abs_max_v: 5.8, rec_max_v: 5.5, pins: {VDD: {abs_max_v: 5.8, source: "nRF9151 PS, abs max (value to verify)"}, VDD_GPIO: {rec_max_v: 3.6, source: "DevZone 126378, Nordic"}}, source: "VDD 3.0-5.5 V PS features; abs-max numbers NOT read"}
U_GNSS: limits: {abs_max_v: 3.63, rec_max_v: 3.6, pins: {V_BCKP: {abs_max_v: 3.63}, RESET_N: {abs_max_v: 3.63, rec_max_v: 3.6}}, source: "LC76G HW design V1.3 Table 10 p.41 / Table 6 p.20-22"}
U_ESIM: limits: {abs_max_v: 5.5, rec_max_v: 5.0, source: "emnify industrial eSIM class 1.62-5.5 V; Truphone datasheet not read: verify"}
```
GNSS supply net v_max: 3.3 V LDO output + accuracy: 3.3 x 1.03 = 3.40 V < 3.6 V rec max (VCC and V_BCKP). nRF9151 VDD net (VDD_3V6) 3.6 V boost + ripple under 5.5 V. The UART/GPIO level: LC76G VIH 2.0 V needs nRF9151 VDD_GPIO >= 2.0 V (3.0-3.3 V ok; do not set 1.8 V).

## Loads for simulation (no SPICE model; current-source profile is enough)
nRF9151 (VDD_3V6): PSM 2.7 uA, eDRX 18 uA (Cat-M1), TX burst 0.5 A / 2 ms (lead default; 23 dBm peak not read in datasheet), RX/ connected ~ 30-60 mA (class value, unverified). LC76G (3.3 V LDO): 9.7 mA tracking/acquisition (24 mA peak, 127 uA on V_BCKP when active), 0 when GNSS_EN off. `spice.source: fit` n/a; model as piecewise-linear current sources, `spice.mpn` = the same part.

## Sourcing / certification findings (numbers that matter)
1. nRF9151 LCSC C22397843: 0 stock. Path: Mouser/DigiKey reel (MOQ 2000, 15.37-15.38 USD) or Symmetry 13.75 @1000 (0 now, 1322 by 2026-12-30). Hand-buy -> consigned part for JLCPCB assembly ("sourcing:" line: customer-supplied reel, MOQ 2000 > 1000 build? check lot size). 1000 pcs at 13.75-15.4 is 13.8-15.4 k USD.
2. No pre-certified third-party nRF9151 module exists on the search. Nordic's SiP is "certified for global operation" at the modem level, but CERT-1 as worded ("uses a pre-certified cellular module ... antenna per the module's reference design") is met only partly: the SiP carries GCF/PTCRB/carrier approvals, but our own antenna (NN02-220 on a 57 mm x 35 mm ground, +15 dB worse than reference) and product need RED radio tests: antenna total efficiency (-7 dB) can fail TRP / conducted spurious targets. Needs product-person decision (budget for RED test round, typically several k USD) and Nordic's certificate list review (not found online by my searches).
3. LC76G is 28-pad, the architecture/design.yaml says footprint-compatible with L76 (18 pads): partly true (the 18 LCC pads), the centre 10 LGA pads (GND 28, D_SEL 24, SPI, I2C) are missing: gap.
4. LC76G cold start 28 s w/o AGNSS (spec table), 15 s with EASY, only 5 s with EPO: with GNSS fully off in sleep (13 uA backup rejected by PWR-5) every fix is a cold start -> IF-2 30 s cap needs A-GNSS/EPO injection from the LTE side; nRF cloud A-GNSS format is nRF9151-specific, not LC76G. Architecture change risk: IF-2 budget 30 s with 28 s TTFF has 2 s margin in open sky only. Keep V_BCKP always on = +13 uA vs PWR-5 10 uA: needs a product decision, or use warm data via EPO files uploaded over LTE.
5. LC76G PA tracking current is 9.7 mA (8.2 mA in architecture was a summary).
6. eSIM: price/stock unknown (LCSC page shows none). COST-1 excludes SIM. The design.yaml footprint WSON-8-1EP_6x5 with EP is not an MFF2 (no exposed pad).

## Unit cost (this group only, @1000, estimates marked)
nRF9151 13.75-15.4 + LC76G ~5 (est.) + NN02-220 ~1.5 (est.) + patch ~1.5 (est.) + SAW ~0.3 + passives ~0.6 + eSIM ~1.5-3 (not in COST-1) = ~22.7-24.3 USD in COST-1 scope; architecture assumed 13.75 + ~5 = 18.75 for the modem group. Whole-board total is not computed here (no design.yaml BOM); the architecture estimate is 32.1 USD against 50.

## Floorplan facts (early layout gate)
| item | footprint id | height | drawing checked | side / method |
|---|---|---|---|---|
| sip | project: nRF9151 LGA-113 (to draw) | 1.2 mm | PS features page only (12.1 x 11.1 x 1.2); pad map NOT read: PLACEHOLDER | front, SMT |
| gnss_rx | RF_GPS:Quectel_L76 (18 LCC pads) | 2.4 mm | LC76G HW design p.9/40: 28 pads, body 10.1 x 9.7; fp pad span x=±4.85, 18 pads checked via lib pads; LGA pads absent | front, SMT |
| lte_ant | project NN02-220 (Ignion datasheet) | 2.0 | AN p.4: 24 x 12 x 2; land pattern not read: PLACEHOLDER | front, SMT |
| esim | project MFF2 8-pad no EP | 0.9 | ETSI TS 102 671 5 x 6 x 0.75 (via Kigen/emnify): not read from drawing | front, SMT |
No connectors in this group.

## Risks
Single source (Nordic, Quectel, Ignion); nRF9151 and LC76G have no KiCad symbol and an incomplete/absent footprint; nRF9151 pin table and reference-circuit values unread (HW guide nwp_056 unreachable by fetch: needs the user to supply the PDF); GNSS and eSIM prices are estimates; patch part not selected; LC76G MPN suffix not confirmed.

---
# FIX PASS 2026-11-01 (PM round 7)
- U_ESIM operator (delegated, conflicts.md C11): 1NCE IoT SIM Chip Industrial MFF2 eUICC, EUR 14.50 per SIM (EUR 12.00 flat 500 MB / 10 years + EUR 2.50 chip), outside COST-1; details and the unverified Italy LTE-M/eDRX/PSM row in notes/datasheets/mff2-esim.md. Alternatives emnify, Onomondo, 1GLOBAL (Truphone).
- U_SIP pins 14 VDD, 65 VDD_GPIO, 19 SIM_1V8, 24 DEC0, 35 ANT, 9 nRESET, 10 ENABLE and the GND list CONFIRMED against the Makerdiary Connect Kit schematic (independent) and the Nordic PS snippet: notes/datasheets/nrf9151.md. PS PDF not fetched (mirror returned a script page).
- U_GNSS: PM round 7 q2: assistance data over LTE every few days, V_BCKP supply off in sleep: every fix is a warm/cold start with injected assistance; first fix TTFF to be measured (IF-2 budget 30 s: 28 s cold without assistance, 5 s with EPO; EPO injection format for LC76G must be implemented in firmware, not nRF Cloud A-GNSS). limits unchanged. VDD_GPIO of the nRF9151 and LIS2DW12 now on VDD_AON (3.0 V), see parts-power.md G2.

## Stage-5 review fixes (2026-11-01)
- U7 bound to the 1NCE IoT SIM Chip Industrial MFF2 (mpn field "1NCE-IoT-SIM-Chip-Industrial-MFF2", no maker part number published); its pin table was not readable: first-article check against ETSI TS 102 671 (1 GND, 2 VPP, 3 I/O, 6 CLK, 7 RST, 8 VCC). Footprint pads pulled back to x 1.9..2.85 and an F.Cu track/via keep-out 3.4 x 4.2 under the die pad.
- R21/R22 1 k series at U1 on GNSS_RX / GNSS_RST_N (limits back-power current into the unpowered LC76G to (3.0 - 0.3) / 1 k = 2.7 mA worst case if firmware leaves them high).
