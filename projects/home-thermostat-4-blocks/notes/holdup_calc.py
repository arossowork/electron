"""Bulk-capacitor sizing for R3 (350 mA, 5 ms Wi-Fi burst at VBAT = 1.8 V, 1 ohm source).

Model: constant-power boost input P_in = 3.3 V * 0.35 A / 0.85; battery = 1.8 V source + 1 ohm;
bulk capacitor C on the boost input. Euler integration over 5 ms. Prints the minimum input voltage.
"""

def vmin(c, p_in=3.3 * 0.35 / 0.85, voc=1.8, rs=1.0, t_end=5e-3, dt=1e-7):
    v = voc
    t = 0.0
    lo = v
    while t < t_end:
        i_load = p_in / v
        i_bat = (voc - v) / rs
        v += (i_bat - i_load) / c * dt
        if v < 0.3:
            return 0.0
        lo = min(lo, v)
        t += dt
    return lo


if __name__ == "__main__":
    for c_mf in (0.47, 1.0, 1.5, 2.2, 3.3, 4.7, 6.8):
        print(f"C = {c_mf:4.2f} mF -> Vin min = {vmin(c_mf * 1e-3):.3f} V, I_in at Vmin = "
              f"{(3.3 * 0.35 / 0.85) / max(vmin(c_mf * 1e-3), 1e-3):.2f} A")
