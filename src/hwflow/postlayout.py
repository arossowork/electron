"""Post-layout extraction (R/L/C of routed copper), back-annotated SPICE, and layout checks."""

from __future__ import annotations

import heapq
import math
import re
from pathlib import Path

import numpy as np

from . import sexpr, spice
from .design import Design
from .pcb import ipc2221_width_mm, net_classes, read_board
from .spice import NodeMap

RHO_CU = 1.72e-8
C0 = 299792458.0


def microstrip(w_mm: float, h_mm: float, t_mm: float, er: float) -> tuple[float, float]:
    """Return (Z0 ohm, effective er) using Hammerstad-Jensen for a surface microstrip."""
    u = w_mm / h_mm
    # thickness correction
    if t_mm > 0:
        du = t_mm / (math.pi * h_mm) * math.log(1 + 4 * math.e / (t_mm / h_mm * (1 / math.tanh(math.sqrt(6.517 * u))) ** 2))
        u = u + du * (1 + 1 / math.cosh(math.sqrt(er - 1))) / 2
    a = 1 + 1 / 49 * math.log((u ** 4 + (u / 52) ** 2) / (u ** 4 + 0.432)) + 1 / 18.7 * math.log(1 + (u / 18.1) ** 3)
    b = 0.564 * ((er - 0.9) / (er + 3)) ** 0.053
    ee = (er + 1) / 2 + (er - 1) / 2 * (1 + 10 / u) ** (-a * b)
    f = 6 + (2 * math.pi - 6) * math.exp(-(30.666 / u) ** 0.7528)
    z0_air = 60 * math.log(f / u + math.sqrt(1 + (2 / u) ** 2))
    return z0_air / math.sqrt(ee), ee


def _ellipk(k: float) -> float:
    """Complete elliptic integral of the first kind K(k) (modulus k) by the AGM."""
    a, b = 1.0, math.sqrt(1 - k * k)
    while abs(a - b) > 1e-15:
        a, b = (a + b) / 2, math.sqrt(a * b)
    return math.pi / (2 * a)


def cpwg_z0(w_mm: float, gap_mm: float, h_mm: float, er: float) -> tuple[float, float]:
    """Grounded coplanar waveguide (trace in a same-layer pour, plane below), zero thickness.

    Conformal mapping (Wadell, Transmission Line Design Handbook 3.4.3). This is what a routed
    trace inside a GND pour really is; `postlayout.microstrip()` ignores the same-layer pour.
    """
    k = w_mm / (w_mm + 2 * gap_mm)
    k1 = math.tanh(math.pi * w_mm / (4 * h_mm)) / math.tanh(math.pi * (w_mm + 2 * gap_mm) / (4 * h_mm))
    r = _ellipk(k) / _ellipk(math.sqrt(1 - k * k))
    r1 = _ellipk(k1) / _ellipk(math.sqrt(1 - k1 * k1))
    ee = (1 + er * r1 / r) / (1 + r1 / r)
    return 60 * math.pi / math.sqrt(ee) / (r + r1), ee


def _zone_fills(pcb: Path) -> list[dict]:
    """Filled zone polygons (KiCad stores them fractured: one outline, holes joined by slits)."""
    t = sexpr.loads(pcb.read_text())
    out = []
    for z in sexpr.find_all(t, "zone"):
        net = sexpr.value(z, "net_name")
        if net is None:                   # KiCad 10: (net "GND"); older: (net 3) (net_name "GND")
            n = sexpr.find(z, "net")
            net = n[1] if n is not None and len(n) > 1 else None
        for fp in sexpr.find_all(z, "filled_polygon"):
            layer = str(sexpr.value(fp, "layer"))
            pts = [(float(p[1]), float(p[2])) for p in sexpr.find_all(sexpr.find(fp, "pts"), "xy")]
            if len(pts) >= 3:
                out.append({"net": str(net), "layer": layer, "pts": np.array(pts).T})
    return out


