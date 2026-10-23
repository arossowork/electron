"""Power budget, battery life and R3 operating points for 2 x AAA + TPS610994 (architecture round 4).

Run: uv run python projects/home-thermostat/notes/power_calc.py
Sources: Energizer E92 / L92 datasheets, TI SLVSD88M Fig 6-5 and 7.3.8, TI SLVSET3D (TPS63900).
"""
import math

V = 3.3
MO = 30.44 * 86400  # seconds per month

# Rail-side average currents (uA) over 24 h
wifi = 24 * 0.5 / 86400 / V * 1e6          # 24 wakes x 0.5 J
meas = 144 * 5.3e-3 / 86400 / V * 1e6      # 144 measurement wakes x 5.3 mJ
rel = 0.425 / 86400 / V * 1e6              # 40 pulses x 10.6 mJ + MCU light sleep
led = 0.10
act = wifi + meas + rel + led
# sleep floor at the rail: module 5.0, XTAL 0.5, C_buf leakage 1.0 (requirement), U6 0.15, IO3 leak 0.05
sleep = 5.0 + 0.5 + 1.0 + 0.15 + 0.05

EFF_ACT_ALK = 0.92   # Fig 6-5, VIN 2.5 V, 10-300 mA reads 93-94 %; 0.92 incl. FET/contacts
EFF_ACT_LI = 0.94    # boost at 3.0-3.25 V (95 %) / down mode 3.3/3.4 = 97 %
EFF_SLEEP = 0.75     # SLVSD88M p.1 "up to 75 % at 10 uA" (Fig 6-5 reads 80-84 %; text value used)


def cell_power(eff_act, eff_sl=EFF_SLEEP, sl=sleep):
    return act * V / eff_act + sl * V / eff_sl


def energy(ah, dod, derate, vavg):
    return ah * dod * derate * vavg * 3600


def eff_boost(vin):
    pts = [(0.7, 0.80), (1.0, 0.83), (1.5, 0.89), (2.5, 0.93)]
    if vin <= pts[0][0]:
        return pts[0][1]
    for (a, ea), (b, eb) in zip(pts, pts[1:]):
        if vin <= b:
            return ea + (eb - ea) * (vin - a) / (b - a)
    return pts[-1][1]


def operating_point(voc, r, pout):
    i = 0.3
    for _ in range(80):
        vin = voc - i * r
        pin = pout / eff_boost(vin)
        d = voc * voc - 4 * r * pin
        if d < 0:
            return None
        i = (voc - math.sqrt(d)) / (2 * r)
    return round(i, 3), round(voc - i * r, 3)


def main():
    print("rail uA: wifi %.2f meas %.2f relay %.2f led %.2f -> active %.2f; sleep %.2f; total %.2f"
          % (wifi, meas, rel, led, act, sleep, act + sleep))
    pa, pl = cell_power(EFF_ACT_ALK), cell_power(EFF_ACT_LI)
    print("alkaline: active %.1f uW + sleep %.1f uW = %.1f uW; pack current at 2.5 V %.1f uA"
          % (act * V / EFF_ACT_ALK, sleep * V / EFF_SLEEP, pa, pa / 2.5))
    print("lithium: %.1f uW; pack current at 3.2 V %.1f uA" % (pl, pl / 3.2))
    cases = [("alkaline typ (1.2 Ah, 85 % DoD, x0.90)", energy(1.2, 0.85, 0.90, 2.5), pa),
             ("alkaline worst (1.1 Ah, 80 % DoD, x0.90)", energy(1.1, 0.80, 0.90, 2.5), pa),
             ("lithium L92 (1.2 Ah, 100 %, x0.97, 3.2 V)", energy(1.2, 1.0, 0.97, 3.2), pl)]
    for name, e, p in cases:
        t = e / (p * 1e-6)
        print("%s: E %.0f J, life %.1f months = %.2f yr" % (name, e, t / MO, t / MO / 12))
    ew = energy(1.1, 0.80, 0.90, 2.5)
    print("R2 limit, 13 months on worst alkaline: %.1f uW (%.1f uA at 2.5 V)" % (ew / (13 * MO) * 1e6, ew / (13 * MO) * 1e6 / 2.5))
    el = energy(1.2, 1.0, 0.97, 3.2)
    print("R2 limit, 24 months lithium: %.1f uW" % (el / (24 * MO) * 1e6))
    et = energy(1.2, 0.85, 0.90, 2.5)
    for lab, sl, wifi_j in [("C_buf leak 3 uA", sleep + 2, 0.5), ("C_buf leak 5 uA", sleep + 4, 0.5),
                            ("sleep floor 11.7 uA", 11.7, 0.5), ("Wi-Fi wake 1 J (TLS)", sleep, 1.0)]:
        p = cell_power(EFF_ACT_ALK, sl=sl) + (wifi_j - 0.5) * 24 / 86400 * 1e6 / EFF_ACT_ALK
        print("  %s: %.1f uW, life typ %.1f / worst %.1f months" % (lab, p, et / (p * 1e-6) / MO, ew / (p * 1e-6) / MO))

    # R3 / R16 operating points, 2 cells in series, per-cell Rint 0.40 (25 C) / 0.80 (0 C), +0.07 ohm wiring and FET
    for temp, rc in [("25C", 0.40), ("0C", 0.80)]:
        for voc in (2.20, 2.54):
            r = 2 * rc + 0.07
            print("%s Voc %.2f R %.2f: Pmax %.3f W; Wi-Fi 0.495 W -> %s; relay R16 0.368 W -> %s"
                  % (temp, voc, r, voc * voc / 4 / r, operating_point(voc, r, 0.495), operating_point(voc, r, 0.368)))
            for lim in (0.65, 0.85, 1.10):
                vin = voc - lim * r
                if vin < 0.5:
                    print("   avg input limit %.2f A: VIN %.2f V -> below UVLO, converter stops" % (lim, vin))
                    continue
                pout = vin * lim * eff_boost(vin)
                deficit = max(0.0, (1.155 - pout) * 5e-3)
                esr = 0.15 if temp == "0C" else 0.07
                c_ideal = deficit / (0.5 * (3.3 ** 2 - 3.0 ** 2))
                c_esr = deficit / (0.5 * (3.3 ** 2 - (3.0 + 0.35 * esr) ** 2))
                print("   avg input limit %.2f A: VIN %.2f V, Pin %.3f W, Pout %.3f W, TX deficit %.2f mJ, C >= %.2f mF (%.2f mF with ESR)"
                      % (lim, vin, vin * lim, pout, deficit * 1e3, c_ideal * 1e3, c_esr * 1e3))
    # TPS63900 alternative: UVLO falling 1.63-1.67 V, no current limit between 100 mA and the 1.45 A switch limit
    for r in (0.87, 1.67):
        d = 2.2 ** 2 - 4 * r * 0.5625
        vin = 2.2 - (2.2 - math.sqrt(d)) / 2 if d >= 0 else None
        i = (2.2 - math.sqrt(d)) / (2 * r) if d >= 0 else None
        print("TPS63900 Wi-Fi at Voc 2.2 V, R %.2f: I %.3f A, VIN %.3f V; TX needs 1.36 W in, Pmax %.3f W"
              % (r, i, 2.2 - i * r, 2.2 ** 2 / 4 / r))
    print("sleep at cell: TPS610994 %.1f uW, TPS63900 (88 %%) %.1f uW, difference %.2f uA rail-equivalent"
          % (sleep * V / 0.75, sleep * V / 0.88, (sleep * V / 0.75 - sleep * V / 0.88) / V))


if __name__ == "__main__":
    main()
