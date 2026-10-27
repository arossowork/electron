"""Grid search for F1 candidate footprints in the 230 V zone of home-thermostat-3.

usage: uv run python fit.py <proj> <fp_id> [<fp_id> ...]

A position (0.25 mm grid, rot 0/90/180/270, front and back face) is accepted only if
 - every F1 pad (AC_L_IN, AC_L) copper box lies in the mains region x <= 20 and inside the board,
 - each pad copper box is >= 6.0 mm from both notch keep-outs (circle r 4 at (2.25, 23) / (85.75, 23)),
   measured like hwflow floorplan.fixed_pad_isolation (pad bounding box to the 32-segment notch polygon),
 - each pad copper box is >= 6.0 mm from every SELV pad of every fixed part (coil pads included) and from the SELV
   region x >= 26 (architecture 6.3),
 - pad copper >= EDGE mm from the outer board edge,
 - the F1 courtyard on its face (with the placer margin 0.25) overlaps no courtyard of a fixed part on that face and no
   rule area that forbids footprints (notch keep-outs),
 - the F1 THT pads lie in no courtyard of a fixed part on either face (pins come through).
The other face's courtyard of the fixed parts is checked against the pads only.
"""
import fnmatch
import sys
from pathlib import Path

from shapely.geometry import Point, box
from shapely.ops import unary_union

from hwflow.design import Design
from hwflow.libs import Libs
from hwflow import placer
from hwflow.placer import rot

ISO = 6.0
EDGE = 1.0
# real courtyards (as hwflow floorplan: placer box minus COURTYARD_MARGIN); --placer-margin uses the placer boxes
PLACER = "--placer-margin" in sys.argv
MARGIN_FIXED = 0.0 if PLACER else placer.COURTYARD_MARGIN
MARGIN_F1 = placer.COURTYARD_MARGIN if PLACER else 0.0
MAINS_X = 20.0
proj = Path(sys.argv[1]).resolve()
fps = [a for a in sys.argv[2:] if not a.startswith('--')]
d = Design.load(proj / "design.yaml")
L = Libs(design=d)
parts = placer.build_parts(d, L)
W, H = float(d.raw["board"]["width_mm"]), float(d.raw["board"]["height_mm"])
notches = {"notch_l": Point(2.25, 23).buffer(4, 32), "notch_r": Point(85.75, 23).buffer(4, 32)}
selv_region = box(26, -10, 200, 100)

fixed = {r: p for r, p in parts.items() if p.fixed and r != "F1"}
selv_pads, yards = [], {"front": [], "back": []}
for r, p in fixed.items():
    c = d.components[r]
    for num, net in c.pins.items():
        if num not in p.pads or fnmatch.fnmatchcase(net, "AC_*"):
            continue
        x, y = p.pad(num)
        w, h = (p.pad_size or {}).get(num, (0, 0))
        if p.r % 180:
            w, h = h, w
        if w == 0:
            continue
        selv_pads.append((f"{r}.{num}", box(x - w / 2, y - h / 2, x + w / 2, y + h / 2)))
    for s in ("front", "back"):
        b = p.side_box(s)
        if b:
            m0 = MARGIN_FIXED
            yards[s].append((r, box(b[0] + m0, b[1] + m0, b[2] - m0, b[3] - m0)))
areas = [a for a in parts.areas if "footprints" in a["forbids"]]
print("fixed parts:", sorted(fixed), " SELV pads of fixed parts:", len(selv_pads), " footprint keep-outs:",
      [a["owner"] + ":" + a["name"] for a in areas])


def candidate(fp_id):
    fp = L.footprint(fp_id)
    pads = [(p["number"], p["x"], p["y"], p["w"], p["h"], p["rot"]) for p in fp["pads"] if p["number"]]
    sides = fp.get("sides", {})
    side = "front" if "front" in sides else "back"
    yb = sides[side]
    return fp, pads, side, yb