def _edge_dist(px: float, py: float, poly: np.ndarray) -> float:
    """Distance from a point to the nearest edge of a (fractured) polygon outline, slits excluded."""
    a = poly.T
    b = np.roll(a, -1, axis=0)
    d = b - a
    L2 = (d ** 2).sum(1)
    ok = L2 > 1e-8
    a, d, L2 = a[ok], d[ok], L2[ok]
    t = np.clip(((px - a[:, 0]) * d[:, 0] + (py - a[:, 1]) * d[:, 1]) / L2, 0, 1)
    q = a + d * t[:, None]
    return float(np.min(np.hypot(q[:, 0] - px, q[:, 1] - py)))


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _on_segment(px, py, s, tol):
    x1, y1, x2, y2 = s["x1"], s["y1"], s["x2"], s["y2"]
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return None
    t = ((px - x1) * dx + (py - y1) * dy) / L2
    if t <= 1e-6 or t >= 1 - 1e-6:
        return None
    qx, qy = x1 + t * dx, y1 + t * dy
    return t if math.hypot(px - qx, py - qy) <= tol else None


def _split_tjunctions(segs):
    """Split segments where another segment's endpoint touches their interior."""
    changed = True
    while changed:
        changed = False
        for s in list(segs):
            for o in segs:
                if o is s or o["layer"] != s["layer"] or o["net"] != s["net"]:
                    continue
                for px, py in ((o["x1"], o["y1"]), (o["x2"], o["y2"])):
                    t = _on_segment(px, py, s, max(s["w"], o["w"]) / 2)
                    if t is not None:
                        segs.remove(s)
                        segs.append(dict(s, x2=px, y2=py))
                        segs.append(dict(s, x1=px, y1=py))
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
    return segs


