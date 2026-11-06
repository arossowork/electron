"""The extreme routing round: the last resort of a layout that the router and the repair loop left with DRC errors or
open connections (stages.stage_layout, after the repair; HWFLOW_EXTREME=0: off). Four steps, each kept only when
the full DRC has fewer errors after it:

1. narrower tracks: every net with no electrical reason for its width is routed again at EXTREME_TRACK_MM (the board
   minimum that pcb.write_project gives KiCad). A net keeps its width when it has `impedance_ohm` (its width sets
   Z0), `current_a` (IPC-2221), `min_width_mm` / `min_width_inner_mm`, `class: power`, or a layout budget width; a
   net already at or under the minimum is not touched;
2. via-in-pad stitching: a split pour (an island the repair loop found no via room for) gets a via that may sit in a
   pad of its own net (repair.stitch_vias `in_pad`). The fab then has to fill and cap those vias (IPC-4761 type VII),
   so REPORT.md names them;
3. pour taps on the walled-in pads: a pad of the pour in a piece that no via reaches (step 2 found no room) has its
   part's pour pads tapped (taps.plan, `board.pour_taps` set to those parts for this route) on the unrouted board,
   and every signal net is routed again around the taps (a QFP's ground pin walled in by its neighbours' fan-outs);
4. pour stitch: a split pour still left (a tap via the re-routed tracks cut off) gets the pour stitch
   (pourstitch.stitch: a stub to a via, displacing at most 3 routed nets that are routed again), when the layout did
   not run it already (settings.layout.pour_stitch, board.pour_stitch).

The narrowed widths stay in the design (design.yaml's nets in memory) for the rest of the layout and the
post-layout checks, as copper_edge_mm of the extreme placement does. No LLM, no re-placement."""
from __future__ import annotations

from pathlib import Path

from ..board import pcb
from . import pourstitch, repair

EXTREME_TRACK_MM = 0.15  # pcb.write_project's min_track_width floor (JLCPCB 2-layer: 0.127 mm)
MAX_NETS = 60  # the narrow step re-routes at most this many nets: a last resort for a small board, not a keyboard


def _errors(drc) -> int:
    return sum(1 for x in drc if x["sev"] == "error")


def narrowable(d) -> dict[str, float]:
    """net -> its current width, for the nets the extreme round may route at EXTREME_TRACK_MM."""
    out = {}
    budget = getattr(d, "budget_widths", None) or {}
    for net, c in pcb.net_classes(d).items():
        info = d.net_info(net)
        if (info.get("impedance_ohm") is not None or info.get("current_a") or info.get("min_width_mm") is not None
                or info.get("min_width_inner_mm") is not None or c["class"] == "power" or net in budget
                or net in set(d.ground_nets()) and d.board["gnd_pour"]):
            continue
        if c["width"] > EXTREME_TRACK_MM + 1e-6:
            out[net] = c["width"]
    return out


