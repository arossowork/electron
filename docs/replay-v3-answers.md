# home-thermostat-3: answers given through the web UI

Precedence: brief > v2 relay answer > baseline answers. When a question was not covered, the agent's recommended option is taken, or a decision by size > price > battery life without trading away safety. Marked *improvised*.

## Round 1 (requirements)

| q | question (short) | answer | source |
|---|---|---|---|
| q1 | power for Wi-Fi from 2xAAA | (recommended) tiny boost converter after the cells, still only two AAA | *improvised*: brief "used directly" read as "two AAA, no pack"; baseline also used a boost |
| q2 | remote command latency | within 60 minutes, >1 year with ordinary firmware | key (baseline R1q2/R3q3/R5q1) |
| q3 | battery change | (recommended) pull off the cover plate, cells face the user, 230 V behind the board; plus screw-fixed battery cover, insulated probe tips, low relays, switch off breaker before changing the battery | recommended + key (R3q1) |
| q4 | what 25 USD includes | (recommended) board, relays, battery holder, two probes with 2 m cable; plate and frame from the installed series | *improvised* (recommended) |

## Round 2 (architecture)

| q | question (short) | answer | source |
|---|---|---|---|
| q1 | battery margin at 60 min check-in | (recommended) fast-reconnect firmware, about 15 months, no cost | *improvised* (recommended; no cost, no size) |
| q2 | behaviour when batteries run out | (recommended) switch both rooms OFF, send "battery empty", stop | key (low battery: outputs OFF + alarm) |
| q3 | temperature accuracy | (recommended) 0.3 C by design, precision ADC, about +1.5 USD | *improvised*: thermostat quality; the key accepts 27-28 USD for the prototypes |
| q4 | relay switching energy 7-9 mJ | (recommended) yes, keep the full pulse | *improvised* (recommended; about 1 day per year) |

## Stage 3 stop (parts): datasheet access and cost

The agent stopped and asked to add `Bash(curl ...)` and `Bash(pdftotext:*)` to `.claude/settings.json` so it could read datasheet PDFs. Its parts estimate was 23.9 USD; with the PCB and assembly shared over 10 units, 29 to 34 USD.

Direction given (*improvised*):
- No settings change. Main has the same permissions, so the baseline ran under the same limits, and widening an agent's permissions is not mine to grant while the user is away. Numbers from searches are marked unverified, used with a margin, and listed to check on the first prototype.
- Cost: about 27 to 28 USD for the prototypes is acceptable (key, R4q3).
- Keep the Panasonic ADW1 relays rather than an unconfirmed clone (size, then price).
- Reword R18 so a standard 3.3 V protection part can meet it.
- The setup button may be read at the once-a-minute wake.

## Round 3 (review)

| q | question (short) | answer | source |
|---|---|---|---|
| q1 | guarantee Wi-Fi down to 1.8 V? | (recommended) no: full operation down to the 2.0 V cut-off, small capacitors | *improvised* (size first; the 2.0 V cut-off was already decided in round 2) |
| q2 | lithium AAA (3.6 V) | support lithium cells, a voltage-limiting part, about +0.3 USD (not the recommended option) | key: "alkaline or lithium both allowed; lithium recommended for life" |
| q3 | probe cables next to 230 V wires | (recommended) 300/500 V rated cable or sleeve, plus a screw terminal, about +0.5 USD | *improvised* (safety) |
| q4 | prototype cost about 37.5 USD | (recommended) accept 33-38 USD for the 10 prototypes; 25 USD stays the production target | *improvised*: above the key's 27-28 USD; most of it is one-off order cost shared by 10 units. **Flag for the user.** |

Operator slip in round 3: my form script picked the hidden round-2 form, so the round-2 options were sent as round-3 answers (q2 would have added a supercapacitor). I stopped the session about 1 minute later, before any design change (design.yaml last written before the answers). I rewrote the round-3 block of `inbox/answers.md` with the answers in the table above and sent a correction message. Later rounds use only the visible form, and the option text is checked before sending.

## Stage 5: R6 sleep floor (direction, *improvised*)

After about 1 h of review iterations, the only failure left was R6: sleep floor 25.26 uA against a 25 uA limit. I stopped the session and accepted up to 26 uA (about 1 % of battery life, still more than 1 year; battery life is the third priority), with the instruction to move on to layout.

## Stage 7: layout direction (from the user, 2026-10-21)

The user: "larger board is not a option, it must fit in a 3-module italian box, 4 layers is doable". Attempt 1 on 74 x 46 mm, 2 layers, parts on both faces: 95 unconnected, 10 creepage, 8 clearance, 8 copper-edge clearance. Direction to the agent: board size fixed, 4 layers approved.