class Extraction:
    """Copper graph per net. Nodes: ('p', x, y, layer) points, ('pad', ref, num)."""

    def __init__(self, design: Design, board_path: Path):
        self.d = design
        self.b = design.board
        self.path = Path(board_path)
        self.board = read_board(board_path)
        self.grounds = design.ground_nets()
        self.t_mm = 0.035 * self.b["copper_oz"]
        self.h_mm = float(self.b.get("ref_plane_h_mm", 0.2 if self.b["layers"] == 4 else self.b["thickness_mm"]))
        self.er = float(self.b["er"])
        self.uf = UF()
        self.elements = []    # (kind, a, b, value, info)
        self.segs = []
        self.adj: dict = {}
        self._ids: dict = {}
        self._build()

    def _key(self, x, y, layer):
        return ("p", round(x, 3), round(y, 3), layer)

    def _build(self):
        """Copper graph using overlap rules like KiCad: trace ends, via rings and pads connect when copper touches."""
        b = self.board
        layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"] if self.b["layers"] == 4 else ["F.Cu", "B.Cu"]
        via_r = self.b["via_dia_mm"] / 2
        segs = [dict(s) for s in b["segments"] if s["net"]]
        # 1. split traces where a via ring sits on their interior, so the via has a node to attach to
        for v in b["vias"]:
            changed = True
            while changed:
                changed = False
                for s in segs:
                    if s["net"] != v["net"]:
                        continue
                    t = _on_segment(v["x"], v["y"], s, s["w"] / 2 + via_r)
                    if t is None:
                        continue
                    px, py = s["x1"] + t * (s["x2"] - s["x1"]), s["y1"] + t * (s["y2"] - s["y1"])
                    if min(math.hypot(px - s["x1"], py - s["y1"]), math.hypot(px - s["x2"], py - s["y2"])) < 1e-3:
                        continue
                    segs.remove(s)
                    segs += [dict(s, x2=px, y2=py), dict(s, x1=px, y1=py)]
                    changed = True
                    break
        segs = _split_tjunctions(segs)
        self.segs = segs
        fills = [z for z in _zone_fills(self.path) if z["net"] in self.grounds] if self.path else []
        for s in segs:
            a, c = self._key(s["x1"], s["y1"], s["layer"]), self._key(s["x2"], s["y2"], s["layer"])
            L = math.hypot(s["x2"] - s["x1"], s["y2"] - s["y1"])
            s["len"] = L
            if L < 1e-6:
                self.uf.union(a, c)
                continue
            z0, ee = microstrip(s["w"], self.h_mm, self.t_mm, self.er)
            s["line"] = "microstrip"
            mx, my = (s["x1"] + s["x2"]) / 2, (s["y1"] + s["y2"]) / 2
            same = [_edge_dist(mx, my, z["pts"]) for z in fills if z["layer"] == s["layer"]]
            if same:
                gap = max(min(same) - s["w"] / 2, 0.05)
                if gap < 2 * self.h_mm:  # the same-layer pour couples: grounded coplanar waveguide
                    z0, ee = cpwg_z0(s["w"], gap, self.h_mm, self.er)
                    s["line"], s["gap"] = "cpwg", round(gap, 3)
            td = math.sqrt(ee) / C0 * L * 1e-3
            s["z0"] = z0
            R = RHO_CU * (L * 1e-3) / (s["w"] * 1e-3 * self.t_mm * 1e-3)
            self.elements.append({"net": s["net"], "a": a, "b": c, "R": R, "L": td * z0, "C": td / z0, "len": L,
                                  "Z0": z0})
        # 2. trace ends that touch (closer than half the narrower width) are one node
        ends = []
        for s in segs:
            for x, y in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
                ends.append((s["net"], s["layer"], x, y, s["w"], self._key(x, y, s["layer"])))
        for i, e1 in enumerate(ends):
            for e2 in ends[i + 1:]:
                if e1[0] == e2[0] and e1[1] == e2[1] and math.hypot(e1[2] - e2[2], e1[3] - e2[3]) <= min(e1[4], e2[4]) / 2:
                    self.uf.union(e1[5], e2[5])
        # 3. vias: a barrel joins its own layer nodes; any trace end inside the ring joins the via
        drill = self.b["via_drill_mm"]
        hv = self.b["thickness_mm"]
        r_via = RHO_CU * hv * 1e-3 / (math.pi * drill * 1e-3 * 25e-6)
        h_in, d_in = hv / 25.4, drill / 25.4
        l_via = 5.08e-9 * h_in * (math.log(4 * h_in / d_in) + 1)
        for v in b["vias"]:
            nodes = [self._key(v["x"], v["y"], L) for L in layers]
            for n1, n2 in zip(nodes, nodes[1:]):
                self.elements.append({"net": v["net"], "a": n1, "b": n2, "R": r_via, "L": l_via, "C": 0, "len": 0})
            for net, layer, x, y, w, key in ends:
                if net == v["net"] and math.hypot(x - v["x"], y - v["y"]) <= via_r + w / 2:
                    self.uf.union(key, self._key(v["x"], v["y"], layer))
        # 4. pads: a trace end whose copper (half width) reaches the pad rectangle joins the pad
        self.pad_nodes = {}
        for ref, fp in b["footprints"].items():
            for p in fp["pads"]:
                if not p["number"] or not p["net"]:
                    continue
                pn = ("pad", ref, p["number"])
                self.uf.find(pn)
                self.pad_nodes[(ref, p["number"])] = pn
                cu = layers if "*.Cu" in p["layers"] else [l for l in p["layers"] if l.endswith(".Cu")]
                for net, layer, x, y, w, key in ends:
                    if net == p["net"] and layer in cu and abs(x - p["x"]) <= p["w"] / 2 + w / 2 \
                            and abs(y - p["y"]) <= p["h"] / 2 + w / 2:
                        self.uf.union(pn, key)
                if p["kind"] == "thru_hole":
                    for v_layer in cu:
                        for v in b["vias"]:
                            if v["net"] == p["net"] and abs(v["x"] - p["x"]) <= p["w"] / 2 and abs(v["y"] - p["y"]) <= p["h"] / 2:
                                self.uf.union(pn, self._key(v["x"], v["y"], v_layer))
        for e in self.elements:
            ra, rb = self.uf.find(e["a"]), self.uf.find(e["b"])
            self.adj.setdefault(ra, []).append((rb, e))
            self.adj.setdefault(rb, []).append((ra, e))

    # ------------------------------------------------------------ connectivity
    def islands(self) -> dict[str, int]:
        """net -> number of separate copper islands holding its pads (1 means fully routed)."""
        out = {}
        for net, conns in self.d.nets().items():
            if net in self.grounds and self.b["gnd_pour"]:
                continue
            roots = set()
            for ref, pin in conns:
                pn = self.pad_nodes.get((ref, pin))
                if pn is None:
                    continue
                roots.add(self._component(pn))
            out[net] = len(roots)
        return out

    def _component(self, node):
        seen, stack = {self.uf.find(node)}, [self.uf.find(node)]
        while stack:
            n = stack.pop()
            for m, _ in self.adj.get(n, []):
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        return min(str(s) for s in seen)

    def path_len(self, a, b) -> float | None:
        a, b = self.uf.find(a), self.uf.find(b)
        dist = {a: 0.0}
        pq = [(0.0, str(a), a)]
        while pq:
            d, _, n = heapq.heappop(pq)
            if n == b:
                return d
            if d > dist.get(n, 1e18):
                continue
            for m, e in self.adj.get(n, []):
                nd = d + e["len"]
                if nd < dist.get(m, 1e18):
                    dist[m] = nd
                    heapq.heappush(pq, (nd, str(m), m))
        return None

    # ------------------------------------------------------------ SPICE node naming
    def node_name(self, n) -> str:
        r = self.uf.find(n)
        if r[0] == "pad":
            return f"x_{r[1]}_{r[2]}".lower()
        if r not in self._ids:
            self._ids[r] = f"x_n{len(self._ids)}"
        return self._ids[r]


