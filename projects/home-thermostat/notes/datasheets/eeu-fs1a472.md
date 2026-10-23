# C1: Panasonic EEU-FS1A472 (FS series, 4700 uF 10 V, 12.5 x 25 mm radial, 5 mm pitch)

NOT read from the Panasonic datasheet (LCSC PDF C407972 and Panasonic pages returned no text). Facts below are from the LCSC product page, DigiKey, Futureelectronics, RS and a search-engine summary of the series datasheet. Treat as unverified until the PDF is read.

| Item | Value | Source |
|---|---|---|
| Size | 12.5 mm dia x 25 mm long, lead pitch 5.0 mm (existing footprint CP_Radial_D12.5mm_P5.00mm fits) | LCSC, DigiKey |
| ESR / impedance | 15 mOhm at 100 kHz, ripple 3.19 A at 100 kHz; tan delta 0.19 (120 Hz, 20 C) -> ESR at 120 Hz = 0.19 / (2 pi 120 4.7e-3) = 0.054 ohm | LCSC, DigiKey |
| Life | 10,000 h at 105 C | LCSC |
| Leakage (spec) | 0.01 CV or 3 uA, whichever is greater, after 2 min at RATED voltage: 0.01 x 4700 x 10 = 470 uA max. Series datasheet text not seen. | search summary, same rule as the Rubycon YXJ and RS PRO parts |
| Leakage at 3.3 V | NOT specified. Pro-rata 0.01 CV at 3.3 V would be 155 uA at 2 min, and after 24 h it falls by one to two orders of magnitude, typically 0.1-2 uA, but no datasheet bound. No electrolytic series found has a guaranteed leakage <= 1 uA at 33 % rated voltage after 24 h. | calculation |
| Price / stock | LCSC C407972: $0.8492 at qty 10, stock 0 (out of stock). DigiKey EEU-FS1A472B: $1.06 at qty 10 ($0.79 at 100), 1,500 in stock; bulk EEU-FS1A472 $1.17 at qty 1, 2,281 in stock | LCSC, DigiKey |

Alternatives: Rubycon 10YXJ4700M12.5X25 (YXJ series, 0.01 CV or 3 uA, 8,000 h, impedance 32 mOhm, DigiKey $0.87 at qty 10 but out of stock, 24-week lead time as of 2026-10); Panasonic EEU-FM1A472L (12.5 x 35 mm, 12 mOhm, DigiKey stock not read, too long); Nichicon UVK1A472MHD (12.5 x 25, leakage 3 uA / 0.01 CV).
Decision on leakage: no part meets "<= 1 uA at 3.3 V after 24 h" by datasheet. The bound is by measurement (3 samples, bench item in state.md). The model uses a 1.1 Mohm shunt (3 uA at 3.3 V) as the pessimistic case.
ESR in the design: 0.10 ohm at 0 C (about twice the 20 C value), under the 0.10 ohm requirement at 20 C by 2 x.
