"""Budget trunks: a copper_r budget's width on its path only (budgets.trunks), not on every pad of the net.

A resistance budget asks for a width on the copper between its source and sink pads (the trunk). The other pads of
the net (decouplers, IC supply pins) need no more than the net's class width. krt_route routes each trunk tier first,
at its width, on a board where the net's pads off the trunk are moved to a stand-in net (`<net>~off`, which the pass
does not route, so they are only obstacles); the stand-in pads go back to the net afterwards, and the ordinary pass of
the net, with the trunk copper kept, joins the other pads to it at the class width."""

from __future__ import annotations

from pathlib import Path

from ...core import sexpr
from ...core.sexpr import Q

OFF = "~off"


def _ref(fp) -> str | None:
    ref = next((str(p[2]) for p in sexpr.find_all(fp, "property") if str(p[1]) == "Reference"), None)
    return ref or next((str(p[2]) for p in sexpr.find_all(fp, "fp_text") if str(p[1]) == "reference"), None)


def _net_table(t) -> dict[str, int]:
    return {str(n[2]): int(n[1]) for n in t if isinstance(n, list) and n and n[0] == "net" and len(n) > 2}


def _set(node: list, name: str, table: dict[str, int]):
    """Set a (net ...) node to `name`: (net <idx> "name") in a board with a net table, else (net "name")."""
    if len(node) > 2 or (len(node) == 2 and str(node[1]).isdigit() and not isinstance(node[1], Q)):
        del node[1:]
        node += [table[name], Q(name)]
    else:
        node[1] = Q(name)


def _name(node, table_inv: dict[int, str]) -> str | None:
    if node is None or len(node) < 2:
        return None
    if len(node) > 2:
        return str(node[2])
    v = node[1]
    return table_inv.get(int(v)) if str(v).isdigit() and not isinstance(v, Q) else str(v)


def split(src: Path, dst: Path, keep: dict[str, set]) -> Path:
    """`src` with every pad of a net of `keep` that is not in keep[net] ((ref, pad) pairs) on `<net>~off`."""
    t = sexpr.loads(Path(src).read_text())
    table = _net_table(t)
    inv = {v: k for k, v in table.items()}
    if table:  # an indexed board: the stand-in nets get entries after the last one
        at = max(i for i, x in enumerate(t) if isinstance(x, list) and x and x[0] == "net")
        for k, net in enumerate(sorted(keep)):
            table[net + OFF] = max(table.values()) + 1
            t.insert(at + 1 + k, ["net", table[net + OFF], Q(net + OFF)])
    for fp in sexpr.find_all(t, "footprint"):
        ref = _ref(fp)
        for p in sexpr.find_all(fp, "pad"):
            n = sexpr.find(p, "net")
            net = _name(n, inv)
            if net in keep and (ref, str(p[1])) not in keep[net]:
                _set(n, net + OFF, table)
    Path(dst).write_text(sexpr.dumps(t) + "\n")
    return Path(dst)


def join(src: Path, dst: Path) -> Path:
    """`src` (a router output of a split board) with the stand-in pads back on their nets."""
    text = Path(src).read_text()
    if OFF not in text:
        if Path(src) != Path(dst):
            Path(dst).write_text(text)
        return Path(dst)
    t = sexpr.loads(text)
    table = _net_table(t)
    inv = {v: k for k, v in table.items()}
    t = [x for x in t if not (isinstance(x, list) and x and x[0] == "net" and len(x) > 2 and str(x[2]).endswith(OFF))]

    def walk(node):
        for x in node:
            if isinstance(x, list) and x:
                if x[0] == "net":
                    name = _name(x, inv)
                    if name and name.endswith(OFF):
                        _set(x, name[: -len(OFF)], table)
                else:
                    walk(x)
    walk(t)
    Path(dst).write_text(sexpr.dumps(t) + "\n")
    return Path(dst)


def tiers(design, nets, classes: dict, skip: set) -> list[tuple[float, dict[str, set]]]:
    """[(width, {net: trunk pads})], widest first: the trunk tiers (design.budget_trunks) of the nets in `nets`
    (None: all) wider than the net's class width, `skip` (pour nets) left out."""
    by_w: dict[float, dict[str, set]] = {}
    for net, lst in (getattr(design, "budget_trunks", None) or {}).items():
        if net in skip or (nets is not None and net not in nets) or net not in classes:
            continue
        for w, pads in lst:
            if w > classes[net]["width"] + 1e-9 and len(pads) >= 2:
                by_w.setdefault(round(float(w), 3), {})[net] = {(str(r), str(p)) for r, p in pads}
    return sorted(by_w.items(), key=lambda x: -x[0])
