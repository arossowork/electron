"""Layout blocks: pre-laid-out, verified sub-circuits (`blocks/<name>/block.yaml`) that a design binds to its parts
(`blocks:` in design.yaml). The placer moves a block's parts as one rigid unit, the board writer adds the block's
copper as pre-existing copper, and the router only joins the blocks. Format: docs/design-yaml.md, blocks/README.md."""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

from .settings import get_settings
from .placer import Part, Rigid, rot



def library_root() -> Path:
    """The repository's block library (settings.paths.blocks_dir)."""
    return Path(get_settings().paths.blocks_dir)


YARD = "block:"  # ref prefix of a block courtyard in the placer


# ------------------------------------------------------------------ library

def lib_dirs(design=None) -> list[Path]:
    """Where blocks are looked up: the project's own `blocks/` first, then the repository library."""
    out = [design.dir / "blocks"] if design is not None and (design.dir / "blocks").is_dir() else []
    return out + [library_root()]


def names(design=None) -> list[str]:
    seen = {}
    for d in lib_dirs(design):
        for f in sorted(d.glob("*/block.yaml")):
            seen.setdefault(f.parent.name, f)
    return sorted(seen)


def find(name: str, design=None) -> Path | None:
    for d in lib_dirs(design):
        f = d / name / "block.yaml"
        if f.exists():
            return f
    return None


@lru_cache(maxsize=64)
def _read(path: str, mtime: int) -> dict:
    b = yaml.safe_load(Path(path).read_text()) or {}
    b["dir"] = str(Path(path).parent)
    b.setdefault("name", Path(path).parent.name)
    return b


def load(name: str, design=None) -> dict:
    f = find(name, design)
    if f is None:
        raise KeyError(f"block {name!r} not found (library: {', '.join(names(design)) or 'empty'})")
    return _read(str(f), f.stat().st_mtime_ns)


def ports(block: dict) -> set[str]:
    return {str(p) for p in block.get("ports") or []}


def pins(role: dict) -> dict[str, str]:
    """pin -> block net of a role (YAML may read pin numbers as integers)."""
    return {str(k): str(v) for k, v in (role.get("pins") or {}).items()}


def block_nets(block: dict) -> set[str]:
    return {n for r in (block.get("roles") or {}).values() for n in pins(r).values()}


def drc_rules(design) -> list[str]:
    """KiCad custom rules the blocks' land patterns need (`drc_rules:` of a block, `{U}` a bound role's ref,
    `{block}` the entry name), for the .kicad_dru that DRC and the router read."""
    out = []
    for bnd in bindings(design):
        for r in bnd["block"].get("drc_rules") or []:
            out.append(str(r).format(block=bnd["name"], **bnd["parts"]))
    return out


def footprint_dirs(design) -> list[Path]:
    """`footprints/` folders of the blocks the design uses (project-specific footprints shipped with a block)."""
    out = []
    for e in (design.raw.get("blocks") or {}).values():
        f = find(str((e or {}).get("use")), design)
        if f is not None and (f.parent / "footprints").is_dir():
            out.append(f.parent / "footprints")
    return out


# ------------------------------------------------------------------ binding

def _refs(name: str, e: dict, block: dict) -> dict[str, str]:
    """role -> ref: `parts` (bind mode), else the instance refs (`refs`, else the block's own refs)."""
    if e.get("parts"):
        return {str(k): str(v) for k, v in e["parts"].items()}
    refs = e.get("refs") or {}
    return {role: str(refs.get(role) or r.get("ref") or f"{name}_{role}") for role, r in (block.get("roles") or {}).items()}


def bindings(design) -> list[dict]:
    """The design's blocks that resolve: [{name, block, parts (role -> ref), at}]. `check` reports the others."""
    out = []
    for name, e in (design.raw.get("blocks") or {}).items():
        e = e or {}
        try:
            b = load(str(e.get("use")), design)
        except KeyError:
            continue
        out.append({"name": str(name), "block": b, "parts": _refs(str(name), e, b), "at": e.get("at") or {}})
    return out


