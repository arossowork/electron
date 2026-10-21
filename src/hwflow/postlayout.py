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
    """Split segments where another segment's endpoint touches their interior. One pass over a grid of the
    endpoints per net and layer (a split makes no endpoint that was not there before), so it is bounded."""
    cell = 1.0
    pts: dict = {}
    for k, o in enumerate(segs):
        for px, py in ((o["x1"], o["y1"]), (o["x2"], o["y2"])):
            pts.setdefault((o["net"], o["layer"], int(px // cell), int(py // cell)), []).append((px, py, o["w"], k))
    reach = max((s["w"] for s in segs), default=0.0) / 2
    out = []
    for k, s in enumerate(segs):
        cuts = []
        for i in range(int((min(s["x1"], s["x2"]) - reach) // cell), int((max(s["x1"], s["x2"]) + reach) // cell) + 1):
            for j in range(int((min(s["y1"], s["y2"]) - reach) // cell), int((max(s["y1"], s["y2"]) + reach) // cell) + 1):
                for px, py, w, o in pts.get((s["net"], s["layer"], i, j), ()):
                    t = _on_segment(px, py, s, max(s["w"], w) / 2) if o != k else None
                    if t is not None:
                        cuts.append((t, px, py))
        if not cuts:
            out.append(s)
            continue
        x, y = s["x1"], s["y1"]
        for _, px, py in sorted(cuts):
            if (px, py) != (x, y):
                out.append(dict(s, x1=x, y1=y, x2=px, y2=py))
                x, y = px, py
        out.append(dict(s, x1=x, y1=y))
    return out


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
            rest = []
            for s in segs:
                t = _on_segment(v["x"], v["y"], s, s["w"] / 2 + via_r) if s["net"] == v["net"] else None
                if t is not None:
                    px, py = s["x1"] + t * (s["x2"] - s["x1"]), s["y1"] + t * (s["y2"] - s["y1"])
                    if min(math.hypot(px - s["x1"], py - s["y1"]), math.hypot(px - s["x2"], py - s["y2"])) >= 1e-3:
                        rest += [dict(s, x2=px, y2=py), dict(s, x1=px, y1=py)]
                        continue
                rest.append(s)
            segs = rest
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
                                  "Z0": z0, "layer": s["layer"], "w": s["w"]})
        # 2. trace ends that touch (closer than half the narrower width) are one node
        ends = []
        for s in segs:
            for x, y in ((s["x1"], s["y1"]), (s["x2"], s["y2"])):
                ends.append((s["net"], s["layer"], x, y, s["w"], self._key(x, y, s["layer"])))
        by_net: dict = {}
        for e in ends:
            by_net.setdefault((e[0], e[1]), []).append(e)
        for grp in by_net.values():
            for i, e1 in enumerate(grp):
                for e2 in grp[i + 1:]:
                    if math.hypot(e1[2] - e2[2], e1[3] - e2[3]) <= min(e1[4], e2[4]) / 2:
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
    def __init__(self, design: Design, ex: Extraction, with_l: bool = True, lump: bool | None = None):
        super().__init__(design)
        self.ex = ex
        self.with_l = with_l
        self.lump = lump  # None: board.post_tran_lump decides

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

    def extra(self, analysis: dict | None = None):
        # Trace L matters in frequency-domain runs. In transients, nH segments with fF nodes only add
        # GHz poles that stall switching circuits, so they are left out unless board.post_tran_l is set.
        kind = str((analysis or {}).get("cmd", "op")).split()[0].lower()
        use_l = self.with_l and (kind != "tran" or bool(self.d.board.get("post_tran_l")))
        lump = self.d.board.get("post_tran_lump", True) if self.lump is None else self.lump
        if kind == "tran" and not use_l and lump:
            fast = self.fast_nets()
            lines = self._lumped(skip=fast)
            if fast:  # nets declared fast keep their full trace network (backlog 47)
                lines.append(f"* full R and C network kept for fast nets: {', '.join(sorted(fast))}")
                lines += self._full([(i, e) for i, e in enumerate(self.ex.elements) if e["net"] in fast], False, "f")
            return self._tie_islands(lines)
        lines = ["* --- post-layout parasitics ---" + ("" if use_l else " (R and C only)")]
        lines += self._full(list(enumerate(self.ex.elements)), use_l, "p")
        return self._tie_islands(lines)

    def fast_nets(self) -> set[str]:
        """Nets whose edges matter in transients (`class: fast`, `edge_ns` or `post_full: true` in nets:)."""
        out = set()
        for net in self.d.nets():
            info = self.d.net_info(net)
            if info.get("class") == "fast" or info.get("edge_ns") is not None or info.get("post_full"):
                out.add(net)
        return out

    def _full(self, elems, use_l: bool, tag: str) -> list[str]:
        lines = []
        for i, e in elems:
            if self._is_ideal(e["net"]):
                continue
            a, b = self.ex.node_name(e["a"]), self.ex.node_name(e["b"])
            if a == b:
                continue
            if use_l and e["L"] > 0:
                t2 = "" if tag == "p" else tag
                mid = f"m{t2}{i}"
                lines.append(f"R{tag}x{i} {a} {mid} {max(e['R'], 1e-6):.6g}")
                lines.append(f"L{tag}x{i} {mid} {b} {e['L']:.6g}")
                # damping across the trace inductance: kills the GHz LC ringing of nH/fF segments,
                # which made switching transients stall, and leaves behaviour below ~1 GHz unchanged
                lines.append(f"Rd{t2}x{i} {mid} {b} {e.get('Z0', 50):.6g}")
            else:
                lines.append(f"R{tag}x{i} {a} {b} {max(e['R'], 1e-6):.6g}")
            if e["C"] > 0:
                lines.append(f"C{tag}xa{i} {a} 0 {e['C'] / 2:.6g}")
                lines.append(f"C{tag}xb{i} {b} 0 {e['C'] / 2:.6g}")
        return lines

    def _tie_islands(self, lines: list[str]) -> list[str]:
        """Every pad node of a net must exist in the netlist and reach the other pads (backlog 46). Copper islands
        joined only through a pour, a pad whose island holds no other pad (dropped by the lumping), or a pad that
        falls back to the ideal net node are tied with 1 mOhm, so a testbench probe on any pad (`v(BT1.2)`) finds
        its node and the circuit is not split. Split nets are still reported by layout_checks."""
        uf = UF()
        for ln in lines:
            t = ln.split()
            if len(t) >= 4 and t[0][:1] in "RLC" and not t[0].startswith("C"):
                uf.union(t[1], t[2])
        ties = []
        for net, conns in self.d.nets().items():
            if self._is_ideal(net):
                continue
            names = sorted({self.pin(r, p) for r, p in conns})
            for a, b in zip(names, names[1:]):
                if uf.find(a) != uf.find(b):
                    uf.union(a, b)
                    ties.append(f"Rtie{len(ties)} {a} {b} 1e-3")
        if ties:
            lines = lines + ["* pads on separate copper islands of one net (pour or unrouted): ideal tie"] + ties
        return lines

    def _lumped(self, skip: set | frozenset = frozenset()) -> list[str]:
        """Transient parasitics reduced to the pads: per copper island, the trace R network is Kron-reduced
        to its pad nodes (exact DC resistance between every pad pair). Trace C is left out: pF on nodes
        joined by mOhm gives 1e-14 s time constants that stall long transients and change nothing at
        transient time scales. AC and noise runs keep the full RC(L) network."""
        lines = ["* --- post-layout parasitics --- (trace R reduced to pads, no C, for transients)"]
        find = self.ex.uf.find
        pad_roots = {find(pn) for pn in self.ex.pad_nodes.values()}
        nets: dict[str, list] = {}
        for e in self.ex.elements:
            if self._is_ideal(e["net"]) or e["net"] in skip:
                continue
            a, b = find(e["a"]), find(e["b"])
            if a != b:
                nets.setdefault(e["net"], []).append((a, b, e))
        k = 0
        for net, elems in nets.items():
            uf = UF()
            for a, b, _ in elems:
                uf.union(a, b)
            islands: dict = {}
            for a, b, e in elems:
                islands.setdefault(uf.find(a), []).append((a, b, e))
            for isl in islands.values():
                nodes = sorted({n for a, b, _ in isl for n in (a, b)}, key=str)
                ports = [n for n in nodes if n in pad_roots]
                if not ports:
                    continue
                inner = [n for n in nodes if n not in pad_roots]
                idx = {n: i for i, n in enumerate(ports + inner)}
                G = np.zeros((len(idx), len(idx)))
                for a, b, e in isl:
                    g = 1.0 / max(e["R"], 1e-6)
                    i, j = idx[a], idx[b]
                    G[i, i] += g
                    G[j, j] += g
                    G[i, j] -= g
                    G[j, i] -= g
                p = len(ports)
                if inner:
                    G = G[:p, :p] - G[:p, p:] @ np.linalg.solve(G[p:, p:], G[p:, :p])
                names = [self.ex.node_name(n) for n in ports]
                for i in range(p):
                    for j in range(i + 1, p):
                        g = -G[i, j]
                        if g > 1e-9 and names[i] != names[j]:
                            lines.append(f"Rpx{k} {names[i]} {names[j]} {1.0 / g:.6g}")
                            k += 1
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
                from .routing import NECK_MAX_MM, longest_neck
                thin = [s for s in segs if s["w"] + 1e-6 < need]
                narrow = sum(s["len"] for s in thin)
                neck = longest_neck([((s["x1"], s["y1"]), (s["x2"], s["y2"]), s["layer"]) for s in thin])
                # a short neck at a pad heats no more than the pad it enters (copper on both sides spreads the heat)
                sev = "warn" if neck <= NECK_MAX_MM else "error"
                add(sev, "track-current", f"{net}: narrowest track {wmin:.2f} mm < {need:.2f} mm needed for "
                                          f"{info['current_a']} A at 10C rise (IPC-2221), for {narrow:.1f} mm"
                                          + (f" (necks of at most {NECK_MAX_MM:g} mm each)" if sev == "warn"
                                             else f" (a stretch of {neck:.1f} mm)"))
    # vias in or touching SMD pads: need IPC-4761 Type VII (filled, capped) or solder wicks away
    vr = b["via_dia_mm"] / 2
    declared = {(r, str((c.get("thermal_vias") or {}).get("pad"))) for r, c in design.components.items()
                if c.get("thermal_vias")}
    for v in ex.board["vias"]:
        for ref, fp in ex.board["footprints"].items():
            for p in fp["pads"]:
                if p["kind"] != "smd" or (ref, p["number"]) in declared:
                    continue
                dx, dy = abs(v["x"] - p["x"]), abs(v["y"] - p["y"])
                if dx < p["w"] / 2 + vr and dy < p["h"] / 2 + vr:
                    inside = dx < p["w"] / 2 and dy < p["h"] / 2
                    add("warn", "via-in-pad", f"via on {v['net']} {'inside' if inside else 'touching'} pad "
                                              f"{ref}.{p['number']}: needs filled and capped vias (IPC-4761 VII)")
    # decoupling distance
    for r in decap_paths(design, ex):
        if r["mm"] > r["limit"]:
            add("warn", "decap-distance", f"{r['cap']} is {r['mm']:.1f} mm of copper from {r['ic']} on {r['net']} "
                                          f"(limit {r['limit']} mm)")
        else:
            add("info", "decap-distance", f"{r['cap']} -> {r['ic']} on {r['net']}: {r['mm']:.1f} mm")
    # requirement budgets on the routed copper (design.yaml budgets:)
    from .budgets import findings as budget_findings
    f += budget_findings(design, ex)
    # signal integrity without SPICE, for nets with a declared edge rate (backlog 48)
    for r in si_screen(design, ex):
        add(r["sev"], "si", r["msg"])
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


def _ref_layers(layer: str, n_layers: int) -> list[str]:
    """Copper layers next to `layer` that carry its return current."""
    if n_layers == 4:
        return {"F.Cu": ["In1.Cu"], "In1.Cu": ["F.Cu", "In2.Cu"], "In2.Cu": ["In1.Cu", "B.Cu"],
                "B.Cu": ["In2.Cu"]}.get(layer, [])
    return {"F.Cu": ["B.Cu"], "B.Cu": ["F.Cu"]}.get(layer, [])


def _inside(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd point in polygon (KiCad's fractured fills: slits cancel out)."""
    x, y = poly[0], poly[1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    X, Y = px[:, None], py[:, None]
    cross = ((y > Y) != (y2 > Y)) & (X < (x2 - x) * (Y - y) / np.where(y2 != y, y2 - y, 1e-12) + x)
    return (cross.sum(1) % 2) == 1


def _path(ex: Extraction, a, b) -> tuple[float, float, int] | None:
    """Shortest copper path a -> b: (mm, series inductance in H from the extraction, vias)."""
    a, b = ex.uf.find(a), ex.uf.find(b)
    best = {a: 0.0}
    pq = [(0.0, 0.0, 0, str(a), a)]
    while pq:
        d, L, nv, _, n = heapq.heappop(pq)
        if n == b:
            return d, L, nv
        if d > best.get(n, 1e18):
            continue
        for m, e in ex.adj.get(n, []):
            nd = d + e["len"] + (0 if e["len"] else 1e-6)
            if nd < best.get(m, 1e18):
                best[m] = nd
                heapq.heappush(pq, (nd, L + e.get("L", 0), nv + (0 if e["len"] else 1), str(m), m))
    return None


def si_screen(design: Design, ex: Extraction, fills: list | None = None) -> list[dict]:
    """Signal-integrity screen without SPICE (backlog 48), for nets with `edge_ns` (rise time) in nets:.

    - electrical length: the longest pad-to-pad copper path, with the delay of the extracted lines, against the
      rise-time rule (a line is electrically long when its delay is above t_r/6; it then needs a termination
      or a full transient);
    - vias and branch points (stubs) on the net;
    - gaps in the reference plane: trace length whose adjacent copper layer has no pour under it;
    - decoupling loops: for an IC with an edge-rate net (or `edge_ns` on the component), the inductance of the
      decap path from the extraction, as impedance at the knee frequency 0.35/t_r, against `board.si_pdn_z_ohm`
      (default 1 ohm; the C5 case: 10.6 mm of copper is about 10 nH).
    Returns rows {net, sev, msg, ...}."""
    b = design.board
    edge = {n: float(design.net_info(n)["edge_ns"]) for n in design.nets()
            if design.net_info(n).get("edge_ns") is not None}
    ic_edge = {}
    for ref, c in design.components.items():
        es = [edge[n] for n in c.pins.values() if n in edge]
        if c.get("edge_ns") is not None:
            es.append(float(c.get("edge_ns")))
        if es:
            ic_edge[ref] = min(es)
    if not edge and not ic_edge:
        return []
    if fills is None:
        fills = _zone_fills(ex.path) if getattr(ex, "path", None) else []
    rows = []
    for net, tr in sorted(edge.items()):
        els = [e for e in ex.elements if e["net"] == net and e["len"] > 0]
        if not els:
            continue
        mm = sum(e["len"] for e in els)
        td_mm = sum(math.sqrt(e["L"] * e["C"]) for e in els) / mm            # s per mm of this net's lines
        pads = [ex.pad_nodes[(r, p)] for r, p in design.nets().get(net, []) if (r, p) in ex.pad_nodes]
        longest = 0.0
        for i, pa in enumerate(pads):
            for pb in pads[i + 1:]:
                q = _path(ex, pa, pb)
                if q:
                    longest = max(longest, q[0])
        delay_ns = longest * td_mm * 1e9
        vias = sum(1 for v in ex.board["vias"] if v["net"] == net)
        deg: dict = {}
        for e in els:
            for n in (ex.uf.find(e["a"]), ex.uf.find(e["b"])):
                deg[n] = deg.get(n, 0) + 1
        stubs = sum(1 for n, k in deg.items() if k >= 3 and n[0] != "pad")
        long_ = delay_ns > tr / 6
        rows.append({"net": net, "check": "length", "sev": "warn" if long_ else "info", "edge_ns": tr,
                     "mm": round(longest, 2), "delay_ns": round(delay_ns, 4), "vias": vias, "stubs": stubs,
                     "msg": f"{net}: {longest:.1f} mm longest path, delay {delay_ns * 1e3:.0f} ps against t_r/6 = "
                            f"{tr / 6 * 1e3:.0f} ps ({'electrically long: terminate it or run the full transient' if long_ else 'short'}); "
                            f"{vias} vias, {stubs} branch points"})
        # reference plane under the trace
        gap = {}
        clr = float(b.get("clearance_mm", 0.2))
        holes = [(v["x"], v["y"], b.get("via_dia_mm", 0.6) / 2 + 2 * clr) for v in ex.board["vias"] if v["net"] == net]
        holes += [(p["x"], p["y"], max(p["w"], p["h"]) / 2 + 2 * clr) for fp in ex.board.get("footprints", {}).values()
                  for p in fp["pads"] if p["net"] == net and p["kind"] == "thru_hole"]   # own antipads: not gaps
        for sgm in [x for x in ex.segs if x["net"] == net and x.get("len", 0) > 0]:
            refs = _ref_layers(sgm["layer"], b["layers"])
            polys = [z["pts"] for z in fills if z["layer"] in refs and z["net"] != net]
            n = max(int(sgm["len"] / 0.25), 1)
            t = (np.arange(n) + 0.5) / n
            px, py = sgm["x1"] + t * (sgm["x2"] - sgm["x1"]), sgm["y1"] + t * (sgm["y2"] - sgm["y1"])
            ok = np.zeros(n, bool)
            for hx, hy, hr in holes:
                ok |= np.hypot(px - hx, py - hy) <= hr
            for poly in polys:
                ok |= _inside(px, py, poly)
            if (~ok).any():
                gap[sgm["layer"]] = gap.get(sgm["layer"], 0.0) + float((~ok).sum()) / n * sgm["len"]
        for layer, g in sorted(gap.items()):
            if g >= 0.5:
                rows.append({"net": net, "check": "plane-gap", "sev": "warn", "layer": layer, "mm": round(g, 2),
                             "msg": f"{net}: {g:.1f} mm on {layer} without a reference plane on "
                                    f"{'/'.join(_ref_layers(layer, b['layers']))} (return path detour)"})
    zmax = float(b.get("si_pdn_z_ohm", 1.0))
    for r in decap_paths(design, ex):
        tr = ic_edge.get(r["ic"])
        if tr is None:
            continue
        c = design.components[r["cap"]]
        L = None
        for cpin, net in c.pins.items():
            if net != r["net"]:
                continue
            for ipin, inet in design.components[r["ic"]].pins.items():
                pa, pb = ex.pad_nodes.get((c.ref, cpin)), ex.pad_nodes.get((r["ic"], ipin))
                if inet == net and pa and pb:
                    q = _path(ex, pa, pb)
                    if q and (L is None or q[1] < L):
                        L = q[1]
        if L is None:
            continue
        x = 2 * math.pi * 0.35 / (tr * 1e-9) * L
        rows.append({"net": r["net"], "check": "decap-loop", "sev": "warn" if x > zmax else "info",
                     "cap": r["cap"], "ic": r["ic"], "nH": round(L * 1e9, 2), "z_ohm": round(x, 3),
                     "msg": f"{r['cap']} -> {r['ic']} on {r['net']}: {r['mm']:.1f} mm, {L * 1e9:.1f} nH, "
                            f"{x:.2f} ohm at {0.35 / tr * 1e3:.0f} MHz (t_r {tr} ns; limit {zmax} ohm)"})
    return rows


def decap_paths(design: Design, ex: Extraction) -> list[dict]:
    """Shortest copper path from each decoupling capacitor to its IC, per supply net (decouples: <IC>)."""
    b = design.board
    rows = []
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
            if best is not None:
                rows.append({"cap": c.ref, "ic": ic, "net": net, "mm": round(best, 2), "limit": lim})
    return rows


# --------------------------------------------------------------- clearance between net groups

_RULE_NAME = re.compile(r'\(rule\s+"([^"]+)"')
_RULE_CLR = re.compile(r"\(constraint\s+clearance\s+\(min\s+([\d.]+)\s*mm\)")
_RULE_COND = re.compile(r"A\.NetName\s*==\s*'([^']+)'\s*&&\s*B\.NetName\s*!=\s*'([^']+)'")


def clearance_rules(design: Design) -> list[dict]:
    """Group clearance rules from board.drc_rules of the form
    (rule "name" (constraint clearance (min X mm)) (condition "A.NetName == 'P' && B.NetName != 'P'"))."""
    out = []
    for r in design.board.get("drc_rules") or []:
        c, m = _RULE_CLR.search(r), _RULE_COND.search(r)
        if c and m and m.group(1) == m.group(2):
            n = _RULE_NAME.search(r)
            out.append({"rule": n.group(1) if n else r[:40], "pattern": m.group(1), "min_mm": float(c.group(1))})
    from .pcb import isolation_groups
    if hasattr(design, "nets"):
        for g in isolation_groups(design):
            lim = g["clearance_mm"] if g["clearance_mm"] is not None else g["creepage_mm"]
            if g["members"] and lim is not None:
                out.append({"rule": g["name"], "pattern": ",".join(g["members"]), "members": set(g["members"]),
                            "min_mm": float(lim), "creepage_mm": g["creepage_mm"], "req": g["req"]})
    return out


def _copper_items(ex: Extraction) -> list[tuple]:
    """Copper as (net, layer, x1, y1, x2, y2, radius): traces as capsules, pads and zone fills as edges, vias as discs."""
    b = ex.d.board
    layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"] if b["layers"] == 4 else ["F.Cu", "B.Cu"]
    items = []
    for s in ex.board["segments"]:
        if s["net"]:
            items.append((s["net"], s["layer"], s["x1"], s["y1"], s["x2"], s["y2"], s["w"] / 2))
    for v in ex.board["vias"]:
        for L in layers:
            items.append((v["net"], L, v["x"], v["y"], v["x"], v["y"], b["via_dia_mm"] / 2))
    for fp in ex.board["footprints"].values():
        for p in fp["pads"]:
            if not p["net"]:
                continue
            cu = layers if "*.Cu" in p["layers"] else [l for l in p["layers"] if l.endswith(".Cu")]
            x0, x1, y0, y1 = p["x"] - p["w"] / 2, p["x"] + p["w"] / 2, p["y"] - p["h"] / 2, p["y"] + p["h"] / 2
            if p.get("shape") in ("circle", "oval"):   # round pads as a disc or capsule, not their bounding box
                r = min(p["w"], p["h"]) / 2
                ax, ay = max(p["w"] / 2 - r, 0), max(p["h"] / 2 - r, 0)
                for L in cu:
                    items.append((p["net"], L, p["x"] - ax, p["y"] - ay, p["x"] + ax, p["y"] + ay, r))
                continue
            for L in cu:
                for e in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
                    items.append((p["net"], L, *e, 0.0))
    if ex.path:
        for z in _zone_fills(ex.path):
            pts = z["pts"].T
            for (xa, ya), (xb, yb) in zip(pts, np.roll(pts, -1, axis=0)):
                items.append((z["net"], z["layer"], xa, ya, xb, yb, 0.0))
    return items


def _seg_dist(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Pairwise distance between 2-D segments A (n,4) and B (m,4) -> (n,m); 0 where they cross."""
    def pt_seg(px, py, s):
        x1, y1, x2, y2 = (s[..., i] for i in range(4))
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        t = np.where(L2 > 0, ((px - x1) * dx + (py - y1) * dy) / np.where(L2 > 0, L2, 1), 0).clip(0, 1)
        return np.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
    a, b = A[:, None, :], B[None, :, :]
    d = np.minimum.reduce([pt_seg(a[..., 0], a[..., 1], b), pt_seg(a[..., 2], a[..., 3], b),
                           pt_seg(b[..., 0], b[..., 1], a), pt_seg(b[..., 2], b[..., 3], a)])

    def orient(px, py, qx, qy, rx, ry):
        return np.sign((qx - px) * (ry - py) - (qy - py) * (rx - px))
    o1 = orient(a[..., 0], a[..., 1], a[..., 2], a[..., 3], b[..., 0], b[..., 1])
    o2 = orient(a[..., 0], a[..., 1], a[..., 2], a[..., 3], b[..., 2], b[..., 3])
    o3 = orient(b[..., 0], b[..., 1], b[..., 2], b[..., 3], a[..., 0], a[..., 1])
    o4 = orient(b[..., 0], b[..., 1], b[..., 2], b[..., 3], a[..., 2], a[..., 3])
    return np.where((o1 * o2 < 0) & (o3 * o4 < 0), 0.0, d)


def group_clearances(design: Design, ex: Extraction) -> list[dict]:
    """Smallest copper-to-copper distance between the nets of each clearance rule and every other net, per rule
    (backlog 39): the number a creepage or isolation requirement depends on, next to its limit."""
    import fnmatch
    rules = clearance_rules(design)
    if not rules:
        return []
    items = _copper_items(ex)
    out = []
    for r in rules:
        best = None
        mem = r.get("members")
        inside = (lambda n: n in mem) if mem else (lambda n: fnmatch.fnmatchcase(n, r["pattern"]))  # noqa: E731
        for layer in sorted({it[1] for it in items}):
            A = [it for it in items if it[1] == layer and inside(it[0])]
            B = [it for it in items if it[1] == layer and not inside(it[0])]
            if not A or not B:
                continue
            Ag, Bg = np.array([it[2:6] for it in A]), np.array([it[2:6] for it in B])
            ra, rb = np.array([it[6] for it in A]), np.array([it[6] for it in B])
            for i in range(0, len(A), 256):
                d = _seg_dist(Ag[i:i + 256], Bg) - ra[i:i + 256, None] - rb[None, :]
                k = np.unravel_index(np.argmin(d), d.shape)
                if best is None or d[k] < best[0]:
                    best = (float(d[k]), A[i + k[0]][0], B[k[1]][0], layer)
        if best:
            out.append({"rule": r["rule"], "pattern": r["pattern"], "limit": r["min_mm"], "mm": round(max(best[0], 0), 3),
                        "between": f"{best[1]} / {best[2]} on {best[3]}", "ok": best[0] >= r["min_mm"] - 1e-6,
                        **({"creepage_mm": r["creepage_mm"], "req": r["req"]} if mem else {})})
    return out


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
    split = ex.islands()
    for netname, conns in design.nets().items():
        if design.net_info(netname).get("class") != "power":
            continue
        if split.get(netname, 1) > 1:  # the islands are joined by ideal 1 mOhm ties: a drop read there means nothing
            rows.append({"net": netname, "drop_mv": None, "worst_pin": None, "v_max": None, "islands": split[netname]})
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