class PostLayoutNodeMap(NodeMap):
    def __init__(self, design: Design, ex: Extraction, with_l: bool = True):
        super().__init__(design)
        self.ex = ex
        self.with_l = with_l

    def _is_ideal(self, net):
        return net in self.grounds and self.d.board["gnd_pour"]

    def pin(self, ref, pin):
        net = self.d.components[ref].pins.get(pin)
        if net is None or net.upper() in ("NC", "~"):
            return f"nc_{ref}_{pin}".lower()
        if self._is_ideal(net):
            return "0"
        pn = self.ex.pad_nodes.get((ref, pin))
        if pn is None or not self.ex.adj.get(self.ex.uf.find(pn)):
            return spice.sanitize(net, self.grounds)  # zone-only or unrouted: fall back to ideal net node
        return self.ex.node_name(pn)

    def net(self, net):
        if self._is_ideal(net):
            return "0"
        conns = self.d.nets().get(net, [])
        conns = sorted(conns, key=lambda rp: (not rp[0].startswith("J"), rp[0]))
        return self.pin(*conns[0]) if conns else spice.sanitize(net, self.grounds)

    def extra(self):
        lines = ["* --- post-layout parasitics ---"]
        for i, e in enumerate(self.ex.elements):
            if self._is_ideal(e["net"]):
                continue
            a, b = self.ex.node_name(e["a"]), self.ex.node_name(e["b"])
            if a == b:
                continue
            if self.with_l and e["L"] > 0:
                mid = f"m{i}"
                lines.append(f"Rpx{i} {a} {mid} {max(e['R'], 1e-6):.6g}")
                lines.append(f"Lpx{i} {mid} {b} {e['L']:.6g}")
                # damping across the trace inductance: kills the GHz LC ringing of nH/fF segments,
                # which made switching transients stall, and leaves behaviour below ~1 GHz unchanged
                lines.append(f"Rdx{i} {mid} {b} {e.get('Z0', 50):.6g}")
            else:
                lines.append(f"Rpx{i} {a} {b} {max(e['R'], 1e-6):.6g}")
            if e["C"] > 0:
                lines.append(f"Cpxa{i} {a} 0 {e['C'] / 2:.6g}")
                lines.append(f"Cpxb{i} {b} 0 {e['C'] / 2:.6g}")
        # tie unrouted nets' fallback node to every pad that the fallback serves is implicit (same name)
        return lines