def expand(design):
    """Instantiate mode: a `blocks:` entry with `nets:` (block port -> design net) and no `parts:` adds the block's
    components (refs from `refs:` or the block). Internal nets become `<entry>_<net>`. Called by Design.load; the
    added components carry `from_block` and are not written back by Design.save."""
    from .design import Component
    for name, e in (design.raw.get("blocks") or {}).items():
        e = e or {}
        if e.get("parts") or e.get("nets") is None:
            continue
        try:
            b = load(str(e.get("use")), design)
        except KeyError:
            continue
        nets = {str(k): str(v) for k, v in (e.get("nets") or {}).items()}
        for role, ref in _refs(str(name), e, b).items():
            r = b["roles"][role]
            if ref in design.components:
                continue  # `check` reports the clash
            data = {k: r[k] for k in ("value", "symbol", "footprint", "mpn", "lcsc", "price_usd") if k in r}
            data["pins"] = {p: nets.get(n, f"{name}_{n}") for p, n in pins(r).items()}
            data.update(spice=False, from_block=str(name))
            design.components[ref] = Component(ref, data)


def netmap(design, bnd: dict) -> tuple[dict[str, str], list[str]]:
    """block net -> design net from the bound parts' pins, and the inconsistencies found."""
    b, out, bad = bnd["block"], {}, []
    for role, r in (b.get("roles") or {}).items():
        ref = bnd["parts"].get(role)
        c = design.components.get(ref)
        if c is None:
            continue
        for pin, bn in pins(r).items():
            dn = c.pins.get(pin)
            if dn is None or dn.upper() in ("NC", "~"):
                bad.append(f"{ref}.{pin} has no net; block {b['name']} connects it to {bn}")
            elif out.setdefault(bn, dn) != dn:
                bad.append(f"block net {bn} is {out[bn]} on one pin and {dn} on {ref}.{pin}")
    return out, bad


