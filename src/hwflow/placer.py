"""Simulated-annealing placement with net length, decoupling, grouping and edge constraints."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .design import Design


def rot(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad rotation (counter-clockwise on screen, y axis down)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


@dataclass
class Part:
    ref: str
    w: float          # courtyard size at rot 0 (with margin)
    h: float
    cx: float         # courtyard center offset from footprint origin at rot 0
    cy: float
    pads: dict        # pad number -> (x, y) at rot 0
    x: float = 0.0
    y: float = 0.0
    r: float = 0.0
    fixed: bool = False
    edge: str | None = None
    group: str | None = None
    decouples: str | None = None
    pad_size: dict | None = None  # pad number -> (w, h) at rot 0, for the copper-to-edge check

    def box(self):
        cx, cy = rot(self.cx, self.cy, self.r)
        w, h = (self.w, self.h) if self.r % 180 == 0 else (self.h, self.w)
        x, y = self.x + cx, self.y + cy
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2

    def pad(self, n):
        px, py = self.pads[n]
        dx, dy = rot(px, py, self.r)
        return self.x + dx, self.y + dy


def build_parts(design: Design, libs, margin: float = 0.25) -> dict[str, Part]:
    parts = {}
    for c in design.components.values():
        if c.get("virtual"):
            continue
        fp = libs.footprint(c.get("footprint"))
        x0, y0, x1, y1 = fp["bbox"]
        pads, sizes = {}, {}
        for p in fp["pads"]:
            if p["number"] and p["number"] not in pads:
                pads[p["number"]] = (p["x"], p["y"])
                w, h = (p["w"], p["h"]) if p["rot"] % 180 == 0 else (p["h"], p["w"])
                sizes[p["number"]] = (w, h)
        pl = c.get("placement") or {}
        part = Part(c.ref, x1 - x0 + 2 * margin, y1 - y0 + 2 * margin, (x0 + x1) / 2, (y0 + y1) / 2, pads,
                    edge=pl.get("edge"), group=c.get("group"), decouples=c.get("decouples"), pad_size=sizes)
        if "x" in pl and "y" in pl:
            part.x, part.y, part.r = float(pl["x"]), float(pl["y"]), float(pl.get("rot", 0))
            part.fixed = bool(pl.get("fixed", True))
        elif "rot" in pl:
            part.r = float(pl["rot"])
        parts[c.ref] = part
    return parts


def _overlap(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    return w * h if w > 0 and h > 0 else 0.0


class Placer:
    def __init__(self, design: Design, parts: dict[str, Part], width: float, height: float, seed: int = 1,
                 weights: dict | None = None):
        self.d, self.parts, self.W, self.H = design, parts, width, height
        self.rng = random.Random(seed)
        self.wt = {"wire": 1.0, "overlap": 400.0, "bounds": 400.0, "decouple": 6.0, "group": 0.6, "edge": 30.0}
        self.wt.update(weights or {})
        gnds = design.ground_nets()
        self.nets = []
        for net, conns in design.nets().items():
            conns = [(r, p) for r, p in conns if r in parts and p in parts[r].pads]
            if len(conns) < 2:
                continue
            info = design.net_info(net)
            w = 0.15 if net in gnds and design.board["gnd_pour"] else float(info.get("place_weight", 1.0))
            if info.get("class") == "power":
                w *= 1.3
            self.nets.append((w, conns))
        # decoupling pairs: (cap ref, cap pad, ic ref, ic pad) on the shared supply net
        self.decap = []
        for p in parts.values():
            if p.decouples and p.decouples in parts:
                cap = design.components[p.ref]
                ic = design.components[p.decouples]
                for cpin, net in cap.pins.items():
                    if net in gnds:
                        continue
                    for ipin, inet in ic.pins.items():
                        if inet == net and ipin in parts[ic.ref].pads:
                            self.decap.append((p.ref, cpin, ic.ref, ipin))

    def cost(self) -> float:
        P = self.parts
        c = 0.0
        for w, conns in self.nets:
            xs, ys = zip(*(P[r].pad(p) for r, p in conns))
            c += self.wt["wire"] * w * ((max(xs) - min(xs)) + (max(ys) - min(ys)))
        boxes = {r: p.box() for r, p in P.items()}
        refs = list(P)
        for i, a in enumerate(refs):
            ba = boxes[a]
            for b in refs[i + 1:]:
                c += self.wt["overlap"] * _overlap(ba, boxes[b])
            m = self.d.board["margin_mm"]
            x0, y0, x1, y1 = ba
            c += self.wt["bounds"] * (max(0, m - x0) + max(0, m - y0) + max(0, x1 - self.W + m) + max(0, y1 - self.H + m))
            e = P[a].edge
            if e:
                dist = {"left": x0 - m, "right": self.W - m - x1, "top": y0 - m, "bottom": self.H - m - y1}[e]
                c += self.wt["edge"] * abs(dist)
        for cr, cp, ir, ip in self.decap:
            ax, ay = P[cr].pad(cp)
            bx, by = P[ir].pad(ip)
            c += self.wt["decouple"] * math.hypot(ax - bx, ay - by)
        groups: dict[str, list] = {}
        for p in P.values():
            if p.group:
                groups.setdefault(p.group, []).append(p)
        for g in groups.values():
            mx = sum(p.x for p in g) / len(g)
            my = sum(p.y for p in g) / len(g)
            c += self.wt["group"] * sum(abs(p.x - mx) + abs(p.y - my) for p in g)
        return c

    def run(self, iters: int = 30000) -> float:
        P = self.parts
        movable = [p for p in P.values() if not p.fixed]
        for p in movable:
            p.x = self.rng.uniform(self.W * 0.2, self.W * 0.8)
            p.y = self.rng.uniform(self.H * 0.2, self.H * 0.8)
        if not movable:
            return self.cost()
        cur = self.cost()
        best, best_state = cur, self._state()
        t0, t1 = max(self.W, self.H) * 2, 0.01
        base_ov, base_b = self.wt["overlap"], self.wt["bounds"]
        for i in range(iters):
            t = t0 * (t1 / t0) ** (i / iters)
            if i % 500 == 0:
                ramp = 1 + 20 * i / iters
                self.wt["overlap"], self.wt["bounds"] = base_ov * ramp, base_b * ramp
                cur = self.cost()
                best, best_state = cur, self._state()  # weights changed: costs are not comparable
            p = self.rng.choice(movable)
            old = (p.x, p.y, p.r)
            mv = self.rng.random()
            other = None
            if mv < 0.75:
                s = max(0.3, t)
                p.x = min(self.W, max(0, p.x + self.rng.gauss(0, s)))
                p.y = min(self.H, max(0, p.y + self.rng.gauss(0, s)))
            elif mv < 0.9:
                p.r = (p.r + self.rng.choice((90, 180, 270))) % 360
            else:
                other = self.rng.choice(movable)
                oold = (other.x, other.y)
                p.x, p.y, other.x, other.y = other.x, other.y, p.x, p.y
            new = self.cost()
            if new <= cur or self.rng.random() < math.exp(-(new - cur) / max(t, 1e-9)):
                cur = new
                if cur < best:
                    best, best_state = cur, self._state()
            else:
                p.x, p.y, p.r = old
                if other:
                    other.x, other.y = oold
        self._restore(best_state)
        self.legalize()
        for p in movable:  # snap to 0.05 mm grid
            p.x, p.y = round(p.x / 0.05) * 0.05, round(p.y / 0.05) * 0.05
        self.legalize()
        return self.cost()

    def legalize(self, rounds: int = 200):
        """Push overlapping courtyards apart along the axis of least overlap, then clamp into the board."""
        P = [p for p in self.parts.values()]
        m = self.d.board["margin_mm"]
        for _ in range(rounds):
            moved = False
            for i, a in enumerate(P):
                for b in P[i + 1:]:
                    ba, bb = a.box(), b.box()
                    ox = min(ba[2], bb[2]) - max(ba[0], bb[0])
                    oy = min(ba[3], bb[3]) - max(ba[1], bb[1])
                    if ox <= 0.001 or oy <= 0.001:
                        continue
                    moved = True
                    mover = b if not b.fixed else a
                    sign_src = (bb[0] + bb[2] - ba[0] - ba[2]) if ox < oy else (bb[1] + bb[3] - ba[1] - ba[3])
                    sgn = 1 if sign_src >= 0 else -1
                    if mover is a:
                        sgn = -sgn
                    if a.fixed and b.fixed:
                        continue
                    if ox < oy:
                        mover.x += sgn * (ox + 0.05)
                    else:
                        mover.y += sgn * (oy + 0.05)
            for p in P:
                if p.fixed:
                    continue
                x0, y0, x1, y1 = p.box()
                if x0 < m:
                    p.x += m - x0
                if x1 > self.W - m:
                    p.x -= x1 - (self.W - m)
                if y0 < m:
                    p.y += m - y0
                if y1 > self.H - m:
                    p.y -= y1 - (self.H - m)
            if not moved:
                break

    def violations(self) -> list[str]:
        P = self.parts
        out = []
        refs = list(P)
        boxes = {r: P[r].box() for r in refs}
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if _overlap(boxes[a], boxes[b]) > 0.01:
                    out.append(f"courtyard overlap {a}/{b}")
            # copper must stay inside the board with the copper-to-edge clearance; a courtyard (body)
            # may overhang on purpose, for example a connector flush with the edge
            ce = float(self.d.board.get("copper_edge_mm", 0.3))
            part = P[a]
            for n in part.pads:
                px, py = part.pad(n)
                w, h = (part.pad_size or {}).get(n, (0.0, 0.0))
                if part.r % 180:
                    w, h = h, w
                if px - w / 2 < ce - 0.01 or py - h / 2 < ce - 0.01 or px + w / 2 > self.W - ce + 0.01 \
                        or py + h / 2 > self.H - ce + 0.01:
                    out.append(f"{a} pad {n} copper closer than {ce} mm to the board edge")
                    break
        return out

    def _state(self):
        return {r: (p.x, p.y, p.r) for r, p in self.parts.items()}

    def _restore(self, s):
        for r, (x, y, a) in s.items():
            self.parts[r].x, self.parts[r].y, self.parts[r].r = x, y, a


def place(design: Design, libs, seeds: int = 4, iters: int = 30000, weights=None):
    """Best of several seeds. Returns (parts, cost, violations)."""
    b = design.board
    best = None
    for seed in range(1, seeds + 1):
        parts = build_parts(design, libs)
        pl = Placer(design, parts, b["width_mm"], b["height_mm"], seed=seed, weights=weights)
        cost = pl.run(iters)
        v = pl.violations()
        key = (len(v), cost)
        if best is None or key < best[0]:
            best = (key, parts, cost, v)
    return best[1], best[2], best[3]