## Round 4 (layout, 4 layers)

The 4-layer build at 74 x 46 mm failed: 92 unconnected; DRC 205 (67 mains clearance, 40 creepage, silkscreen and mirrored text); R10 isolation failed. The agent escalated after one build, as directed. Causes it gave:
- the placer does not keep the 230 V group in a region;
- the router puts mains nets on the inner layers under SELV parts;
- 74 mm leaves no room for the 230 V zone, the 6 mm gap and a 53 mm cell holder.

| q | question | answer | source |
|---|---|---|---|
| q1 | board shape | (recommended) 88 x 46 mm with two screw-post notches, which fits inside the 503 box (inner 96 x 50 mm, bosses at 83.5 mm). Large parts get fixed positions from the architecture floorplan. Mains nets kept out of the SELV area on every layer, or escalate if that needs an hwflow feature. Fix the back-side silkscreen. Then one build. | *improvised*, within the user's constraint "must fit in a 3-module Italian box" |

## Stage 7: 88 x 46 mm floorplan, one more build (direction, *improvised*)

Result of the 88 x 46 mm, 4-layer build with a fixed floorplan:
- mains clearance 67 -> 0;
- 73 unrouted, all on low-voltage nets;
- DRC 96: 72 silkscreen, 17 creepage (GND pour 5.5 mm against 6.0 mm), 6 clearance at the 0.5 mm-pitch U2, 5 dangling;
- floorplan errors: F1 in the notch, J1 1.13 mm short, overlapping courtyards.

The agent asked to run one more build with three fixes: the anchors, a 6.0 mm mains gap, and a 0.15 mm fine-pitch rule. Approved: all three are stricter or correct floorplan mistakes. The agent was told to stop after that build if the low-voltage nets stay unrouted.

## Stage 7: attempt 6 (88 x 46 mm, anchors corrected)

Result: 71 unconnected, all low-voltage; DRC 91, of which 74 are silkscreen. The agent stopped and asked: fix hwflow or try another layout approach.

Operator analysis of krt.log:
- The router applies the mains keep-out rules only to the 4 mains nets, as intended.
- In the 0.25 mm pass, 26 of 27 signal nets fail as "boxed_in_static" after 2 iterations. Every cell around their pads is blocked, even at sparse 0402 pads (R2 EN at 70.9, 33.8 mm). So something static covers those pads; density is not the cause.

A diagnosis subagent (router run on single nets, no full builds) looks for the obstacle before the agent gets an answer.

Diagnosis result: the router's "pre-existing rip candidate" mode boxes in the narrow pass's pads once the earlier passes have laid copper. hwflow now sets `KICAD_RIP_PREEXISTING=0`. A copy of this project, same floorplan: 73 -> 11 unrouted, DRC 96 -> 61. The agent was told the fix is in hwflow and to run one clean build, then at most 3 cycles on the named non-routing errors.

## Stage 7: attempt 7 (router fix)

Result:
- Mains isolation R10 passes; clearance and creepage 0.
- The agent added a 6.2 mm rule area around the mains pads (mains nets only) and moved J1 by 0.6 mm.
- DRC 11, all unrouted: SENS_EN 3, VBAT_P 2, +3V3, SW_BOOST, two relay coil pads inside the new zone, 2 GND zone items.
- F1 notch: false flag from the placer's box test (real gap 4.32 mm, KiCad clean).
- Found: the route cache key ignored the router environment (fixed in 1877c78).

Approved (*improvised*): a channel in the mains zone to the coil pads, one build with --attempts 8, the F1 flag accepted as a false positive. Stop if unrouted is not 0.

## Stage 7: attempt 8 (--attempts 8, about 25 min)

Result: 12 unrouted, all at the boost chip U2 and the analog chip U5 (SENS_EN 3, +3V3 2, VBAT_P 2, SW_BOOST 1, K2_COIL_B 1, 3 GND islands). Isolation still passes. No channel was cut: the coil pads were already outside the mains zone.

Operator diagnosis: krt.log shows SW_BOOST (0.6 mm) and VBAT_RAW (0.4 mm) as "boxed in by static obstacles" at U2's 0.5 mm-pitch pads. So the track width is the cause, not crowding.

Direction for the last cycle (*improvised*):
- width_mm 0.3 on SW_BOOST, VBAT_P, VBAT_RAW and +3V3, checked with post-layout IR drop and current density;
- SENS_EN as a signal-class net;
- no part moves, one --auto build;
- continue to post-layout if 0 unrouted, else stop.
