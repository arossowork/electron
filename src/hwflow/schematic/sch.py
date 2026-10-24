"""KiCad schematic generation from design.yaml (symbols + net labels, grouped by function)."""

from __future__ import annotations

import uuid
from pathlib import Path

from ..core import sexpr
from ..core.design import Design, natkey
from ..core.sexpr import Q

SCH_VERSION = "20250114"
G = 1.27


_SEQ: list = [None]  # (namespace, next index) while build_schematic runs: UUIDs are a function of the design


def uid() -> Q:
    """A KiCad UUID. Inside build_schematic it is uuid5(design namespace, running index), so the same design gives
    the same schematic bytes (a content-addressed output, ADR 0001 2.4); elsewhere a random one."""
    if _SEQ[0] is None:
        return Q(str(uuid.uuid4()))
    ns, n = _SEQ[0]
    _SEQ[0] = (ns, n + 1)
    return Q(str(uuid.uuid5(ns, str(n))))


def snap(v: float) -> float:
    return round(round(v / G) * G, 4)


def _font(hide=False, justify=None):
    e = ["effects", ["font", ["size", 1.27, 1.27]]]
    if justify:
        e.append(["justify"] + justify)
    if hide:
        e.append(["hide", "yes"])
    return e


def _prop(name, val, x, y, hide=False, justify=None):
    return ["property", Q(name), Q(str(val)), ["at", round(x, 4), round(y, 4), 0], _font(hide, justify)]


def _unit_pins(sym: dict, unit: int) -> list[dict]:
    return [p for p in sym["pins"] if p["unit"] in (0, unit)]


def _label_len(net: str) -> float:
    return 1.27 * 0.8 * len(net) + 3.0


def _extent(pins, nets: dict | None = None):
    """Bounding box of pin endpoints plus the net labels that grow out of them (screen coordinates)."""
    if not pins:
        return -2.54, -2.54, 2.54, 2.54
    nets = nets or {}
    xs, ys = [], []
    for p in pins:
        x, y = p["x"], -p["y"]
        xs.append(x)
        ys.append(y)
        L = _label_len(nets.get(p["number"], ""))
        ang = int((p["angle"] + 180) % 360)
        dx, dy = {0: (L, 0), 180: (-L, 0), 90: (0, -L), 270: (0, L)}[ang]
        xs.append(x + dx)
        ys.append(y + dy)
    return min(xs), min(ys), max(xs), max(ys)


LABEL_SPACE = 14.0


def _glabel(net, x, y, ang, just):
    """Global label: net names stay unprefixed, so schematic and PCB nets match exactly."""
    j = [just[0]]
    return ["global_label", Q(net), ["shape", "passive"], ["at", x, y, ang], ["fields_autoplaced", "yes"],
            ["effects", ["font", ["size", 1.27, 1.27]], ["justify"] + j], ["uuid", uid()]]


def build_schematic(design: Design, libs) -> list:
    _SEQ[0] = (uuid.uuid5(uuid.NAMESPACE_URL, f"hwflow:{design.name}"), 0)
    try:
        return _build_schematic(design, libs)
    finally:
        _SEQ[0] = None


