"""Part lookup on the public JLCPCB in-stock index (jlcsearch.tscircuit.com, no API key)."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

BASE = "https://jlcsearch.tscircuit.com"
CATEGORIES = ["resistors", "capacitors", "inductors", "ldos", "boost_converters", "buck_converters", "diodes",
              "leds", "mosfets", "bjt_transistors", "microcontrollers", "opamps", "voltage_regulators",
              "usb_c_connectors", "headers", "switches", "oscillators", "crystals", "fuses", "adcs", "dacs",
              "wifi_modules", "accelerometers", "gyroscopes", "lcd_displays", "led_drivers", "battery_holders"]


def _get(path: str, params: dict) -> dict:
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"User-Agent": "hwflow/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def unit_price(row: dict) -> float | None:
    p = row.get("price1", row.get("price"))
    if isinstance(p, (int, float)):
        return float(p)
    if isinstance(p, str):
        m = re.search(r":([\d.]+)", p)
        return float(m.group(1)) if m else None
    return None


def search(category: str, filters: dict, text: str | None = None, limit: int = 15) -> list[dict]:
    """Category search with server-side filters, then optional client-side text filter."""
    path = f"{category}/list.json" if category != "any" else "components/list.json"
    data = _get(path, filters)
    rows = next((v for v in data.values() if isinstance(v, list)), [])
    out = []
    for r in rows:
        hay = f"{r.get('mfr', '')} {r.get('description', '')} {r.get('package', '')}".lower()
        if text and not all(t in hay for t in text.lower().split()):
            continue
        out.append({"lcsc": f"C{r.get('lcsc')}", "mpn": r.get("mfr"), "package": r.get("package"),
                    "stock": r.get("stock"), "price_usd": unit_price(r), "basic": r.get("is_basic"),
                    "desc": (r.get("description") or "")[:140]})
    out.sort(key=lambda r: (not r["basic"], -(r["stock"] or 0)))
    return out[:limit]