# ------------------------------------------------------------ checks

def layout_checks(design: Design, ex: Extraction) -> list[dict]:
    f = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    b = design.board
    classes = net_classes(design)
    for net, n in ex.islands().items():
        if n > 1:
            add("error", "unrouted", f"net {net} is split in {n} copper islands")
    # current capacity
    for net in design.nets():
        info = design.net_info(net)
        if not info.get("current_a"):
            continue
        need = ipc2221_width_mm(float(info["current_a"]), b["copper_oz"], float(info.get("max_rise_c", 10)))
        segs = [s for s in ex.segs if s["net"] == net]
        if segs:
            wmin = min(s["w"] for s in segs)
            if wmin + 1e-6 < need:
                add("error", "track-current", f"{net}: narrowest track {wmin:.2f} mm < {need:.2f} mm needed for "
                                              f"{info['current_a']} A at 10C rise (IPC-2221)")
    # vias in or touching SMD pads: need IPC-4761 Type VII (filled, capped) or solder wicks away
    vr = b["via_dia_mm"] / 2
    for v in ex.board["vias"]:
        for ref, fp in ex.board["footprints"].items():
            for p in fp["pads"]:
                if p["kind"] != "smd":
                    continue
                dx, dy = abs(v["x"] - p["x"]), abs(v["y"] - p["y"])
                if dx < p["w"] / 2 + vr and dy < p["h"] / 2 + vr:
                    inside = dx < p["w"] / 2 and dy < p["h"] / 2
                    add("warn", "via-in-pad", f"via on {v['net']} {'inside' if inside else 'touching'} pad "
                                              f"{ref}.{p['number']}: needs filled and capped vias (IPC-4761 VII)")
    # decoupling distance
    for c in design.components.values():
        ic = c.get("decouples")
        if not ic or ic not in design.components:
            continue
        lim = float(c.get("decap_max_mm", b.get("decap_max_mm", 4.0)))
        for cpin, net in c.pins.items():
            if net in ex.grounds:
                continue
            best = None
            for ipin, inet in design.components[ic].pins.items():
                if inet != net:
                    continue
                pa, pb = ex.pad_nodes.get((c.ref, cpin)), ex.pad_nodes.get((ic, ipin))
                if pa and pb:
                    d = ex.path_len(pa, pb)
                    if d is not None:
                        best = d if best is None else min(best, d)
            if best is None:
                continue
            if best > lim:
                add("warn", "decap-distance", f"{c.ref} is {best:.1f} mm of copper from {ic} on {net} (limit {lim} mm)")
            else:
                add("info", "decap-distance", f"{c.ref} -> {ic} on {net}: {best:.1f} mm")
    # impedance
    for net in design.nets():
        z = design.net_info(net).get("impedance_ohm")
        if not z:
            continue
        for s in ex.segs:
            if s["net"] == net and s.get("z0") and abs(s["z0"] - z) / z > 0.1:
                add("warn", "impedance", f"{net}: segment Z0 {s['z0']:.0f} ohm vs target {z} ohm "
                                         f"(w={s['w']} mm, h={ex.h_mm} mm)")
                break
    # crosstalk: sensitive victims next to aggressors
    victims = {n for n in design.nets() if design.net_info(n).get("sensitive")}
    aggressors = {n for n in design.nets() if design.net_info(n).get("aggressor") or
                  design.net_info(n).get("class") in ("clock", "switching", "high_speed")}
    for v in victims:
        for a in aggressors - {v}:
            worst = _parallel(ex, v, a)
            if worst:
                plen, gap = worst
                k = 1 / (1 + (gap / ex.h_mm) ** 2)
                sev = "warn" if (k > 0.05 and plen > 3) else "info"
                add(sev, "crosstalk", f"{v} runs {plen:.1f} mm parallel to {a} at {gap:.2f} mm gap, "
                                      f"coupling ~{k * 100:.1f}%")
    # return path on 2-layer boards: traces on the reference (bottom) layer cut the plane
    if b["layers"] == 2 and b["gnd_pour"]:
        for net in design.nets():
            if net in ex.grounds:
                continue
            bl = [s for s in ex.segs if s["net"] == net and s["layer"] == "B.Cu"]
            tot = sum(s["len"] for s in bl)
            info = design.net_info(net)
            if tot > float(b.get("max_bottom_run_mm", 8)) and (info.get("sensitive") or info.get("class") in
                                                                  ("clock", "high_speed", "switching")):
                add("warn", "return-path", f"{net} has {tot:.1f} mm on B.Cu, which cuts the ground reference")
            elif tot > 15:
                add("info", "return-path", f"{net}: {tot:.1f} mm routed on B.Cu ground layer")
    return f


