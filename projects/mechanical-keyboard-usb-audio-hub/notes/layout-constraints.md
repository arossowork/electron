# Layout constraints (circuit stage -> hw-layout)

Rules the circuit relies on that `design.yaml` cannot express. hw-layout reads this file; each rule names its source.

## LC1: SuperSpeed return path at the C ports J311 / J312 (delta review D8, stage 5 M1)
Why: the connector GND pins sit on Cn_RTN (the IP2736U low-side shunt R709 / R759 is in the port return, Fig. 1),
so the SS pairs (C1/C2_TX/RX, 45 ohm single-ended / 90 ohm differential) leave the hub over GND and arrive at the
receptacle over Cn_RTN. Their return current changes reference plane at that boundary; the only AC path between the
planes is C724-C726 / C774-C776 (3 x 10 nF) + C727 / C777 (100 pF).
- A solid Cn_RTN pour on the layer next to the SS pairs under the whole receptacle footprint, from the connector
  pads back to at least the HD3SS3212 side of the ESD arrays D311-D316; no GND copper under the receptacle on that layer.
- The reference transition (GND -> Cn_RTN) happens at one line across the pairs. On that line, at every SS pair
  crossing, place a row of stitching caps (0402, 10 nF + 100 pF, the C724-C727 / C774-C777 values; add more of the same
  if the pair count needs it) on BOTH sides of each pair, pads within 1 mm of the pair edge, each cap with its own via
  to GND and to Cn_RTN.
- D+/D- (USB 2) follow the same transition line.
- The shunt R709 / R759 is the only DC tie between Cn_RTN and GND: no other copper joins the two pours. Kelvin traces
  from the shunt pads to CSP / CSN.
- C724-C727 / C774-C777 within 2 mm of the connector GND pins (stage 5 M1); C723 / C773 (shell 10 nF 100 V) at the
  shell tab.