def place(fp_id, mirror_to_back=True):
    fp, pads, side, yb = candidate(fp_id)
    variants = [(side, False)]
    if mirror_to_back:
        variants.append(("back" if side == "front" else "front", True))
    found = []
    for face, mir in variants:
        for r in (0, 90, 180, 270):
            for ix in range(int(MAINS_X / 0.25) + 1):
                x = ix * 0.25
                for iy in range(int(H / 0.25) + 1):
                    y = iy * 0.25
                    res = check(pads, yb, face, mir, x, y, r)
                    if res:
                        found.append((face, r, x, y, res))
    return found


def check(pads, yb, face, mir, x, y, r):
    m = -1 if mir else 1
    boxes = []
    for num, px, py, w, h, pr in pads:
        dx, dy = rot(m * px, py, r)
        cx, cy = x + dx, y + dy
        if (pr + r) % 180:
            w, h = h, w
        g = box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        b = g.bounds
        if b[2] > MAINS_X or b[0] < EDGE or b[1] < EDGE or b[3] > H - EDGE:
            return None
        boxes.append((num, g))
    dn = min(g.distance(n) for _, g in boxes for n in notches.values())
    if dn < ISO:
        return None
    ds = min(g.distance(selv_region) for _, g in boxes)
    dp = min((g.distance(sp), n) for _, g in boxes for n, sp in selv_pads)
    if ds < ISO or dp[0] < ISO:
        return None
    x0, y0, x1, y1 = yb
    cx, cy = (m * (x0 + x1) / 2), (y0 + y1) / 2
    w, h = x1 - x0 + 2 * MARGIN_F1, y1 - y0 + 2 * MARGIN_F1
    ox, oy = rot(cx, cy, r)
    if r % 180:
        w, h = h, w
    yard = box(x + ox - w / 2, y + oy - h / 2, x + ox + w / 2, y + oy + h / 2)
    if yard.bounds[0] < 0 or yard.bounds[2] > W or yard.bounds[1] < 0 or yard.bounds[3] > H:
        return None
    for ref, yb2 in yards[face]:
        if yard.intersects(yb2) and yard.intersection(yb2).area > 1e-9:
            return None
    for a in areas:
        if yard.intersects(a["poly"]) and yard.intersection(a["poly"]).area > 1e-9:
            return None
    for s in ("front", "back"):
        for ref, yb2 in yards[s]:
            for _, g in boxes:
                if g.intersects(yb2) and g.intersection(yb2).area > 1e-9:
                    return None
    gap = min(yard.distance(yb2) for ref, yb2 in yards[face]) if yards[face] else 99
    return {"yard_gap": gap, "notch": dn, "selv_region": ds, "selv_pad": dp,
            "pads": {n: tuple(round(v, 2) for v in g.centroid.coords[0]) for n, g in boxes}}


for fp_id in fps:
    found = place(fp_id)
    print(f"\n== {fp_id}: {len(found)} free positions")
    if not found:
        continue
    by = {}
    for f in found:
        by.setdefault((f[0], f[1]), []).append(f)
    for k, v in sorted(by.items()):
        xs, ys = [f[2] for f in v], [f[3] for f in v]
        print(f"  face {k[0]:5s} rot {k[1]:3d}: {len(v):4d} positions, origin x {min(xs)}..{max(xs)}, y {min(ys)}..{max(ys)}")
    best = max(found, key=lambda f: (min(f[4]["notch"], f[4]["selv_pad"][0]), f[0] == "back"))
    f = best
    print(f"  best (max of min(notch, SELV pad) distance): face {f[0]} rot {f[1]} at ({f[2]}, {f[3]}): pads {f[4]['pads']}, "
          f"notch {f[4]['notch']:.2f} mm, SELV pad {f[4]['selv_pad'][0]:.2f} mm ({f[4]['selv_pad'][1]}), "
          f"SELV region {f[4]['selv_region']:.2f} mm, courtyard gap {f[4]['yard_gap']:.2f} mm")
    for f in found:
        if f[0] == "back" or "--list" in sys.argv:
            r_ = f[4]
            print(f"    {f[0]} rot {f[1]} ({f[2]}, {f[3]}): pads {r_['pads']} notch {r_['notch']:.2f} SELV pad {r_['selv_pad'][0]:.2f} "
                  f"({r_['selv_pad'][1]}) courtyard gap {r_['yard_gap']:.2f}")