def findings(design, libs=None) -> list[dict]:
    """`check` findings of the `blocks:` field: unknown block, roles not bound, footprints, pin nets, layers."""
    f = []

    def add(sev, msg):
        f.append({"sev": sev, "code": "block", "msg": msg})

    seen: dict[str, str] = {}
    known = {x["name"] for x in bindings(design)}
    for name, e in (design.raw.get("blocks") or {}).items():
        if str(name) not in known:
            add("error", f"blocks.{name}: block {(e or {}).get('use')!r} not found "
                         f"(library: {', '.join(names(design)) or 'empty'})")
    for bnd in bindings(design):
        name, b, refs = bnd["name"], bnd["block"], bnd["parts"]
        roles = b.get("roles") or {}
        for role in roles:
            if role not in refs:
                add("error", f"blocks.{name}: role {role} of {b['name']} is not bound (parts: {{{role}: <ref>}})")
        for role, ref in refs.items():
            if role not in roles:
                add("error", f"blocks.{name}: {b['name']} has no role {role} (roles: {', '.join(roles)})")
                continue
            c = design.components.get(ref)
            if c is None:
                add("error", f"blocks.{name}: {role} is {ref}, which is not a component")
                continue
            e = (design.raw.get("blocks") or {}).get(name) or {}
            if not e.get("parts") and c.get("from_block") != name:
                add("error", f"blocks.{name}: the block would add {ref}, which is already a component (set refs: {{{role}: <ref>}})")
            if ref in seen:
                add("error", f"blocks.{name}: {ref} is already bound in block {seen[ref]}")
            seen[ref] = name
            r = roles[role]
            if str(c.get("footprint")) != str(r.get("footprint")):
                add("error", f"blocks.{name}: {ref} has footprint {c.get('footprint')}; role {role} of {b['name']} "
                             f"needs {r.get('footprint')}")
            pl = c.get("placement") or {}
            if "x" in pl or "y" in pl or "edge" in pl:
                add("error", f"blocks.{name}: {ref} has its own placement; the block places it (remove it, or set "
                             f"blocks.{name}.at)")
            msg = _value_mismatch(c, r)
            if msg:
                add("warn", f"blocks.{name}: {ref} {msg}")
        nm, bad = netmap(design, bnd)
        for m in bad:
            add("error", f"blocks.{name}: {m}")
        P = ports(b)
        inside = {(r, p) for role, r in refs.items() for p in pins(roles.get(role, {}))}
        conns = design.nets()
        for bn, dn in nm.items():
            if bn in P:
                continue
            out = [f"{r}.{p}" for r, p in conns.get(dn, []) if (r, p) not in inside]
            if out:
                add("error", f"blocks.{name}: internal net {bn} of {b['name']} ({dn}) also reaches {', '.join(out[:4])}"
                             f"; only ports ({', '.join(sorted(P))}) may leave the block")
        by_dn: dict[str, list[str]] = {}
        for bn, dn in nm.items():
            by_dn.setdefault(dn, []).append(bn)
        for dn, bns in by_dn.items():
            if len(bns) > 1 and not all(x in P for x in bns):
                add("error", f"blocks.{name}: block nets {', '.join(sorted(bns))} of {b['name']} are all {dn} "
                             f"(a short across the block's copper)")
        at = bnd["at"]
        if str(at.get("side", "front")) != "front":
            add("error", f"blocks.{name}: side {at.get('side')} is not supported (hwflow places every part on the front)")
        if at.get("rot") is not None and float(at["rot"]) % 90:
            add("error", f"blocks.{name}: rot {at['rot']} is not a multiple of 90")
        brd = design.board
        if int(b.get("layers_min", 2)) > int(brd.get("layers", 2)):
            add("error", f"blocks.{name}: {b['name']} needs {b.get('layers_min')} layers, the board has {brd.get('layers')}")
        rules = b.get("rules") or {}
        for k in ("clearance_mm", "via_dia_mm"):
            if k in rules and float(brd[k]) > float(rules[k]) + 1e-9:
                add("warn", f"blocks.{name}: {b['name']} copper was checked at {k} {rules[k]}; the board uses "
                            f"{brd[k]} (DRC may flag the block copper)")
    return f


def _value_mismatch(c, r: dict) -> str | None:
    from .design import parse_value
    v = c.get("value")
    try:
        x = parse_value(v)
    except (ValueError, TypeError):
        x = None
    if r.get("value") is not None:
        try:
            same = math.isclose(x, parse_value(r["value"]), rel_tol=1e-6)
        except (ValueError, TypeError):  # a part number: the block's names the family (TPS61299 for "TPS61299 3.3V")
            same = str(v).upper().startswith(str(r["value"]).upper())
        if not same:
            return f"value {v} differs from the block's {r['value']}"
    for k, cmp in (("value_min", lambda a, b: a >= b * (1 - 1e-9)), ("value_max", lambda a, b: a <= b * (1 + 1e-9))):
        if r.get(k) is not None and x is not None and not cmp(x, parse_value(r[k])):
            return f"value {v} is outside the block's {k} {r[k]}"
    return None


# ------------------------------------------------------------------ geometry

def courtyard(block: dict) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in block["courtyard"])
    return x0, y0, x1, y1


def _origin(bnd: dict, parts) -> tuple[float, float, float] | None:
    """Block origin and rotation from its first bound part's position."""
    pl = bnd["block"].get("placement") or {}
    for role, ref in bnd["parts"].items():
        if ref in parts and role in pl:
            p, q = parts[ref], pl[role]
            r = (p.r - float(q.get("rot", 0))) % 360
            ox, oy = rot(float(q["x"]), float(q["y"]), r)
            return p.x - ox, p.y - oy, r
    return None


