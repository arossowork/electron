stage: 3-parts
status: waiting-for-answers
iteration: 1
round: 3
done: stages 1-2 (architecture.md, budgets: sleep 8.90 uA, 213 uW = 12.3 months, NTC worst 0.413 C, VBAT 16.6 mV). C6 (AA end clips, SMD under the cell), C7 (bulk-cap leakage by first-article test), C8 (R1 0-80 mA continuous) resolved by orchestrator.
done: parts notes: notes/parts-{power,mcu,relay,ntc}.md. Boost TPS61099 adjustable (TPS610994 not stocked; TPS610995 is 3.6 V, wrong). ESP32-C3-MINI-1-H4 + project lib (lib/symbols/thermostat.kicad_sym). Relay project lib thermostat_relay (Panasonic DW).
open: C9 cost/size: relay (A) Panasonic DW 65x42 about 35 USD vs (B) Omron G5RL 75x48 blank plate about 25 USD -> PM round 3 q1 (inbox/questions.json).
decided (engineering): 12-bit ADC (TLA2024/ADS1015 class) instead of SGM58031 to save about 4 USD; verify R14 in stage 4.
todo after answer: relay symbol/footprint for the chosen relay; pick the 12-bit ADC part; update R12 if (B); GPIO plan from notes/parts-mcu.md (relay gates GPIO4-7, I2C GPIO20/21, button GPIO2, VSW GPIO10 - check deep-sleep hold); merge notes into notes/parts.md.
issues: hwflow lib show/pads do not load project libraries; hwflow parts crashes on package= filters and null stock, 404 on inductors/crystals.
risk: WiFi wake energy 0.5 J estimate (limit 0.52 J); crystal ESR at the 70 kohm limit; datasheet PDFs not text-checked (pdftotext/curl need approval).