def _parallel(ex: Extraction, n1: str, n2: str):
    best = None
    for s in ex.segs:
        if s["net"] != n1 or s["len"] < 0.5:
            continue
        for o in ex.segs:
            if o["net"] != n2 or o["layer"] != s["layer"] or o["len"] < 0.5:
                continue
            ux, uy = (s["x2"] - s["x1"]) / s["len"], (s["y2"] - s["y1"]) / s["len"]
            vx, vy = (o["x2"] - o["x1"]) / o["len"], (o["y2"] - o["y1"]) / o["len"]
            if abs(ux * vx + uy * vy) < 0.98:
                continue
            # projected overlap and perpendicular distance
            t = sorted([0, s["len"]])
            p1 = (o["x1"] - s["x1"]) * ux + (o["y1"] - s["y1"]) * uy
            p2 = (o["x2"] - s["x1"]) * ux + (o["y2"] - s["y1"]) * uy
            ov = min(t[1], max(p1, p2)) - max(t[0], min(p1, p2))
            if ov <= 0:
                continue
            dist = abs((o["x1"] - s["x1"]) * -uy + (o["y1"] - s["y1"]) * ux)
            gap = dist - (s["w"] + o["w"]) / 2
            if gap < 5 * ex.h_mm and (best is None or ov / max(gap, 0.05) > best[0] / max(best[1], 0.05)):
                best = (ov, gap)
    return best


def ir_drop(design: Design, ex: Extraction, workdir: Path) -> list[dict]:
    """Operating point on extracted copper: voltage spread across pads of each power net."""
    nm = PostLayoutNodeMap(design, ex, with_l=False)
    sim = design.raw.get("sim") or {}
    a = {"name": "ir_drop", "cmd": sim.get("derating_cmd", "op"), "control": ["print all"]}
    net, _ = spice.build_netlist(design, a, nm)
    out, _ = spice.run_ngspice(net, workdir, "ir_drop")
    vals = {}
    for m in re.finditer(rf"^\s*(?:v\()?(\S+?)\)?\s*=\s*({spice._NUM})\s*$", out, re.M):
        vals[m.group(1).lower()] = float(m.group(2))
    rows = []
    for netname, conns in design.nets().items():
        if design.net_info(netname).get("class") != "power":
            continue
        vs = {}
        for ref, pin in conns:
            node = nm.pin(ref, pin)
            if node in vals:
                vs[f"{ref}.{pin}"] = vals[node]
        if len(vs) >= 2:
            hi = max(vs.values())
            lo_k = min(vs, key=vs.get)
            rows.append({"net": netname, "drop_mv": (hi - vs[lo_k]) * 1e3, "worst_pin": lo_k, "v_max": hi})
    return rows


def board_metrics(design: Design, ex: Extraction) -> dict:
    b = design.board
    segs = ex.segs
    return {"area_mm2": b["width_mm"] * b["height_mm"], "layers": b["layers"],
            "track_len_mm": round(sum(s["len"] for s in segs), 1), "vias": len(ex.board["vias"]),
            "bottom_len_mm": round(sum(s["len"] for s in segs if s["layer"] == "B.Cu"), 1)}