def units(design, libs, parts, margin: float) -> list:
    """The placer units of the design's blocks (placer.Rigid). A block with `at: {x, y}` is fixed (unless
    `fixed: false`) and its members get their positions; members of a movable block are movable."""
    out = []
    for bnd in bindings(design):
        b, refs = bnd["block"], bnd["parts"]
        pl = b.get("placement") or {}
        if not refs or any(r not in parts for r in refs.values()) or any(role not in pl for role in refs):
            continue
        members = [(ref, float(pl[role]["x"]), float(pl[role]["y"]), float(pl[role].get("rot", 0)))
                   for role, ref in refs.items()]
        x0, y0, x1, y1 = courtyard(b)
        w, h, cx, cy = x1 - x0 + 2 * margin, y1 - y0 + 2 * margin, (x0 + x1) / 2, (y0 + y1) / 2
        yard = Part(YARD + bnd["name"], w, h, cx, cy, {}, sides={"front": (w, h, cx, cy)})
        u = Rigid(bnd["name"], yard.ref, members, yard)
        at = bnd["at"]
        if "x" in at and "y" in at:
            u.x, u.y, u.r = float(at["x"]), float(at["y"]), float(at.get("rot", 0))
            u.fixed = bool(at.get("fixed", True))
        elif "rot" in at:
            u.r = float(at["rot"])
        yard.fixed = u.fixed
        for ref in refs.values():
            parts[ref].fixed = u.fixed
        u.sync(parts)
        out.append(u)
    return out


def anchor(design, ref: str) -> dict | None:
    """Board placement {x, y, rot} of a member of a fixed block (from `at`), else None."""
    for bnd in bindings(design):
        at = bnd["at"]
        if "x" not in at or "y" not in at or not at.get("fixed", True):
            continue
        for role, r in bnd["parts"].items():
            q = (bnd["block"].get("placement") or {}).get(role)
            if r == ref and q is not None:
                a = float(at.get("rot", 0))
                dx, dy = rot(float(q["x"]), float(q["y"]), a)
                return {"x": float(at["x"]) + dx, "y": float(at["y"]) + dy, "rot": (float(q.get("rot", 0)) + a) % 360}
    return None


def copper(design, parts) -> tuple[list[dict], list[dict]]:
    """The blocks' copper in board coordinates and design nets: (tracks, vias) as the board writer takes them."""
    tracks, vias = [], []
    for bnd in bindings(design):
        o = _origin(bnd, parts)
        if o is None or any(r not in parts for r in bnd["parts"].values()):
            continue
        ox, oy, a = o
        nm, _ = netmap(design, bnd)
        cu = bnd["block"].get("copper") or {}

        def xy(x, y):
            dx, dy = rot(float(x), float(y), a)
            return round(ox + dx, 4), round(oy + dy, 4)

        for t in cu.get("tracks") or []:
            net = nm.get(str(t["net"]))
            if net is None:
                continue
            pts = [xy(*p) for p in t["pts"]]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                tracks.append({"net": net, "layer": str(t["layer"]), "w": float(t["w"]),
                               "x1": x1, "y1": y1, "x2": x2, "y2": y2, "block": bnd["name"]})
        for v in cu.get("vias") or []:
            net = nm.get(str(v["net"]))
            if net is not None:
                x, y = xy(v["x"], v["y"])
                vias.append({"net": net, "x": x, "y": y, "block": bnd["name"]})
    return tracks, vias


def _tkey(t: dict) -> tuple:
    a, b = (round(t["x1"], 4), round(t["y1"], 4)), (round(t["x2"], 4), round(t["y2"], 4))
    return (t["net"], t["layer"], round(float(t["w"]), 4)) + tuple(sorted((a, b)))


