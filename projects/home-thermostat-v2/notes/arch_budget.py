"""Stage 2 architecture budgets (architecture.md). Run: python3 -I notes/arch_budget.py"""
from math import log, sqrt
V = 3.3
# ---------- sleep current at 3.3 V (uA) ----------
sleep = {"ESP32-C3 deep sleep": 5.0, "32k XTAL osc allowance": 1.0,
         "TPS610995 Iq (0.6 VOUT + 0.4 VIN x 1.2/3.3)": 0.6 + 0.4 * 1.2 / 3.3,
         "bulk cap leakage (measured target)": 2.0, "VBAT divider + pin leakage": 0.05,
         "4 relay FETs Idss": 0.05, "button ESD diode": 0.05}
I_sl = sum(sleep.values())
print("sleep uA %.2f" % I_sl)
# ---------- energy per event at the rail ----------
E_samp = 25e-3 * V * 0.040 + 0.29e-3 * V * 0.3 + 0.13e-3 * V * 0.3 + 25e-3 * V * 0.005  # boot+run, excitation, light sleep settle, re-wake
E_wifi = 0.5
I_coil = 3.23 / (22.5 + 1.0)
E_relay = V * I_coil * 0.030
E_led_day = 2e-3 * V * 35
rail = {"sleep": I_sl * 1e-6 * V, "sampling": 720 * E_samp / 86400, "wifi": 26 * E_wifi / 86400,
        "relay": 20 * E_relay / 86400, "led": E_led_day / 86400}
eff = {"sleep": 0.70, "sampling": 0.85, "wifi": 0.85, "relay": 0.85, "led": 0.85}
cell = {k: rail[k] / eff[k] for k in rail}
for k in rail:
    print("%-9s rail %.1f uW  cell %.1f uW" % (k, rail[k] * 1e6, cell[k] * 1e6))
R, C = sum(rail.values()), sum(cell.values())
usable_cell = 3.0 * 0.8 / 7300
print("E_samp %.2f mJ  E_relay %.1f mJ" % (E_samp * 1e3, E_relay * 1e3))
print("rail %.1f uW, cell %.1f uW, rail-equiv x0.8 %.1f uW (limit 223.6), life %.0f h = %.1f months"
      % (R * 1e6, C * 1e6, C * 0.8 * 1e6, 2.4 / C, 2.4 / C / 730))
print("max sampling energy for limit: %.2f mJ" % (((223.6e-6 / 0.8) - (C - cell["sampling"])) * 0.85 * 86400 / 720 * 1e3))
# ---------- boost capability, buffer (R1, R4, R7, R8) ----------
def iout(emf, rs, iin=0.6, e=0.80):
    vin = emf - iin * rs
    return vin * iin * e / V
for emf in (1.0, 0.9):
    print("EMF %.1f V, 0.5 ohm: boost Iout max %.0f mA" % (emf, iout(emf, 0.5) * 1e3))
Ceff = 8.2e-3 * 0.8
dq = (0.350 - iout(1.0, 0.5)) * 5e-3
print("R4: deficit %.2f mC, dV %.3f V + ESR %.3f V -> rail %.3f V (from 3.234)"
      % (dq * 1e3, dq / Ceff, (0.350 - iout(1.0, 0.5)) * 0.06, 3.234 - dq / Ceff - (0.350 - iout(1.0, 0.5)) * 0.06))
dq = (I_coil + 0.13e-3 - iout(0.9, 0.5)) * 0.030
print("R7/R8 at 0.9 V: deficit %.2f mC, dV %.3f V -> rail %.3f V, coil %.2f V"
      % (dq * 1e3, dq / Ceff, 3.234 - dq / Ceff, (3.234 - dq / Ceff) * 22.5 / 23.5))
print("R1: Iin at 350 mA DC from 0.9 V = %.2f A" % (0.35 * V / 0.85 / 0.9))
print("buffer usable 3.3->3.0 V: %.1f mJ" % (0.5 * Ceff * (3.3 ** 2 - 3.0 ** 2) * 1e3))
# ---------- NTC error budget (R14) ----------
B, T25, Rref = 3950.0, 298.15, 10e3
for tc in (10, 20, 30):
    T = tc + 273.15
    Rn = 10e3 * 2.718281828 ** (B * (1 / T - 1 / T25))
    a = B / T ** 2
    P = 3.3; N = P * Rn / (Rn + Rref)
    kN = P / (N * (P - N)); kP = 1 / (P - N)
    e_ntc = 0.01 / a
    e_B = abs(0.01 * B * (1 / T - 1 / T25)) / a
    e_ref = (0.001 + 25e-6 * 15) / a
    lsb = 4.096 / 32768
    e_adc = (kN * 2 * lsb + kP * 2 * lsb + abs(kN - kP) * 3 * lsb) / a
    e_cab = (1.0 + 1.0) / Rn / a  # 2 x 0.5 ohm cable + 1 ohm tolerance of 100 ohm series R
    e_leak = 0.1e-6 * (Rn * Rref / (Rn + Rref)) * kN / a
    e_heat = N ** 2 / Rn / 1.5e-3 * 0.3 / 10
    parts = [e_ntc, e_B, e_ref, e_adc, e_cab, e_leak, e_heat]
    print("%d C: Rntc %.0f N %.3f V; ntc %.3f B %.3f ref %.3f adc %.3f cable %.4f leak %.3f heat %.4f -> worst %.3f RSS %.3f"
          % (tc, Rn, N, *parts, sum(parts), sqrt(sum(p * p for p in parts))))
    # internal ESP ADC alternative: nodes <= 1.3 V (47k series), +/-10 mV per reading
    I = 3.3 / (47e3 + Rref + Rn); Pp = I * (Rref + Rn); Nn = I * Rn
    print("   ESP ADC alt: P %.3f N %.3f -> %.2f C worst" % (Pp, Nn, (Pp / (Nn * (Pp - Nn)) + 1 / (Pp - Nn)) * 0.010 / a))
    print("   resolution %.4f C/LSB" % (lsb / (P * Rref * Rn / (Rref + Rn) ** 2 * a)))
# ---------- battery voltage (R5) ----------
k = 100 / 133
print("R5: k %.4f, Vnode max %.3f; ADC %.1f + div %.1f + tcr %.1f + leak %.1f + noise 0.5 = %.1f mV"
      % (k, 1.65 * k, 10 / k, 1.65 * (1 - k) * 0.002 * 1e3, 1.65 * (1 - k) * 2 * 25e-6 * 15 * 1e3,
         50e-9 * (33e3 * 100e3 / 133e3) / k * 1e3,
         10 / k + 1.65 * (1 - k) * 0.002e3 + 1.65 * (1 - k) * 2 * 25e-6 * 15e3 + 50e-9 * 24.8e3 / k * 1e3 + 0.5))
