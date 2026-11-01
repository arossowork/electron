# CERT-1 evidence (datasheet)

Requirement (could): CE / RED compliant for the EU (deferred: prototypes only in this phase, no RED test round); uses a certified cellular SiP and its reference RF design so a later RED round is not blocked

Acceptance: module datasheet lists CE/RED, GCF and the target operator approvals; antenna per the module's reference design

## Result

Result: open

Evidence: notes/datasheets/nrf9151.md line 15: Nordic markets the nRF9151 SiP as "certified for global operation" (DigiKey highlight); the SiP carries Nordic's modem certification (GCF, PTCRB, carrier) but the end product still needs RED testing (EN 301 908-13, -25, EN 303 413, EN 62368, EN 301 489) with its antenna. The notes do not list CE/RED, GCF or named operator approvals from a Nordic datasheet page, and no pre-certified module was found (search 2026-10). Antenna: Ignion NN02-220 with the 60 mm ground column matching network (Z1..Z7), per nn02-220.md (AN_NN02-220_Clearance-Length_GP Tables 5-6); this follows an Ignion application note, not the Nordic reference design. Operator: 1NCE eSIM (mff2-esim.md), Italy LTE-M coverage not verified. CE/RED itself is deferred (PM round 6, q3). Open: confirm the GCF and operator lists in the nRF9151 product specification.
