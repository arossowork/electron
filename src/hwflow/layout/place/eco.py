"""Incremental (ECO) placement: a small edit after a clean layout keeps the last good placement.

A layout that converged (0 unrouted, 0 DRC errors, a legal placement) leaves `build/placement.good.json`: the
position of every part, its footprint and the board size. A later build of the same board (same declared size)
starts from it instead of placing from scratch, when the edit touches few parts:

- a movable part with the same footprint keeps its last good position (and rotation);
- a part that is new, changed footprint or left a block is *touched*: it starts next to the parts it connects to;
- a part the design now fixes (a component `placement` or a block `at`) takes its new position;
- the parts in conflict with the new constraints (a fixed part or block landing on them, a new keep-out or region)
  are the violators of the seeded placement: `Placer.settle` takes them (and only when needed, their neighbours) out
  and puts them back on the nearest free spot;
- the units that moved get a short greedy detailed pass (`Placer.detailed`) for their wire length.

Routing is redone on the result (the route cache still serves the route groups that did not change). When more than
`MAX_TOUCHED` of the movable parts are touched, or the board size changed, or the seeded placement keeps a
violation, the caller places from scratch as before. `HWFLOW_ECO=0` (settings `layout.eco`) turns the path off.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

GOOD = "placement.good.json"
MAX_TOUCHED = 0.25  # share of the movable parts an edit may touch and still keep the last good placement


def save_good(out: Path, d, libs, parts: dict | None = None, placement: Path | None = None) -> Path | None:
    """Write build/placement.good.json from the kept layout's placement (`placement`: a placement.json, default
    out/placement.json). The footprints come from the design, so a later swap to another footprint is seen."""
    src = Path(placement) if placement is not None else Path(out) / "placement.json"
    try:
        doc = json.loads(src.read_text())
    except (OSError, ValueError):
        return None
    fps = {r: str(c.get("footprint")) for r, c in d.components.items()}
    doc["__footprints__"] = {r: fps[r] for r in doc if not r.startswith("__") and r in fps}
    dst = Path(out) / GOOD
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(dst)
    return dst


def load_good(path: Path) -> dict | None:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and any(not k.startswith("__") for k in doc) else None


def _seed_xy(d, parts, ref, placed):
    """Mean position of the placed parts that share a signal net with `ref` (power and ground nets connect too
    much to say where a part belongs), else of every net neighbour, else the board centre."""
    gnds = set(d.ground_nets())
    near, weak = [], []
    for net, conns in d.nets().items():
        refs = {r for r, _ in conns}
        if ref not in refs or net in gnds:
            continue
        cls = (d.net_info(net) or {}).get("class")
        others = [parts[r] for r in sorted(refs) if r != ref and r in placed and r in parts]  # sorted: same sum bits
        (weak if cls == "power" or len(refs) > 8 else near).extend(others)
    pts = near or weak
    if not pts:
        return d.board["width_mm"] / 2, d.board["height_mm"] / 2
    return sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)


def place(d, libs, good: dict, deadline: float | None = None):
    """The incremental placement of design `d` from the last good placement `good` (load_good). Returns
    (parts, cost, violations, info), or None with info printed when the edit is too large for it (the caller places
    from scratch). info: {kept (stayed where they were), seeded, touched, conflicts, moved, movable, max_shift_mm,
    violations, seconds}."""
    from . import placer
    t0 = time.time()
    W, H = d.board["width_mm"], d.board["height_mm"]
    size = good.get("__board_mm__")
    if size is not None and [float(v) for v in size] != [float(W), float(H)]:
        print(f"[place] incremental: the last good placement is for another board size "
              f"({size} mm, now {[W, H]}): placing from scratch")
        return None
    fps = good.get("__footprints__") or {}
    parts = placer.build_parts(d, libs)
    units = {u.ref: u for u in getattr(parts, "blocks", ())}
    block_of = {r: u for u in units.values() for r, *_ in u.members}
    movable = [r for r, p in parts.items() if not p.fixed]
    kept, touched = set(), []
    for r in movable:
        p = parts[r]
        same_fp = r not in fps or fps[r] == str(d.components[r].get("footprint"))
        if r in good and same_fp:
            p.x, p.y, p.r = (float(v) for v in good[r])
            kept.add(r)
        else:
            touched.append(r)
    # a movable block keeps its rigid shape: it is touched as a whole when any member is (Placer pulls the block
    # origin from its first member, so the members must agree)
    for u in units.values():
        if u.fixed:
            continue
        refs = [m[0] for m in u.members]
        if any(r in touched for r in refs):
            for r in refs:
                if r in kept:
                    kept.discard(r)
                    touched.append(r)
    n = max(len(movable), 1)
    if len(touched) > MAX_TOUCHED * n:
        print(f"[place] incremental: {len(touched)} of {n} movable parts are new or changed (more than "
              f"{MAX_TOUCHED:.0%}): placing from scratch")
        return None
    placed = set(kept) | {r for r, p in parts.items() if p.fixed}
    seeded = set()
    for r in touched:  # blocks: the first member's seed places the block (Placer.pull), the rest follow
        u = block_of.get(r)
        if u is not None and u.ref in seeded:
            continue
        x, y = _seed_xy(d, parts, r, placed)
        if u is not None:
            ref0, dx, dy, dr = u.members[0]
            parts[ref0].x, parts[ref0].y, parts[ref0].r = x, y, dr
            seeded.add(u.ref)
        else:
            parts[r].x, parts[r].y = x, y
    pl = placer.Placer(d, parts, W, H)
    before = pl._state()
    tu, seen = [], set()
    for r in touched:  # the new and changed units first, on the free spot nearest to their seed
        u = pl._unit(pl.parts[r])
        if id(u) not in seen:
            seen.add(id(u))
            tu.append(u)
    from ...core.settings import get_settings
    settle = get_settings().layout.settle
    if tu and settle:
        pl._settle(tu)
    v0 = pl.violations()
    conflicts = sorted({getattr(u, "ref", "") for u in pl._violators(v0)} - {""} - set(touched))
    viol = v0
    if viol and settle:  # the kept parts in conflict with the edit: the nearest free spot, then their neighbourhood
        viol = pl.settle()
    moved = [u for u in pl._movable()
             if any(abs(before[r][0] - pl.parts[r].x) > 1e-6 or abs(before[r][1] - pl.parts[r].y) > 1e-6
                    or before[r][2] != pl.parts[r].r for r in pl._refs(u) if r in before)
             or any(r in touched for r in pl._refs(u))]
    if moved and not viol and deadline is not None and time.time() >= deadline:
        placer.safety_abort("incremental placement: detailed moves skipped at the layout budget")
    elif moved and not viol:
        s = pl._state()
        pl.detailed(moved)
        v2 = pl.violations()
        if v2:  # never trade legality for wire length
            pl._restore(s)
    cost = pl.cost()
    viol = pl.violations()
    end = pl._state()
    moved_refs = sorted(r for r in movable if r in parts and r in before and (
        abs(before[r][0] - end[r][0]) > 1e-6 or abs(before[r][1] - end[r][1]) > 1e-6 or before[r][2] != end[r][2]))
    stayed = sum(1 for r in kept if r not in moved_refs)
    shift = max((abs(before[r][0] - end[r][0]) + abs(before[r][1] - end[r][1]) for r in kept), default=0.0)
    info = {"kept": stayed, "seeded": len(kept), "touched": sorted(touched), "moved": moved_refs,
            "conflicts": conflicts, "movable": n, "max_shift_mm": round(shift, 2), "violations": len(viol),
            "seconds": round(time.time() - t0, 1)}
    print(f"[place] incremental: {stayed} of {n} movable parts stayed at their last good positions; "
          f"{len(touched)} new or changed{' (' + ', '.join(sorted(touched)[:8]) + ')' if touched else ''}; "
          f"{len(conflicts)} in conflict with the edit{' (' + ', '.join(conflicts[:8]) + ')' if conflicts else ''}; "
          f"{len(moved_refs)} re-placed (largest move of a kept part {shift:.1f} mm); {len(viol)} violations "
          f"({info['seconds']} s)")
    return parts, cost, viol, info