def _build_schematic(design: Design, libs) -> list:
    root = uid()
    lib_symbols = ["lib_symbols"]
    embedded = set()
    items = []
    instances = []  # (ref, unit, sym, comp)
    for c in sorted(design.components.values(), key=lambda c: natkey(c.ref)):
        if c.get("virtual"):
            continue
        sym = libs.symbol(c.get("symbol"))
        if c.get("symbol") not in embedded:
            lib_symbols.append(sym["tree"])
            embedded.add(c.get("symbol"))
        for u in sym["units"]:
            instances.append((c.ref, u, sym, c))

    # power flags for externally supplied nets with no power_out driver
    flags = []
    pf = None
    for net, conns in design.nets().items():
        types = []
        for ref, pin in conns:
            s = libs.symbol(design.components[ref].get("symbol"))
            types += [p["type"] for p in s["pins"] if p["number"] == pin]
        needs = ("power_in" in types and "power_out" not in types) or net in design.ground_nets()
        if needs:
            if pf is None:
                pf = libs.symbol("power:PWR_FLAG")
                lib_symbols.append(pf["tree"])
            flags.append(net)

    # group layout: each group is a column of symbols
    groups: dict[str, list] = {}
    for inst in instances:
        groups.setdefault(inst[3].get("group") or "main", []).append(inst)
    x_cursor = 25.4
    for gname, insts in groups.items():
        y_cursor = 30.48
        col_w = 0.0
        items.append(["text", Q(gname.upper()), ["exclude_from_sim", "no"], ["at", snap(x_cursor), snap(y_cursor - 8), 0],
                      ["effects", ["font", ["size", 2.0, 2.0], ["bold", "yes"]], ["justify", "left"]], ["uuid", uid()]])
        for ref, unit, sym, c in insts:
            pins = _unit_pins(sym, unit)
            x0, y0, x1, y1 = _extent(pins, c.pins)
            w = (x1 - x0) + 12.7
            h = (y1 - y0) + 7.62
            if y_cursor + h > 270:
                x_cursor += col_w + 10
                y_cursor, col_w = 30.48, 0.0
            sx = snap(x_cursor - x0)
            sy = snap(y_cursor - y0 + 2.54)
            col_w = max(col_w, w)
            y_cursor += h
            props = sym["props"]
            node = ["symbol", ["lib_id", Q(c.get("symbol"))], ["at", sx, sy, 0], ["unit", unit],
                    ["exclude_from_sim", "no"], ["in_bom", "no" if c.get("dnp") else "yes"],
                    ["on_board", "yes"], ["dnp", "yes" if c.get("dnp") else "no"], ["uuid", uid()],
                    _prop("Reference", ref, sx + x1 + 1.27, sy + y0 - 1.27, justify=["left"]),
                    _prop("Value", c.get("value"), sx + x1 + 1.27, sy + y1 + 2.54, justify=["left"]),
                    _prop("Footprint", c.get("footprint", ""), sx, sy, hide=True),
                    _prop("Datasheet", c.get("datasheet", props.get("Datasheet", "~")), sx, sy, hide=True)]
            for k in ("mpn", "lcsc", "manufacturer"):
                if c.get(k):
                    node.append(_prop(k.upper() if k != "manufacturer" else "Manufacturer", c.get(k), sx, sy, hide=True))
            for p in pins:
                node.append(["pin", Q(p["number"]), ["uuid", uid()]])
            node.append(["instances", ["project", Q(design.name), ["path", Q(f"/{root}"), ["reference", Q(ref)],
                                                                     ["unit", unit]]]])
            items.append(node)
            nc = {str(x) for x in (c.get("nc") or [])}
            for p in pins:
                px, py = round(sx + p["x"], 4), round(sy - p["y"], 4)
                net = c.pins.get(p["number"])
                if net is None or p["number"] in nc or (net or "").upper() == "NC":
                    if p["type"] != "no_connect" and not p["hidden"]:
                        items.append(["no_connect", ["at", px, py], ["uuid", uid()]])
                    continue
                ang = (p["angle"] + 180) % 360
                just = {0: ["left", "bottom"], 90: ["left", "bottom"], 180: ["right", "bottom"], 270: ["right", "bottom"]}[int(ang)]
                items.append(_glabel(net, px, py, ang, just))
        x_cursor += col_w + 10

    # power flags in their own column
    fx = snap(x_cursor + 10)
    fy = 30.48
    for i, net in enumerate(flags):
        sy = snap(fy + i * 7.62)
        items.append(["symbol", ["lib_id", Q("power:PWR_FLAG")], ["at", fx, sy, 0], ["unit", 1],
                      ["exclude_from_sim", "no"], ["in_bom", "yes"], ["on_board", "yes"], ["dnp", "no"],
                      ["uuid", uid()],
                      _prop("Reference", f"#FLG{i + 1:02d}", fx, sy - 3.81, hide=True),
                      _prop("Value", "PWR_FLAG", fx, sy - 3.81),
                      _prop("Footprint", "", fx, sy, hide=True), _prop("Datasheet", "~", fx, sy, hide=True),
                      ["pin", Q("1"), ["uuid", uid()]],
                      ["instances", ["project", Q(design.name), ["path", Q(f"/{root}"), ["reference", Q(f"#FLG{i + 1:02d}")],
                                                                 ["unit", 1]]]]])
        items.append(_glabel(net, fx, sy, 270, ["right"]))

    tree = ["kicad_sch", ["version", SCH_VERSION], ["generator", Q("eeschema")], ["generator_version", Q("9.0")],
            ["uuid", root], ["paper", Q("A3")],
            ["title_block", ["title", Q(design.name)], ["rev", Q(str(design.raw.get("revision", "A")))],
             ["comment", 1, Q("Generated by hwflow from design.yaml. Edit design.yaml, not this file.")]],
            lib_symbols] + items + [["sheet_instances", ["path", Q("/"), ["page", Q("1")]]],
                                    ["embedded_fonts", "no"]]
    return tree


def write_schematic(design: Design, libs, path: Path):
    path.write_text(sexpr.dumps(build_schematic(design, libs)) + "\n")