def merge(design, parts, tracks, vias) -> tuple[list, list]:
    """The block copper plus `tracks` and `vias` without the items that repeat it (a routed board read back keeps
    the block copper it was given): the block copper appears once on any board."""
    bt, bv = copper(design, parts)
    if not bt and not bv:
        return list(tracks or []), list(vias or [])
    tk = {_tkey(t) for t in bt}
    vk = {(v["net"], round(v["x"], 4), round(v["y"], 4)) for v in bv}
    return (bt + [t for t in tracks or [] if _tkey(t) not in tk],
            bv + [v for v in vias or [] if (v["net"], round(v["x"], 4), round(v["y"], 4)) not in vk])


def keepout_zones(design, parts) -> list:
    """Rule areas of the blocks' `keepouts` in board coordinates (KiCad zone nodes)."""
    import uuid

    from .pcb import cu_layers
    from .sexpr import Q
    out, board = [], set(cu_layers(int(design.board["layers"])))
    for bnd in bindings(design):
        o = _origin(bnd, parts)
        if o is None:
            continue
        ox, oy, a = o
        for i, k in enumerate(bnd["block"].get("keepouts") or []):
            lay = [str(L) for L in k.get("layers") or ["F.Cu", "B.Cu"] if str(L) in board]
            if not lay:
                continue  # inner-layer keep-outs on a 2-layer board
            x0, y0, x1, y1 = (float(v) for v in k["rect"])
            pts = [rot(x, y, a) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            forbid = {str(x) for x in k.get("forbids") or ["tracks", "vias"]}
            rules = [[n, "not_allowed" if n in forbid else "allowed"] for n in ("tracks", "vias", "pads", "copperpour", "footprints")]
            out.append(["zone", ["net", 0], ["net_name", Q("")], ["layers"] + [Q(L) for L in lay],
                        ["uuid", Q(str(uuid.uuid4()))], ["name", Q(f"block {bnd['name']} keepout {i + 1}")],
                        ["hatch", "edge", 0.5], ["connect_pads", ["clearance", 0]], ["min_thickness", 0.25],
                        ["filled_areas_thickness", "no"], ["keepout"] + rules,
                        ["fill", ["thermal_gap", 0.5], ["thermal_bridge_width", 0.5]],
                        ["polygon", ["pts"] + [["xy", round(ox + x, 4), round(oy + y, 4)] for x, y in pts]]])
    return out


def _pad_shapes(fp: dict, x: float, y: float, r: float, layers_all: list[str]):
    """(pad number, layers, polygon) of a footprint placed at x, y, r (pads as rectangles)."""
    from shapely.geometry import box
    out = []
    for p in fp["pads"]:
        if not p["number"]:
            continue
        w, h = (p["w"], p["h"]) if (p["rot"] + r) % 180 == 0 else (p["h"], p["w"])
        dx, dy = rot(p["x"], p["y"], r)
        lay = layers_all if p["kind"] == "thru_hole" else [L for L in p["layers"] if L in layers_all]
        out.append((p["number"], lay, box(x + dx - w / 2, y + dy - h / 2, x + dx + w / 2, y + dy + h / 2)))
    return out


def connected_nets(block: dict, libs, via_dia: float = 0.6) -> set[str]:
    """Block nets whose pads the block copper joins into one piece (nets the router need not touch)."""
    from shapely.geometry import LineString, Point
    layers = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    items: dict[str, list] = {}
    for role, r in (block.get("roles") or {}).items():
        q = block["placement"][role]
        fp = libs.footprint(r["footprint"])
        for num, lay, g in _pad_shapes(fp, float(q["x"]), float(q["y"]), float(q.get("rot", 0)), layers):
            n = pins(r).get(num)
            if n is not None:
                items.setdefault(n, []).append(("pad", set(lay), g))
    cu = block.get("copper") or {}
    for t in cu.get("tracks") or []:
        g = LineString([tuple(map(float, p)) for p in t["pts"]]).buffer(float(t["w"]) / 2)
        items.setdefault(str(t["net"]), []).append(("cu", {str(t["layer"])}, g))
    for v in cu.get("vias") or []:
        items.setdefault(str(v["net"]), []).append(("cu", set(layers), Point(float(v["x"]), float(v["y"])).buffer(via_dia / 2)))
    out = set()
    for net, its in items.items():
        pads = [i for i, x in enumerate(its) if x[0] == "pad"]
        if len(pads) < 2:
            continue
        seen, todo = {pads[0]}, [pads[0]]
        while todo:
            i = todo.pop()
            for j, x in enumerate(its):
                if j not in seen and its[i][1] & x[1] and its[i][2].intersects(x[2]):
                    seen.add(j)
                    todo.append(j)
        if all(i in seen for i in pads):
            out.add(net)
    return out


def closed_nets(design, libs) -> set[str]:
    """Design nets whose every pad is in one block and joined by its copper: the router leaves them alone."""
    conns = design.nets()
    out = set()
    for bnd in bindings(design):
        b, refs = bnd["block"], bnd["parts"]
        nm, bad = netmap(design, bnd)
        if bad:
            continue
        joined = connected_nets(b, libs, float(design.board["via_dia_mm"]))
        inside = {(r, p) for role, r in refs.items() for p in pins((b.get("roles") or {}).get(role, {}))}
        for bn, dn in nm.items():
            same = [x for x, y in nm.items() if y == dn]
            if all(x in joined for x in same) and len(same) == 1 and all((r, p) in inside for r, p in conns.get(dn, [])):
                out.add(dn)
    return out


# ------------------------------------------------------------------ extraction

def extract(board: Path, refs: list[str], name: str, source: str, roles: dict | None = None, design=None,
            libs=None, margin: float = 1.0) -> dict:
    """A block from a routed board: the parts' placement relative to the first one, the copper of the nets whose
    pads are all in the part set, and the port copper attached to the parts, clipped at the parts' courtyards and
    that copper grown by `margin`. The block courtyard is the box of the parts and the copper kept."""
    from shapely.geometry import LineString, Point, box

    from . import pcb
    rb = pcb.read_board(Path(board))
    fps = rb["footprints"]
    roles = roles or {r: r for r in refs}
    missing = [r for r in roles.values() if r not in fps]
    if missing:
        raise KeyError(f"not on the board: {', '.join(missing)}")
    S = set(roles.values())
    ref0 = next(iter(roles.values()))
    ox, oy = fps[ref0]["x"], fps[ref0]["y"]
    allpads: dict[str, set] = {}
    for ref, fp in fps.items():
        for p in fp["pads"]:
            if p["net"]:
                allpads.setdefault(p["net"], set()).add(ref)
    rl, pl, P = {}, {}, set()
    yard = None
    for role, ref in roles.items():
        fp = fps[ref]
        pmap = {}
        for p in fp["pads"]:
            if p["number"] and p["net"] and p["number"] not in pmap:
                pmap[p["number"]] = p["net"]
                if allpads[p["net"]] - S:
                    P.add(p["net"])
        c = design.components.get(ref) if design is not None else None
        rl[role] = {"ref": ref, **({"symbol": c.get("symbol")} if c is not None else {}), "footprint": fp["footprint"],
                    "value": str(c.get("value")) if c is not None else None, "pins": pmap}
        rl[role] = {k: v for k, v in rl[role].items() if v is not None}
        pl[role] = {"x": round(fp["x"] - ox, 4), "y": round(fp["y"] - oy, 4), "rot": fp["r"] % 360, "side": "front"}
        if libs is not None:
            b = libs.footprint(fp["footprint"])["sides"].get("front") or libs.footprint(fp["footprint"])["bbox"]
            pts = [rot(x, y, fp["r"]) for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))]
        else:
            pts = [(q["x"] - fp["x"] + s * q["w"] / 2, q["y"] - fp["y"] + t * q["h"] / 2)
                   for q in fp["pads"] for s in (-1, 1) for t in (-1, 1)]
        bx = box(min(x for x, _ in pts) + fp["x"], min(y for _, y in pts) + fp["y"],
                 max(x for x, _ in pts) + fp["x"], max(y for _, y in pts) + fp["y"])
        yard = bx if yard is None else yard.union(bx)
    nets = {n for r in rl.values() for n in r["pins"].values()}
    internal = nets - P
    tracks = [s for s in rb["segments"] if s["net"] in internal]
    vias = [v for v in rb["vias"] if v["net"] in internal]
    for s in tracks:
        yard = yard.union(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2).envelope)
    for v in vias:
        yard = yard.union(Point(v["x"], v["y"]).buffer(0.3).envelope)
    rect = yard.envelope.buffer(margin, join_style=2)
    # port copper: what joins a member pad of the net inside the courtyard rectangle
    via_r = (design.board["via_dia_mm"] if design is not None else 0.6) / 2
    for net in sorted(P):
        shapes = [(set(p["layers"]) if p["kind"] != "thru_hole" else {"*"},
                   box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2))
                  for r in S for p in fps[r]["pads"] if p["net"] == net]
        cand = []
        for s in rb["segments"]:
            if s["net"] != net:
                continue
            piece = LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).intersection(rect)
            if piece.geom_type == "LineString" and piece.length > 1e-3:
                (x1, y1), (x2, y2) = piece.coords[0], piece.coords[-1]
                cand.append(("t", dict(s, x1=x1, y1=y1, x2=x2, y2=y2), {s["layer"]}, piece.buffer(s["w"] / 2)))
        cand += [("v", v, {"*"}, Point(v["x"], v["y"]).buffer(via_r)) for v in rb["vias"]
                 if v["net"] == net and rect.contains(Point(v["x"], v["y"]))]
        grow = True
        while grow:
            grow = False
            for c in list(cand):
                if any((c[2] & L or "*" in c[2] or "*" in L) and c[3].intersects(g) for L, g in shapes):
                    shapes.append((c[2], c[3]))
                    (tracks if c[0] == "t" else vias).append(c[1])
                    cand.remove(c)
                    grow = True

    def rel(x, y):
        return [round(x - ox, 4), round(y - oy, 4)]

    for s in tracks:
        yard = yard.union(LineString([(s["x1"], s["y1"]), (s["x2"], s["y2"])]).buffer(s["w"] / 2).envelope)
    for v in vias:
        yard = yard.union(Point(v["x"], v["y"]).buffer(via_r).envelope)
    x0, y0, x1, y1 = yard.bounds
    rules = {k: float(design.board[k]) for k in ("clearance_mm", "via_dia_mm", "via_drill_mm")} if design is not None \
        else {"clearance_mm": 0.2, "via_dia_mm": 0.6, "via_drill_mm": 0.3}
    used = {t["layer"] for t in tracks}
    return {
        "name": name,
        "description": f"extracted from {Path(board).name}: {', '.join(f'{k}={v}' for k, v in roles.items())}",
        "source": source,
        "layers_min": 4 if used & {"In1.Cu", "In2.Cu"} else 2,
        "rules": rules,
        "ports": sorted(P),
        "roles": rl,
        "placement": pl,
        "copper": {"tracks": [{"net": t["net"], "layer": t["layer"], "w": t["w"], "pts": [rel(t["x1"], t["y1"]), rel(t["x2"], t["y2"])]}
                              for t in tracks],
                   "vias": [{"net": v["net"], "x": rel(v["x"], v["y"])[0], "y": rel(v["x"], v["y"])[1]} for v in vias]},
        "courtyard": [round(x0 - ox, 4), round(y0 - oy, 4), round(x1 - ox, 4), round(y1 - oy, 4)],
    }