def _walled(d, board: Path, tracks: list, vias: list, drc: list, check) -> set:
    """Refs with a pad of a pour net in a split-off piece of the pour that no stitching via reaches (repair.stitch_vias,
    via in pad allowed, found no room: a QFP's ground pin between its neighbours' fan-outs), when the board taps no
    pour pad yet (taps.on): step 3 taps those parts' pour pads before routing, the fan-outs then go around the via."""
    from . import taps
    if taps.on(d) or not d.board.get("gnd_pour"):
        return set()
    nets = repair.plan(d, tracks, vias, drc)["islands"]
    if not nets:
        return set()
    check(tracks, vias)  # the board file filled with the copper given
    fill, rb = repair.zone_fill(board), pcb.read_board(board)
    out = set()
    for net in nets:
        v, left, _ = repair.stitch_vias(d, board, net, in_pad=True)
        if not left:
            continue
        nodes, grp, _ = repair._groups(net, fill, rb)
        area: dict = {}
        for i, g in enumerate(grp):
            area[g] = area.get(g, 0.0) + nodes[i][1].area
        main = max(area, key=area.get) if area else None
        from shapely.geometry import box
        for ref, fp in rb["footprints"].items():
            for p in fp["pads"]:
                if p["net"] != net or p["kind"] != "smd":
                    continue
                pb = box(p["x"] - p["w"] / 2, p["y"] - p["h"] / 2, p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
                hit = [grp[i] for i, (ly, g) in enumerate(nodes) if ly in p["layers"] and g.intersects(pb)]
                if hit and main not in hit:
                    out.add(ref)
    return out


def _set_widths(d, widths: dict[str, float | None]):
    nets = d.raw.setdefault("nets", {})
    for n, w in widths.items():
        info = nets.get(n)
        info = dict(info) if isinstance(info, dict) else {}
        if w is None:
            info.pop("width_mm", None)
        else:
            info["width_mm"] = w
        nets[n] = info


def second_round(d, libs, parts, workdir: Path, board: Path, tracks: list, vias: list, drc: list, check,
                 deadline: float | None = None) -> dict | None:
    """`check(tracks, vias)` writes the board and returns its DRC; `deadline` (time.time()) stops the router. Returns
    {tracks, vias, drc, narrowed, via_in_pad, log, kept}, `kept` False when no step removed errors (the board file
    then holds the copper it was called with), or None when there was nothing to try."""
    best = (tracks, vias, drc)
    log, narrowed, in_pad = [], {}, []
    nets = narrowable(d)
    if len(nets) > MAX_NETS:
        log.append(f"narrow tracks: {len(nets)} nets, more than {MAX_NETS}: skipped")
        nets = {}
    if nets:
        old = {n: (d.net_info(n) or {}).get("width_mm") for n in nets}
        _set_widths(d, {n: EXTREME_TRACK_MM for n in nets})
        try:
            keep_t = [t for t in tracks if t["net"] not in nets]
            keep_v = [v for v in vias if v["net"] not in nets]
            tr, vi = repair.reroute(d, libs, parts, workdir, keep_t, keep_v, sorted(nets), attempt=0, island=0,
                                     deadline=deadline)
            dr = check(tr, vi)
        except Exception as e:  # noqa: BLE001 - a router failure: the round keeps the copper it had
            log.append(f"narrow tracks: {type(e).__name__}: {str(e)[:100]}")
            dr = None
        if dr is not None and _errors(dr) < _errors(best[2]):
            log.append(f"{len(nets)} nets at {EXTREME_TRACK_MM:g} mm: {_errors(best[2])} -> {_errors(dr)} errors")
            best, narrowed = (tr, vi, dr), {n: nets[n] for n in nets}
        else:
            if dr is not None:
                log.append(f"{len(nets)} nets at {EXTREME_TRACK_MM:g} mm: {_errors(best[2])} -> {_errors(dr)} errors, "
                           "not kept")
            _set_widths(d, old)
    tr, vi, dr = best
    islands = repair.plan(d, tr, vi, dr)["islands"]
    if islands:
        check(tr, vi)  # the board file holds the copper kept, filled
        new = []
        for net in islands:
            v, _, _ = repair.stitch_vias(d, board, net, in_pad=True)
            new += v
        if new:
            dr2 = check(tr, vi + new)
            if _errors(dr2) < _errors(dr):
                log.append(f"{len(new)} stitching vias, via-in-pad allowed: {_errors(dr)} -> {_errors(dr2)} errors")
                best, in_pad = (tr, vi + new, dr2), new
            else:
                log.append(f"{len(new)} via-in-pad stitching vias: {_errors(dr)} -> {_errors(dr2)} errors, not kept")
    tr, vi, dr = best
    walled = _walled(d, board, tr, vi, dr, check)
    if walled:  # 3. the walled-in pour pads tapped first, the signal nets routed again around the taps
        from . import krt
        raw = d.raw.setdefault("board", {})  # Design.board is a copy
        old = raw.get("pour_taps")
        raw["pour_taps"] = sorted(walled)
        dr3 = None
        try:
            wd = workdir / "extreme_taps"
            wd.mkdir(exist_ok=True)
            tr3, vi3, _, _ = krt.run_krt(d, libs, parts, wd, deadline=deadline)
            dr3 = check(tr3, vi3)
        except Exception as e:  # noqa: BLE001 - a router failure: the round keeps the copper it had
            log.append(f"taps on {', '.join(sorted(walled))}: {type(e).__name__}: {str(e)[:100]}")
        finally:
            if old is None:
                raw.pop("pour_taps", None)
            else:
                raw["pour_taps"] = old
        if dr3 is not None:
            ok = _errors(dr3) < _errors(dr)
            log.append(f"re-routed with pour taps on {', '.join(sorted(walled))}: {_errors(dr)} -> {_errors(dr3)} "
                       f"errors{'' if ok else ', not kept'}")
            if ok:
                best, in_pad = (tr3, vi3, dr3), []
            else:
                check(tr, vi)  # the board file back to the copper kept
    tr, vi, dr = best
    from ...core.settings import get_settings
    if (pourstitch.split(d, dr) and d.board["gnd_pour"]
            and not (get_settings().layout.pour_stitch or d.board.get("pour_stitch"))):
        try:  # 4. what step 3 left split (a tap via cut off by the re-routed tracks)
            ps = pourstitch.stitch(d, libs, parts, board, tr, vi, dr, check, deadline=deadline)
        except Exception as e:  # noqa: BLE001 - a router failure: the round keeps the copper it had
            log.append(f"pour stitch: {type(e).__name__}: {str(e)[:100]}")
            ps = None
        if ps is not None and _errors(ps["drc"]) < _errors(dr):
            log.append(f"pour stitch, {ps['added'][1]} vias and {ps['added'][0]} stubs: {_errors(dr)} -> "
                       f"{_errors(ps['drc'])} errors")
            best = (ps["tracks"], ps["vias"], ps["drc"])
        elif ps is not None:
            log.append(f"pour stitch: {_errors(dr)} -> {_errors(ps['drc'])} errors, not kept")
            if (ps["tracks"], ps["vias"]) != (tr, vi):
                check(tr, vi)  # the board file back to the copper kept
    if best[0] is tracks and best[1] is vias:
        check(tracks, vias)  # the board file back to the copper it was called with
        return None if not log else {"tracks": tracks, "vias": vias, "drc": drc, "narrowed": {}, "via_in_pad": [],
                                     "log": log, "kept": False}
    return {"tracks": best[0], "vias": best[1], "drc": best[2], "narrowed": narrowed,
            "via_in_pad": [v for v in in_pad if v in best[1]], "log": log, "kept": True}
