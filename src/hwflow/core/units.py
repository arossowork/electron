"""SI-suffixed values and preferred-value series: '4k7' -> 4700, 'E24' snapping."""

from __future__ import annotations

import math
import re

_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "": 1.0,
       "k": 1e3, "K": 1e3, "meg": 1e6, "M": 1e6, "g": 1e9, "G": 1e9}


def parse_value(v) -> float:
    """'4k7' -> 4700, '100n' -> 1e-7, '2.2meg' -> 2.2e6, 10 -> 10."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "")
    s = re.sub(r"(ohm|Ω|F|H|V|A|W|Hz)$", "", s, flags=re.I) or s
    m = re.fullmatch(r"(\d+)([pnuµmkKMgG]|meg|R)(\d+)", s)  # 4k7, 4R7
    if m:
        mult = 1.0 if m.group(2) == "R" else _SI[m.group(2)]
        return float(f"{m.group(1)}.{m.group(3)}") * mult
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)(meg|[fpnuµmkKMgG]?)", s)
    if not m:
        raise ValueError(f"cannot parse value {v!r}")
    return float(m.group(1)) * _SI[m.group(2)]


def fmt_value(x: float, unit: str = "") -> str:
    if x == 0:
        return f"0{unit}"
    for suf, mult in (("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if abs(x) >= mult * 0.9999:
            s = f"{x / mult:.3g}"
            return f"{s}{suf}{unit}"
    return f"{x:.3g}{unit}"


E_SERIES = {
    "E6": [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1,
            5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
}
E_SERIES["E96"] = [round(10 ** (i / 96), 2) for i in range(96)]


def snap_e(x: float, series: str = "E24") -> float:
    dec = math.floor(math.log10(x))
    base = x / 10 ** dec
    vals = E_SERIES[series] + [10.0]
    best = min(vals, key=lambda v: abs(math.log(v / base)))
    return best * 10 ** dec