def write(block: dict, root: Path | None = None) -> Path:
    """blocks/<name>/block.yaml, leaf lists and maps in flow style."""
    d = Path(root or library_root()) / block["name"]
    d.mkdir(parents=True, exist_ok=True)
    f = d / "block.yaml"
    f.write_text(yaml.safe_dump(block, sort_keys=False, default_flow_style=None, width=120))
    return f


# ------------------------------------------------------------------ standalone check

def verify(name: str, workdir: Path, design=None) -> dict:
    """Place the block alone on a small board (instantiate mode, ports as single nets) and run KiCad DRC on the
    unrouted board, which holds only the block copper. Returns {ok, errors, warnings, drc, board, joined}."""
    from . import kicad, pcb, placer, postlayout
    from .design import Design
    from .libs import Libs
    b = load(name, design)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    x0, y0, x1, y1 = courtyard(b)
    m = 3.0
    rules = b.get("rules") or {}
    P = ports(b)
    gnd = "GND" if "GND" in P else None
    board = {"width_mm": round(x1 - x0 + 2 * m, 2), "height_mm": round(y1 - y0 + 2 * m, 2),
             "layers": int(b.get("layers_min", 2)), "gnd_pour": gnd is not None, "margin_mm": 0.5,
             **{k: float(v) for k, v in rules.items()}}
    nets = {n: {"allow_single": True, **({"class": "ground"} if n == gnd else {})} for n in sorted(P)}
    raw = {"project": f"block_{name}", "board": board, "nets": nets,
           "blocks": {"t": {"use": name, "nets": {n: n for n in sorted(P)},
                            "at": {"x": round(m - x0, 4), "y": round(m - y0, 4), "rot": 0, "fixed": True}}}}
    (workdir / "design.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    if b.get("dir") and (Path(b["dir"]).parent != library_root()):  # a project block: look it up from the project
        (workdir / "blocks").mkdir(exist_ok=True)
        link = workdir / "blocks" / name
        if not link.exists():
            link.symlink_to(Path(b["dir"]).resolve())
    d = Design.load(workdir)
    libs = Libs(design=d)
    parts = placer.build_parts(d, libs)
    out = workdir / "build"
    out.mkdir(exist_ok=True)
    pcb.write_project(out, d.name, d)
    f = out / f"{d.name}.kicad_pcb"
    pcb.write_board(f, pcb.build_board(d, libs, parts))
    drc = kicad.drc(f, refill=True)
    errs = [x for x in drc if x["sev"] == "error"]
    # the block copper must also be one piece per net for the post-layout extraction (copper overlap, as KiCad)
    split = {n: k for n, k in postlayout.Extraction(d, f).islands().items() if k > 1}
    return {"ok": not errs and not split, "errors": len(errs), "warnings": len(drc) - len(errs), "drc": drc,
            "board": str(f), "split": split, "joined": sorted(connected_nets(b, libs, float(d.board["via_dia_mm"])))}


def describe(name: str, design=None) -> str:
    b = load(name, design)
    x0, y0, x1, y1 = courtyard(b)
    cu = b.get("copper") or {}
    L = [f"{b['name']}: {str(b.get('description', '')).strip()}",
         f"  source: {str(b.get('source', '')).strip()}",
         f"  ports: {', '.join(sorted(ports(b)))}; internal nets: {', '.join(sorted(block_nets(b) - ports(b))) or '-'}",
         f"  courtyard {x1 - x0:.2f} x {y1 - y0:.2f} mm, layers_min {b.get('layers_min', 2)}, "
         f"{len(cu.get('tracks') or [])} tracks, {len(cu.get('vias') or [])} vias, rules {b.get('rules') or {}}"]
    for role, r in (b.get("roles") or {}).items():
        q = (b.get("placement") or {}).get(role, {})
        val = r.get("value") or "/".join(f"{k} {r[k]}" for k in ("value_min", "value_max") if k in r)
        L.append(f"  {role:<6} {r.get('ref', ''):<5} {r.get('footprint')} {val} at ({q.get('x')}, {q.get('y')}) "
                 f"rot {q.get('rot', 0)}  pins {r.get('pins')}")
    for k, v in (b.get("resistance_mohm") or {}).items():
        L.append(f"  copper resistance {k}: {v} mOhm")
    return "\n".join(L)
