# C1: bulk capacitor on VBAT_P (hwflow index, package filter, 2026-10-20)

Chosen: 2 x RL1A332MG250A00CE0 (LCSC C240437): 3300 uF 10 V, 105 C 2000 h, D10 x L25 mm, 5 mm lead pitch, 0.1479 USD, stock 6502. Two give 6.6 mF nominal (5.3 mF at -20 %). Lying: height 10 mm plus lead bend.

Alternatives:
- 2 x RGA472M0JBK-1030G (C311679) 4700 uF 6.3 V D10 x L30, 0.2154, stock 967 (9.4 mF).
- RGA472M0JBK-1320G (C782984) 4700 uF 6.3 V D12.5 x L20, 0.2355, stock 983 (one part, 4.7 mF, 12.5 mm high).
- 10YXJ4700M12.5X25 (C5113044) 4700 uF 10 V D12.5 x L25, 0.4393, stock 2964.

6800 uF: only EEUFS0J682L (C407967, 6.3 V, D12.5 x L30) found, stock 0. Panasonic EEUHD1A682 (10 V, D12.5 x L25) exists at RS, not at LCSC.

Leakage: not in the index. The general-purpose spec is about 0.01 CV (330 uA at 10 V for 3300 uF); the real value at 3 V after hours is far lower but is not stated: bench measure (C7). The series datasheet was not read.
