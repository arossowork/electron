"""Layout diagnosis: can more layout attempts fix the failures of a layout build, or does the loop go back to an earlier
stage? Deterministic, from the boards of the build's attempts and the builds before it.

1. Attribution: every DRC error and unrouted item is put on the parts it touches (a pad's part, else the parts with a
   pad within ATTRIB_MM of the item).
2. Persistence: a part is persistent when the failures land on it in at least PERSIST_SHARE of the independent
   attempts of the build (placer seeds). A failure that moves from part to part between seeds is placement or router
   luck; one that stays is not.
3. Physics at the persistent parts:
   - escape: a pad whose net class is wider than the widest track that leaves the pad straight between its foreign
     neighbours. One such pad necks down (routing.NECK_MAX_MM); ESCAPE_PINS or more on one part, or a pad that no
     track of the board's minimum width can leave, is a package choice.
   - board fill: courtyard area over the board face (floorplan.FILL_MAX).
4. History: build/layout_history.json keeps every layout build with the structure key of the design (parts,
   footprints, nets, net widths, board size and layers; not placement anchors or router settings). Two failing builds
   with the same structure key whose persistent parts overlap mean the layout cycle has nothing left to try: the next
   layout build is blocked until the structure changes (`hwflow build --retry-layout` overrides it once).

The verdict names the stage to go back to: `parts` (a package or part choice), `circuit` (net widths or currents),
`architecture` (board size, layers, part count: a product decision), or `layout` (one more placement cycle is still
worth it), with the numbers behind it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

ATTRIB_MM = 1.5       # a failure without a pad is put on the parts with a pad this close to it
PERSIST_SHARE = 0.75  # share of the attempts a part must fail in to be persistent
ESCAPE_PINS = 2       # pads on one part that need a neck before the package itself is the problem
PAD_RE = re.compile(r"Pad (\S+) \[([^\]]*)\] of (\S+)")
STAGES = {"parts": "stage 3 (parts)", "circuit": "stage 4 (circuit)", "architecture": "stage 2 (architecture)",
          "layout": "stage 7 (layout)"}


# ------------------------------------------------------------------ attribution and persistence

def _pads(board: dict):
    for ref, fp in board["footprints"].items():
        for p in fp["pads"]:
            yield ref, p


def failing_parts(drc: list[dict], board: dict) -> dict[str, list[str]]:
    """ref -> failure kinds (one entry per DRC error or unrouted item that touches it)."""
    out: dict[str, list[str]] = {}
    pads = list(_pads(board))
    for x in drc:
        if x.get("sev") != "error":
            continue
        refs = {m.group(3) for it in x.get("items") or [] for m in [PAD_RE.search(str(it))] if m}
        if not refs:
            for pos in x.get("at") or []:
                if pos:
                    refs |= {r for r, p in pads if math.hypot(p["x"] - pos[0], p["y"] - pos[1]) <= ATTRIB_MM}
        for r in refs:
            if r in board["footprints"]:
                out.setdefault(r, []).append(x.get("type") or "error")
    return out


def persistent(per_attempt: list[dict[str, list[str]]]) -> list[str]:
    """Parts that fail in at least PERSIST_SHARE of the attempts (at least 2 attempts)."""
    n = len(per_attempt)
    if n < 2:
        return []
    count: dict[str, int] = {}
    for a in per_attempt:
        for r in a:
            count[r] = count.get(r, 0) + 1
    return sorted((r for r, k in count.items() if k >= math.ceil(PERSIST_SHARE * n)), key=lambda r: (-count[r], r))


# ------------------------------------------------------------------ physics at a part

def escape(fp: dict, classes: dict, clearance: float, default_w: float) -> list[dict]:
    """Pads of footprint `fp` whose net class is wider than the widest straight escape. A pad inside a row (foreign
    pads on both sides along the row) leaves across the row: the track is limited by the nearest foreign pad on
    either side, 2 x (distance to that pad's edge along the row - clearance). A pad at the end of a row can leave
    along the row and is not limited."""
    pads = [p for p in fp["pads"] if p.get("net") and p.get("kind") != "np_thru_hole"]
    out = []
    for p in pads:
        others = [q for q in pads if q is not p]
        if not others:
            continue
        near = min(others, key=lambda q: math.hypot(q["x"] - p["x"], q["y"] - p["y"]))
        ax = abs(near["x"] - p["x"]) >= abs(near["y"] - p["y"])  # the row runs along x

        def along(q):
            return (q["x"] - p["x"]) if ax else (q["y"] - p["y"])

        row = [q for q in others if (abs(q["y"] - p["y"]) if ax else abs(q["x"] - p["x"])) < (p["h"] if ax else p["w"]) / 2]
        foreign = [q for q in row if q["net"] != p["net"]]
        if not (any(along(q) > 0 for q in foreign) and any(along(q) < 0 for q in foreign)):
            continue
        gap = min(abs(along(q)) - (q["w"] if ax else q["h"]) / 2 for q in foreign)
        w_esc = max(2 * (gap - clearance), 0.0)
        w = float(classes.get(p["net"], {}).get("width", default_w))
        if w > w_esc + 1e-6:
            pitch = min(abs(along(q)) for q in row)
            half = (p["w"] if ax else p["h"]) / 2
            out.append({"pad": p["number"], "net": p["net"], "width": round(w, 3), "escape": round(w_esc, 3),
                        "pitch": round(pitch, 3),
                        "pitch_needed": round(w / 2 + clearance + half, 2)})  # this pad's width, the same clearance
    return out


def _side(design, ref) -> str:
    c = design.components.get(ref)
    return str(c.get("side", "front")) if c else "front"


def board_fill(design, parts: dict, W: float, H: float) -> dict[str, float]:
    out = {"front": 0.0, "back": 0.0}
    for r, p in parts.items():
        if r in design.components:
            x0, y0, x1, y1 = p.box()
            out[_side(design, r) if _side(design, r) in out else "front"] += (x1 - x0) * (y1 - y0)
    return {k: v / (W * H) if W and H else 0.0 for k, v in out.items()}


# ------------------------------------------------------------------ structure key and history

def structure_key(design) -> str:
    """What a layout cannot change: parts and footprints, the netlist, net widths and currents, board size and
    layers. Placement anchors, weights, router and DRC settings are left out."""
    comps = sorted((r, str(c.get("footprint")), sorted((str(k), str(v)) for k, v in (c.pins or {}).items()))
                   for r, c in design.components.items())
    nets = sorted((n, str(i.get("width_mm")), str(i.get("current_a"))) for n, i in (design.raw.get("nets") or {}).items())
    b = design.board
    board = [b.get("width_mm"), b.get("height_mm"), b.get("layers"), b.get("track_mm"), b.get("clearance_mm")]
    return hashlib.sha256(json.dumps([comps, nets, board], default=str).encode()).hexdigest()[:12]


def history(out: Path) -> list[dict]:
    f = out / "layout_history.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return []


def record(out: Path, entry: dict):
    h = history(out)[-19:] + [entry]
    (out / "layout_history.json").write_text(json.dumps(h, indent=1))


def blocked(design, out: Path) -> dict | None:
    """The diagnosis that blocks a new layout build of this structure, or None."""
    key = structure_key(design)
    for e in reversed(history(out)):
        if e.get("structure") != key:
            return None
        if e.get("blocked"):
            return e
        if e.get("ok") or e.get("override"):
            return None
    return None


# ------------------------------------------------------------------ the verdict

def diagnose(design, parts: dict, attempts: list[dict], board: dict, out: Path) -> dict:
    """`attempts`: [{"failing": failing_parts(...)}] of the build's attempts; `board`: pcb.read_board of the kept
    board; `parts`: placer parts at the kept placement. Returns the diagnosis and records it in the history."""
    from . import floorplan, pcb, routing
    key = structure_key(design)
    per = [a["failing"] for a in attempts]
    pers = persistent(per)
    classes = pcb.net_classes(design)
    clr = float(design.board["clearance_mm"])
    w0 = float(design.board["track_mm"])
    floor = float(design.board.get("min_track_mm", routing.NECK_FLOOR))
    rows = []
    for r in pers:
        fp = board["footprints"].get(r)
        if fp is None or r not in parts:
            continue
        esc = escape(fp, classes, clr, w0)
        rows.append({"ref": r, "footprint": fp.get("footprint"), "attempts": sum(r in a for a in per),
                     "kinds": sorted(set(sum((a.get(r, []) for a in per), []))),
                     "escape": esc, "dead_pads": [e for e in esc if e["escape"] < floor]})
    W, H = design.board["width_mm"], design.board["height_mm"]
    fill = board_fill(design, parts, W, H)
    reasons, back = [], None
    if max(fill.values()) > floorplan.FILL_MAX:
        back = "architecture"
        reasons.append(f"board fill {max(fill.values()):.0%} of a face (limit {floorplan.FILL_MAX:.0%}): "
                       "the parts do not fit this board")
    for row in rows:
        if row["dead_pads"]:
            back = back or "parts"
            e = row["dead_pads"][0]
            reasons.append(f"{row['ref']} ({row['footprint']}): pad {e['pad']} ({e['net']}) can be left only by a track "
                           f"of {e['escape']} mm, under the board minimum {floor} mm: a package with pitch "
                           f">= {e['pitch_needed']} mm, or another part")
        elif len(row["escape"]) >= ESCAPE_PINS:
            back = back or "parts"
            nets = sorted({e["net"] for e in row["escape"]})
            worst = max(row["escape"], key=lambda e: e["width"] - e["escape"])
            reasons.append(f"{row['ref']} ({row['footprint']}, pitch {worst['pitch']} mm): {len(row['escape'])} pads on "
                           f"{', '.join(nets)} need tracks up to {worst['width']} mm where {worst['escape']} mm "
                           f"escapes; every one is a neck in a row of fine-pitch pads. Options: a package with pitch "
                           f">= {worst['pitch_needed']} mm, or narrower nets (circuit: width/current of "
                           f"{', '.join(nets)})")
    plain = [r["ref"] for r in rows if not r["escape"]]
    if plain and back is not None:
        reasons.append(f"Also failing in most attempts, no physical limit found: {', '.join(plain)}.")
    prev = [e for e in history(out) if e.get("structure") == key and not e.get("ok")]
    repeat = bool(prev and pers and set(pers) & set(prev[-1].get("persistent") or []))
    if pers and back is None:
        if repeat:
            back = "architecture"
            reasons.append(f"the same parts ({', '.join(sorted(set(pers) & set(prev[-1]['persistent'])))}) failed in "
                           f"the previous layout build of this design too: placement and routing have been tried; "
                           "change the parts around them, the board size or the layers")
        else:
            back = "layout"
            reasons.append(f"failures stay on {', '.join(pers)} in {len(per)} attempts but no physical limit was "
                           "found there: one placement change around them (a fixed cluster) is worth one more build")
    if not pers and any(per):
        back = back or "layout"
        reasons.append("the failures move between parts from attempt to attempt: more attempts or seeds can help"
                       if len(per) > 1 else "one attempt only: no evidence yet whether the failures stay on the same parts")
    res = {"structure": key, "attempts": len(per), "persistent": pers, "parts": rows,
           "fill": {k: round(v, 2) for k, v in fill.items()}, "back_to": back, "stage": STAGES.get(back),
           "reasons": reasons, "repeat": repeat,
           "blocked": back not in (None, "layout") or repeat}
    return res


def summary(res: dict) -> str:
    if not res or not res.get("back_to"):
        return ""
    head = ("BLOCKED: more layout attempts cannot fix this; go back to " if res["blocked"] else "next: ") + res["stage"]
    return head + ". " + " ".join(res["reasons"])
