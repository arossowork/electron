"""Pure-Python parts of the optional openEMS stage (no openEMS needed)."""

import numpy as np

from hwflow import em


def test_cpwg_tends_to_microstrip_for_wide_gap():
    z_wide, _ = em.cpwg_z0(0.25, 5.0, 1.6, 4.3)
    z_ms, _ = em.microstrip(0.25, 1.6, 0.0, 4.3)
    assert abs(z_wide - z_ms) / z_ms < 0.05
    assert em.cpwg_z0(0.25, 0.2, 1.6, 4.3)[0] < 90      # tight pour pulls Z0 far below microstrip


def test_tdr_recovers_line_impedance():
    f = np.linspace(20e9 / 400, 20e9, 400)
    z, r, length = 90.0, 50.0, 11.6e-3
    b = 2 * np.pi * f * np.sqrt(3.2) / em.C0
    zin = z * (r + 1j * z * np.tan(b * length)) / (z + 1j * r * np.tan(b * length))
    _, dist, ztdr = em._tdr(f, (zin - r) / (zin + r), r, 3.2)
    mid = (dist > 4) & (dist < 8)
    assert abs(np.median(ztdr[mid]) - z) < 2
    assert abs(ztdr[dist > 25][0] - r) < 3
