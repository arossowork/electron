"""Stage 1 battery-budget arithmetic for conflicts.md (C1). Run: python3 -I notes/power_budget.py"""
H = 8760  # hours per year
# name, capacity Ah (low drain to 0.9 V/cell), mean cell V, cells, converter efficiency
cases = [("1xAAA alk", 1.1, 1.2, 1, 0.80), ("1xAAA Li", 1.2, 1.5, 1, 0.82),
         ("2xAAA alk", 1.1, 1.2, 2, 0.85), ("1xAA alk", 2.5, 1.2, 1, 0.80),
         ("2xAA alk", 2.5, 1.2, 2, 0.85)]
DERATE = 0.8                     # self-discharge, cold, end-of-life cut-off
floor = 10e-6 * 3.3              # 10 uA at 3.3 V: MCU deep sleep 5 uA + RTC + converter Iq + leakage
samp = 720 * 3.3e-3 / 86400      # probe read every 2 min, 3.3 mJ per wake (25 mA, 40 ms, 3.3 V)
relay = 20 * 25e-3 / 86400       # 20 latching pulses/day, 25 mJ each (400 mW x 50 ms / 0.8)
print("floor uW %.1f  sampling uW %.1f  relay uW %.1f" % (floor * 1e6, samp * 1e6, relay * 1e6))
for k, ah, v, n, eff in cases:
    wh = ah * v * n
    P = wh * eff * DERATE / H
    spare = P - floor - samp - relay
    for E in (0.3, 0.5, 1.0):
        w = spare * 86400 / E
        print("%s: Wh=%.2f Pavg=%.0fuW I@3.3V=%.0fuA spare=%.0fuW E=%.1fJ wakes/day=%.1f interval=%.0fmin"
              % (k, wh, P * 1e6, P / 3.3 * 1e6, spare * 1e6, E, w, 1440 / w if w > 0 else 0))
for iv in (5, 15, 30, 60):
    w = 1440 / iv
    P = w * 0.5 / 86400 + floor + samp + relay
    print("%d min interval: rail %.0f uW, battery %.2f Wh/yr" % (iv, P * 1e6, P * H / 0.85 / DERATE))
print("always-on WiFi (2 mA avg) Wh/yr %.1f" % (2e-3 * 3.3 * H / 0.85))
e = 2 * 1.5e-3 * 3.3 * 0.75 + 0.13e-3 * 3.3 * 0.75
print("2x DS18B20 12-bit per read mJ %.2f, at 2 min = %.0f uW" % (e * 1e3, e * 720 / 86400 * 1e6))
for vin in (1.0, 1.2, 2.0, 2.4):
    print("Vin %.1f V: input current at 350 mA/3.3 V TX = %.2f A" % (vin, 0.35 * 3.3 / 0.85 / vin))
print("buffer cap for 350 mA, 5 ms, 0.3 V droop: %.1f mF" % (0.35 * 5e-3 / 0.3 * 1e3))
# Round 1 check (PM q1: 1 x AA, hourly check-in + button check-ins), conflicts.md C1 status
P_aa = 2.5 * 1.2 * 0.80 * DERATE / H
over = floor + samp + relay
for wakes, E in ((24, 0.5), (26, 0.5), (26, 0.4), (26, 0.3)):
    tot = over + wakes * E / 86400
    print("1xAA %d wakes/day at %.1f J: %.1f uW (%.1f uA), margin %.1f %%"
          % (wakes, E, tot * 1e6, tot / 3.3 * 1e6, (P_aa - tot) / P_aa * 100))
print("break-even wake energy at 26 wakes/day: %.2f J" % ((P_aa - over) * 86400 / 26))
# Round 2 check (PM q4: cost first, >= 10 months accepted), requirement R2
H10 = 10 * 730                   # 10 months in hours
P10 = 2.5 * 1.2 * 0.80 * DERATE / H10
lim = 0.85 * P10                 # 15 % margin
print("10 months: usable %.1f uW (%.1f uA), limit %.1f uW, wake limit %.3f J, break-even %.3f J"
      % (P10 * 1e6, P10 / 3.3 * 1e6, lim * 1e6, (lim - over) * 86400 / 26, (P10 - over) * 86400 / 26))
for E in (0.4, 0.5):
    tot = over + 26 * E / 86400
    print("26 wakes at %.1f J: %.1f uW, margin %.1f %%, life %.1f months"
          % (E, tot * 1e6, (P10 - tot) / P10 * 100, 2.5 * 1.2 * 0.8 * DERATE / tot / 730))
